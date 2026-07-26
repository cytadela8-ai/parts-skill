"""Behavior tests for KiCad-to-TME footprint translations."""

import importlib.util
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
