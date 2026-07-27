"""Behavior tests for KiCad-to-TME footprint translations."""

import csv
import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).parents[1] / "find-tme-parts" / "scripts" / "tme_parts.py"
SPEC = importlib.util.spec_from_file_location("tme_parts", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT_PATH}")
tme_parts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tme_parts)


class FootprintOptionsTests(unittest.TestCase):
    """Verify alternatives and geometry constraints in footprint translations."""

    def test_loads_alternative_diameter_options_without_height_constraints(
        self,
    ) -> None:
        """Treat permitted capacitor diameters as OR-ed options and ignore height."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "footprint-translations.csv"
            path.write_text(
                "kicad_footprint,option,match_type,tme_parameter,tme_value,notes\n"
                "Capacitor_SMD:CP_Elec_5x4.5,small,exact,Mounting,SMD,\n"
                "Capacitor_SMD:CP_Elec_5x4.5,small,numeric_mm,Diameter,5,\n"
                "Capacitor_SMD:CP_Elec_5x4.5,large,exact,Mounting,SMD,\n"
                "Capacitor_SMD:CP_Elec_5x4.5,large,numeric_mm,Diameter,6.3,\n",
                encoding="utf-8",
            )

            options = tme_parts.load_footprint_options(
                path, "Capacitor_SMD:CP_Elec_5x4.5"
            )

        self.assertEqual(len(options), 2)
        self.assertEqual(
            options["small"],
            [("exact", "Mounting", "SMD"), ("numeric_mm", "Diameter", "5")],
        )
        self.assertNotIn("Height", str(options))

    def test_treats_an_empty_mapping_file_as_a_new_mapping_store(self) -> None:
        """Allow map-footprint to initialize a caller-provided empty destination."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "footprint-translations.csv"
            path.touch()

            options = tme_parts.load_footprint_options(path, "Any:Footprint")

        self.assertEqual(options, {})

    def test_writes_a_header_when_appending_to_an_empty_mapping_file(self) -> None:
        """Keep an initialized mapping destination readable on the next run."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "footprint-translations.csv"
            path.touch()

            tme_parts.append_footprint_mapping(
                path,
                "Test:Footprint",
                [("exact", "Mounting", "SMD")],
                "default",
                "test mapping",
            )

            header = path.read_text(encoding="utf-8").splitlines()[0]

        self.assertEqual(
            header,
            "kicad_footprint,option,match_type,tme_parameter,tme_value,notes",
        )


class CsvEnrichmentTests(unittest.TestCase):
    """Verify CSV enrichment reports completed work while later lookups run."""

    def test_flushes_completed_rows_while_a_later_search_is_running(self) -> None:
        """Make partial results visible instead of leaving a zero-byte output file."""
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            input_path = directory_path / "input.csv"
            output_path = directory_path / "output.csv"
            translation_path = directory_path / "translations.csv"
            input_path.write_text(
                "Reference,Value,Footprint,Qty\n"
                "R1,unmapped,Missing:Footprint,1\n"
                "R2,10k,Test:Footprint,1\n",
                encoding="utf-8",
            )
            translation_path.write_text(
                "kicad_footprint,option,match_type,tme_parameter,tme_value,notes\n"
                "Test:Footprint,default,exact,Mounting,SMD,\n",
                encoding="utf-8",
            )
            search_started = threading.Event()
            allow_search_to_finish = threading.Event()

            def delayed_search(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
                search_started.set()
                allow_search_to_finish.wait(timeout=2)
                return []

            with mock.patch.object(tme_parts, "search", side_effect=delayed_search):
                worker = threading.Thread(
                    target=tme_parts.enrich,
                    args=(input_path, output_path, "token", translation_path),
                )
                worker.start()
                self.assertTrue(search_started.wait(timeout=1))
                partial_output = output_path.read_text(encoding="utf-8")
                allow_search_to_finish.set()
                worker.join(timeout=1)

        self.assertIn("R1,unmapped,Missing:Footprint,1", partial_output)


class ManualTmeSymbolTests(unittest.TestCase):
    """Verify manually supplied TME symbols bypass enrichment."""

    def test_passes_through_tme_symbol_without_searching(self) -> None:
        """Keep a manual display-name symbol without treating it as a candidate."""
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            input_path = directory_path / "input.csv"
            output_path = directory_path / "output.csv"
            mapping_path = directory_path / "footprint-translations.csv"
            input_path.write_text(
                "Reference,Value,Footprint,Qty,TME Symbol\n"
                "R1,10k,Resistor_SMD:R_0402_1005Metric,1,MANUAL-0402\n",
                encoding="utf-8",
            )
            mapping_path.write_text(
                "kicad_footprint,option,match_type,tme_parameter,tme_value,notes\n"
                "Resistor_SMD:R_0402_1005Metric,default,exact,Mounting,SMD,test\n",
                encoding="utf-8",
            )

            with mock.patch.object(tme_parts, "search", return_value=[]) as search:
                tme_parts.enrich(input_path, output_path, "unused", mapping_path)

            with output_path.open(encoding="utf-8", newline="") as file:
                result = next(csv.DictReader(file))

        self.assertEqual(result["TME Symbol"], "MANUAL-0402")
        self.assertEqual(result["TME Match Status"], "manually_provided")
        self.assertIn("not reviewed", result["TME Match Notes"].lower())
        search.assert_not_called()

    def test_passes_through_tme_symbol_identifier_without_searching(self) -> None:
        """Accept the machine-style manual-symbol header as an override."""
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            input_path = directory_path / "input.csv"
            output_path = directory_path / "output.csv"
            mapping_path = directory_path / "footprint-translations.csv"
            input_path.write_text(
                "Reference,Value,Footprint,Qty,TME_SYMBOL\n"
                "R1,10k,Resistor_SMD:R_0402_1005Metric,1,MANUAL-0402\n",
                encoding="utf-8",
            )
            mapping_path.write_text(
                "kicad_footprint,option,match_type,tme_parameter,tme_value,notes\n"
                "Resistor_SMD:R_0402_1005Metric,default,exact,Mounting,SMD,test\n",
                encoding="utf-8",
            )

            with mock.patch.object(tme_parts, "search", return_value=[]) as search:
                tme_parts.enrich(input_path, output_path, "unused", mapping_path)

            with output_path.open(encoding="utf-8", newline="") as file:
                result = next(csv.DictReader(file))

        self.assertEqual(result["TME Symbol"], "MANUAL-0402")
        self.assertEqual(result["TME Match Status"], "manually_provided")
        self.assertIn("not reviewed", result["TME Match Notes"].lower())
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
