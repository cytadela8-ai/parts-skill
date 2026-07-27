"""Behavior tests for KiCad-to-TME footprint translations."""

import csv
import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
import sys
from types import ModuleType
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

            options = tme_parts.load_footprint_options(path, "Capacitor_SMD:CP_Elec_5x4.5")

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


class FinalCsvTests(unittest.TestCase):
    """Verify the review app's persistent final CSV state."""

    def test_initializes_final_counts_and_preserves_existing_edits(self) -> None:
        """Create final counts once without replacing later manual values."""
        review_state = load_script_module("review_state")
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            input_path = directory_path / "bom-results.csv"
            input_path.write_text(
                "Reference,Value,Footprint,Qty\n"
                "R1,10k,Resistor_SMD:R_0402_1005Metric,10\n"
                "R2,1k,Resistor_SMD:R_0402_1005Metric,100\n",
                encoding="utf-8",
            )

            final_path = review_state.initialize_final_csv(input_path)
            with final_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            rows[0]["Final Item Count"] = "99"
            with final_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            repeated_path = review_state.initialize_final_csv(input_path)
            with repeated_path.open(encoding="utf-8", newline="") as file:
                repeated_rows = list(csv.DictReader(file))

        self.assertEqual(final_path.name, "bom-results-final.csv")
        self.assertEqual(rows[1]["Final Item Count"], "115")
        self.assertEqual(repeated_rows[0]["Final Item Count"], "99")


class TmeReviewTests(unittest.TestCase):
    """Verify normalization of one live TME product for review."""

    def test_fetch_product_includes_summary_links_and_current_price(self) -> None:
        """Expose description, parameters, price, stock, and supplied datasheet link."""
        review_tme = load_script_module("review_tme")
        records = {
            "product": {
                "symbol": "ABC-1",
                "description": "Precision resistor",
                "manufacturer_symbols": ["MFG-1"],
                "minimal_amount": 1,
                "multiples": 1,
                "datasheet_url": "https://example.test/data.pdf",
            },
            "parameters": [
                {"name": "Resistance", "values": [{"value": "10kΩ"}]},
            ],
            "data": {
                "stock_quantity": 80,
                "prices": {"elements": [{"amount": 1, "price": 0.5}]},
            },
        }
        with mock.patch.object(review_tme, "product_records", return_value=records):
            product = review_tme.fetch_product("ABC-1", 12, "token")

        self.assertEqual(product["manufacturer_part_number"], "MFG-1")
        self.assertEqual(product["datasheet_url"], "https://example.test/data.pdf")
        self.assertEqual(product["line_total_pln"], "6.000000")
        self.assertEqual(product["parameters"][0]["value"], "10kΩ")


class ReviewAppTests(unittest.TestCase):
    """Verify review-app edits through its public HTTP interface."""

    def test_substitution_persists_tme_fields_after_a_valid_response(self) -> None:
        """Update a row only after the pasted replacement resolves at TME."""
        review_parts = load_script_module("review_parts")
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            final_path = directory_path / "bom-final.csv"
            final_path.write_text(
                "Reference,Value,Footprint,Qty,Final Item Count,TME Symbol,"
                "Manufacturer Part Number,TME URL,Unit Price PLN,Line Total PLN,"
                "Order Qty,Excess Qty,Stock Qty,TME Match Status,TME Match Notes\n"
                "R1,10k,Resistor_SMD:R_0402_1005Metric,10,12,OLD,,,,,,,,,\n",
                encoding="utf-8",
            )
            product = {
                "symbol": "NEW",
                "manufacturer_part_number": "MFG-NEW",
                "product_url": "https://www.tme.eu/pl/details/new/",
                "unit_price_pln": "0.500000",
                "line_total_pln": "6.000000",
                "order_qty": "12",
                "excess_qty": "0",
                "stock_qty": "80",
            }
            app = review_parts.create_app(final_path, lambda: "token")
            with mock.patch.object(review_parts, "fetch_product", return_value=product):
                response = app.test_client().post(
                    "/api/rows/0/substitution", json={"symbol": "NEW"}
                )
            with final_path.open(encoding="utf-8", newline="") as file:
                row = next(csv.DictReader(file))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(row["TME Symbol"], "NEW")
        self.assertEqual(row["TME Match Status"], "manually_substituted")

    def test_failed_substitution_leaves_final_csv_unchanged(self) -> None:
        """Reject a bad replacement without partially changing the user's selection."""
        review_parts = load_script_module("review_parts")
        with tempfile.TemporaryDirectory() as directory:
            final_path = Path(directory) / "bom-final.csv"
            final_path.write_text(
                "Reference,Value,Footprint,Qty,Final Item Count,TME Symbol\n"
                "R1,10k,Resistor_SMD:R_0402_1005Metric,10,12,OLD\n",
                encoding="utf-8",
            )
            app = review_parts.create_app(final_path, lambda: "token")
            with mock.patch.object(
                review_parts, "fetch_product", side_effect=review_parts.TmeError("not found")
            ):
                response = app.test_client().post(
                    "/api/rows/0/substitution", json={"symbol": "BAD"}
                )
            with final_path.open(encoding="utf-8", newline="") as file:
                row = next(csv.DictReader(file))

        self.assertEqual(response.status_code, 422)
        self.assertEqual(row["TME Symbol"], "OLD")

    def test_count_edit_persists_without_recalculating_it(self) -> None:
        """Save an explicit final count exactly as supplied by the user."""
        review_parts = load_script_module("review_parts")
        with tempfile.TemporaryDirectory() as directory:
            final_path = Path(directory) / "bom-final.csv"
            final_path.write_text(
                "Reference,Value,Footprint,Qty,Final Item Count\n"
                "R1,10k,Resistor_SMD:R_0402_1005Metric,10,12\n",
                encoding="utf-8",
            )
            app = review_parts.create_app(final_path, lambda: "token")
            response = app.test_client().patch("/api/rows/0/count", json={"count": 31})
            with final_path.open(encoding="utf-8", newline="") as file:
                row = next(csv.DictReader(file))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(row["Final Item Count"], "31")

    def test_review_page_serves_the_application_shell(self) -> None:
        """Render an accessible review screen with the essential controls."""
        review_parts = load_script_module("review_parts")
        with tempfile.TemporaryDirectory() as directory:
            final_path = Path(directory) / "bom-final.csv"
            final_path.write_text(
                "Reference,Value,Footprint,Qty,Final Item Count\n"
                "R1,10k,Resistor_SMD:R_0402_1005Metric,10,12\n",
                encoding="utf-8",
            )
            app = review_parts.create_app(final_path, lambda: "token")
            response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Final Item Count", response.data)
        self.assertIn(b"Part details", response.data)


def load_script_module(name: str) -> ModuleType:
    """Load a script module by name without installing the skill as a package."""
    path = SCRIPT_PATH.parent / f"{name}.py"
    script_directory = str(SCRIPT_PATH.parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
