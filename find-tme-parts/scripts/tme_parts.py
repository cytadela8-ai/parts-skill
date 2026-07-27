#!/usr/bin/env python3
"""Search TME's API for in-stock Polish-market component candidates."""

from __future__ import annotations
import argparse
import base64
import csv
import json
import math
import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = "https://api.tme.eu"
TRANSLATION_FILE = Path(__file__).parents[1] / "footprint-translations.csv"
REQUIRED = ("Reference", "Value", "Footprint", "Qty")
TRANSLATION_COLUMNS = (
    "kicad_footprint",
    "option",
    "match_type",
    "tme_parameter",
    "tme_value",
    "notes",
)
ADDED = (
    "TME Symbol",
    "Manufacturer Part Number",
    "TME URL",
    "Unit Price PLN",
    "Line Total PLN",
    "Order Qty",
    "Excess Qty",
    "Stock Qty",
    "TME Match Status",
    "TME Match Notes",
)


class TmeError(RuntimeError):
    """Describe an actionable input or TME API error."""


def positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search one component.")
    search.add_argument("--phrase", required=True)
    search.add_argument("--footprint", required=True)
    search.add_argument("--qty", required=True, type=positive)
    search.add_argument("--limit", default=100, type=int)
    search.add_argument("--env-file", default=Path(".env"), type=Path)
    discover = commands.add_parser(
        "discover-footprint", help="List TME package-related parameter terms."
    )
    discover.add_argument("--phrase", required=True)
    discover.add_argument("--env-file", default=Path(".env"), type=Path)
    mapping = commands.add_parser(
        "map-footprint", help="Verify and store a KiCad-to-TME footprint mapping."
    )
    mapping.add_argument("--kicad-footprint", required=True)
    mapping.add_argument("--phrase", required=True)
    mapping.add_argument("--constraint", action="append", required=True)
    mapping.add_argument("--numeric-constraint", action="append")
    mapping.add_argument("--option", default="default")
    mapping.add_argument("--notes", default="")
    mapping.add_argument("--translation-file", default=TRANSLATION_FILE, type=Path)
    mapping.add_argument("--env-file", default=Path(".env"), type=Path)
    batch = commands.add_parser("csv", help="Validate and enrich a BOM CSV.")
    batch.add_argument("input", type=Path)
    batch.add_argument("--output", required=True, type=Path)
    batch.add_argument("--translation-file", default=TRANSLATION_FILE, type=Path)
    batch.add_argument("--env-file", default=Path(".env"), type=Path)
    return parser.parse_args()


def credentials(env_file: Path) -> tuple[str, str]:
    values = dict(os.environ)
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values.setdefault(name.strip(), value.strip().strip("'\\\""))
    token, secret = values.get("APP_TOKEN"), values.get("APP_SECRET")
    if not token or not secret:
        raise TmeError("Missing APP_TOKEN or APP_SECRET in .env or the environment.")
    return token, secret


def request(url: str, headers: Mapping[str, str], data: bytes | None = None) -> dict[str, Any]:
    req = Request(url, data=data, headers=dict(headers), method="POST" if data else "GET")
    try:
        with urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise TmeError(
            f"TME API returned HTTP {error.code}: {error.read().decode('utf-8', errors='replace')[:400]}"
        ) from error
    except URLError as error:
        raise TmeError(f"Could not reach TME API: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise TmeError("TME API returned invalid JSON.") from error
    if result.get("status") not in (None, "OK"):
        raise TmeError(f"TME API error: {result}")
    return result


def bearer(token: str, secret: str) -> str:
    basic = base64.b64encode(f"{token}:{secret}".encode()).decode()
    result = request(
        f"{API}/auth/token",
        {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        urlencode({"grant_type": "client_credentials"}).encode(),
    )
    value = result.get("access_token")
    if not isinstance(value, str) or not value:
        raise TmeError("TME authentication returned no access token.")
    return value


def get(path: str, token: str, pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    return request(
        f"{API}{path}?{urlencode(list(pairs), doseq=True)}",
        {"Authorization": f"Bearer {token}"},
    )


def nested(value: Any, *path: str) -> list[dict[str, Any]]:
    for key in path:
        if not isinstance(value, Mapping):
            return []
        value = value.get(key)
    return value if isinstance(value, list) else []


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def is_searchable_phrase(value: str) -> bool:
    """Require a meaningful TME query rather than a placeholder BOM value."""
    return len(value.strip()) >= 2


def load_footprint_options(
    translation_file: Path, kicad_footprint: str
) -> dict[str, list[tuple[str, str, str]]]:
    """Load OR-ed footprint options, each containing AND-ed constraints."""
    if not translation_file.exists():
        return {}
    if translation_file.stat().st_size == 0:
        return {}
    try:
        with translation_file.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            headers = [str(header) for header in reader.fieldnames or []]
            if tuple(headers) != TRANSLATION_COLUMNS:
                raise TmeError(
                    "Footprint translation CSV must have columns: "
                    f"{', '.join(TRANSLATION_COLUMNS)}."
                )
            options: dict[str, list[tuple[str, str, str]]] = {}
            for row in reader:
                if row["kicad_footprint"] != kicad_footprint:
                    continue
                option = row["option"] or "default"
                constraint = (
                    row["match_type"],
                    row["tme_parameter"],
                    row["tme_value"],
                )
                options.setdefault(option, []).append(constraint)
            return options
    except OSError as error:
        raise TmeError(f"Could not read footprint translation CSV: {error}") from error


def constraint_matches(parameter: Mapping[str, Any], match_type: str, expected: str) -> bool:
    """Match an exact term or a millimetre-valued TME parameter."""
    for value in nested(parameter, "values"):
        actual = str(value.get("value", ""))
        if match_type == "exact" and norm(actual) == norm(expected):
            return True
        if match_type == "numeric_mm":
            number = re.search(r"\d+(?:[.,]\d+)?", actual)
            if number and abs(float(number.group().replace(",", ".")) - float(expected)) < 0.01:
                return True
    return False


def has_footprint_option(
    parameters: list[dict[str, Any]], option: list[tuple[str, str, str]]
) -> bool:
    """Confirm every constraint in one footprint option."""
    for match_type, expected_name, expected_value in option:
        parameter = next(
            (item for item in parameters if norm(str(item.get("name", ""))) == norm(expected_name)),
            None,
        )
        if parameter is None or not constraint_matches(parameter, match_type, expected_value):
            return False
    return True


def has_footprint_options(
    parameters: list[dict[str, Any]], options: dict[str, list[tuple[str, str, str]]]
) -> bool:
    """Accept a product that fulfils any documented footprint option."""
    return any(has_footprint_option(parameters, option) for option in options.values())


def search_facets(phrase: str, token: str) -> list[dict[str, Any]]:
    """Return TME parameter facets for a focused discovery search."""
    if not is_searchable_phrase(phrase) or len(phrase) > 40:
        raise TmeError("Search phrase must have 2 to 40 characters.")
    data = get(
        "/products/search",
        token,
        [
            ("country", "PL"),
            ("scope[]", "parameters"),
            ("phrase", phrase),
            ("limit", "100"),
        ],
    ).get("data", {})
    return nested(data, "parameters", "elements")


def constraint_filters(
    facets: list[dict[str, Any]], constraints: list[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Resolve exact parameter/value labels to TME search filters."""
    filters: list[tuple[str, str]] = []
    unresolved: list[tuple[str, str]] = []
    for index, (parameter, expected) in enumerate(constraints):
        matched = False
        for facet in facets:
            if norm(str(facet.get("name", ""))) != norm(parameter):
                continue
            for value in nested(facet, "values"):
                if norm(str(value.get("value", ""))) != norm(expected):
                    continue
                filters.extend(
                    [
                        (f"parameters[{index}][id]", str(facet["id"])),
                        (f"parameters[{index}][values][]", str(value["id"])),
                    ]
                )
                matched = True
                break
            if matched:
                break
        if not matched:
            unresolved.append((parameter, expected))
    return filters, unresolved


def package_filter(facets: list[dict[str, Any]], footprint: str) -> list[tuple[str, str]]:
    expected, names = (
        norm(footprint),
        ("case", "package", "footprint", "housing", "mounting"),
    )
    for facet in facets:
        if not any(word in str(facet.get("name", "")).lower() for word in names):
            continue
        for item in nested(facet, "values"):
            if norm(str(item.get("value", ""))) == expected:
                return [
                    ("parameters[0][id]", str(facet["id"])),
                    ("parameters[0][values][]", str(item["id"])),
                ]
    return []


def common_exact_constraints(
    options: dict[str, list[tuple[str, str, str]]],
) -> list[tuple[str, str]]:
    """Return exact constraints shared by every allowed footprint option."""
    exact_sets = [
        {(parameter, value) for match_type, parameter, value in option if match_type == "exact"}
        for option in options.values()
    ]
    if not exact_sets:
        return []
    return sorted(set.intersection(*exact_sets))


def products(
    phrase: str,
    footprint: str,
    token: str,
    limit: int,
    options: dict[str, list[tuple[str, str, str]]] | None = None,
) -> list[dict[str, Any]]:
    if not is_searchable_phrase(phrase) or len(phrase) > 40:
        raise TmeError("Search phrase must have 2 to 40 characters.")
    if not 1 <= limit <= 100:
        raise TmeError("Search limit must be from 1 to 100.")
    base = [
        ("country", "PL"),
        ("scope[]", "products"),
        ("scope[]", "parameters"),
        ("phrase", phrase),
        ("limit", str(limit)),
    ]
    data = get("/products/search", token, base).get("data", {})
    facets = nested(data, "parameters", "elements")
    if options:
        constraints = common_exact_constraints(options)
        filters, unresolved = constraint_filters(facets, constraints)
        if unresolved:
            return []
    else:
        filters = package_filter(facets, footprint)
    if filters:
        data = get("/products/search", token, [*base, *filters]).get("data", {})
    return nested(data, "products", "elements")


def parse_constraint(value: str) -> tuple[str, str]:
    """Parse one PARAMETER=VALUE mapping constraint."""
    if "=" not in value:
        raise TmeError("Constraints must use PARAMETER=VALUE syntax.")
    parameter, expected = (part.strip() for part in value.split("=", 1))
    if not parameter or not expected:
        raise TmeError("Constraints require both a parameter and a value.")
    return parameter, expected


def append_footprint_mapping(
    translation_file: Path,
    kicad_footprint: str,
    constraints: list[tuple[str, str, str]],
    option: str,
    notes: str,
) -> None:
    """Append verified, non-duplicate KiCad-to-TME constraints."""
    existing = load_footprint_options(translation_file, kicad_footprint).get(option, [])
    new_constraints = [constraint for constraint in constraints if constraint not in existing]
    if not new_constraints:
        return
    translation_file.parent.mkdir(parents=True, exist_ok=True)
    write_header = not translation_file.exists() or translation_file.stat().st_size == 0
    with translation_file.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=TRANSLATION_COLUMNS)
        if write_header:
            writer.writeheader()
        for match_type, parameter, value in new_constraints:
            writer.writerow(
                {
                    "kicad_footprint": kicad_footprint,
                    "option": option,
                    "match_type": match_type,
                    "tme_parameter": parameter,
                    "tme_value": value,
                    "notes": notes,
                }
            )


def batches(items: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(items), 50):
        yield items[start : start + 50]


def details(
    symbols: list[str], token: str, endpoint: str, pairs: list[tuple[str, str]]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for batch in batches(symbols):
        for item in nested(
            get(
                endpoint,
                token,
                [("country", "PL"), *pairs, *[("symbols[]", s) for s in batch]],
            ),
            "data",
            "elements",
        ):
            result[str(item.get("symbol", ""))] = item
    return result


def order_qty(product: Mapping[str, Any], requested: int) -> int:
    minimum, multiple = (
        int(product.get("minimal_amount") or 1),
        int(product.get("multiples") or 1),
    )
    return math.ceil(max(requested, minimum) / multiple) * multiple


def unit_price(data: Mapping[str, Any], quantity: int) -> float | None:
    tiers = [
        tier
        for tier in nested(data, "prices", "elements")
        if int(tier.get("amount", 0)) <= quantity
    ]
    if not tiers:
        return None
    value = max(tiers, key=lambda tier: int(tier["amount"])).get("price")
    return float(value) if isinstance(value, int | float) else None


def params(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    return nested(item, "parameters", "elements")


def has_footprint(parameters: list[dict[str, Any]], footprint: str) -> bool:
    expected, names = (
        norm(footprint),
        ("case", "package", "footprint", "housing", "mounting"),
    )
    for parameter in parameters:
        if any(word in str(parameter.get("name", "")).lower() for word in names):
            if any(
                norm(str(value.get("value", ""))) == expected
                for value in nested(parameter, "values")
            ):
                return True
    return False


def summary(parameters: list[dict[str, Any]]) -> str:
    return "; ".join(
        part
        for parameter in parameters
        for part in [
            str(parameter.get("name", "")),
            *(str(value.get("value", "")) for value in nested(parameter, "values")),
        ]
        if part
    )


def select_preferred_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the largest order whose payable total is within 25% of the lowest."""
    if not candidates:
        raise TmeError("Cannot select a preferred candidate from an empty list.")
    for candidate in candidates:
        line_total = candidate["order_quantity"] * candidate["unit_price_pln"]
        candidate["line_total_pln"] = round(line_total, 6)
        requested = int(candidate.get("requested_quantity", candidate["order_quantity"]))
        candidate["excess_quantity"] = candidate["order_quantity"] - requested
        candidate["needs_attention"] = candidate["order_quantity"] >= 1000
        candidate["attention_note"] = (
            f"Order quantity {candidate['order_quantity']:,} is excessive; "
            "confirm that the surplus is wanted."
            if candidate["needs_attention"]
            else ""
        )
    cheapest_total = min(candidate["line_total_pln"] for candidate in candidates)
    affordable_extra_stock = [
        candidate
        for candidate in candidates
        if candidate["line_total_pln"] <= cheapest_total * 1.25
    ]
    return min(
        affordable_extra_stock,
        key=lambda candidate: (
            -candidate["order_quantity"],
            candidate["line_total_pln"],
            candidate["symbol"],
        ),
    )


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place the preferred exact-footprint candidate first in the result list."""
    matching = [candidate for candidate in candidates if candidate["footprint_match"]]
    nonmatching = [candidate for candidate in candidates if not candidate["footprint_match"]]
    ranked: list[dict[str, Any]] = []
    for group in (matching, nonmatching):
        if not group:
            continue
        preferred = select_preferred_candidate(group)
        others = [candidate for candidate in group if candidate is not preferred]
        others.sort(key=lambda candidate: (candidate["line_total_pln"], candidate["symbol"]))
        ranked.extend([preferred, *others])
    return ranked


def search(
    phrase: str,
    footprint: str,
    quantity: int,
    token: str,
    limit: int = 100,
    options: dict[str, list[tuple[str, str, str]]] | None = None,
) -> list[dict[str, Any]]:
    found = products(phrase, footprint, token, limit, options)
    symbols = [str(product["symbol"]) for product in found if product.get("symbol")]
    parameter_data = details(symbols, token, "/products/parameters", [])
    price_data = details(
        symbols,
        token,
        "/products/data",
        [("currency", "PLN"), ("scope[]", "stock"), ("scope[]", "prices")],
    )
    candidates = []
    for product in found:
        symbol, product_params = (
            str(product.get("symbol", "")),
            params(parameter_data.get(str(product.get("symbol", "")), {})),
        )
        data, order = price_data.get(symbol, {}), order_qty(product, quantity)
        price, stock = unit_price(data, order), int(data.get("stock_quantity") or 0)
        if stock >= order and price is not None:
            candidates.append(
                {
                    "symbol": symbol,
                    "manufacturer": (product.get("manufacturer") or {}).get("name"),
                    "manufacturer_part_numbers": product.get("manufacturer_symbols", []),
                    "description": product.get("description", ""),
                    "product_url": f"https://www.tme.eu/pl/details/{quote(symbol.lower(), safe='')}/",
                    "requested_quantity": quantity,
                    "order_quantity": order,
                    "minimum_order_quantity": int(product.get("minimal_amount") or 1),
                    "order_multiple": int(product.get("multiples") or 1),
                    "stock_quantity": stock,
                    "unit_price_pln": price,
                    "footprint_match": (
                        has_footprint_options(product_params, options)
                        if options
                        else has_footprint(product_params, footprint)
                    ),
                    "parameters": product_params,
                    "parameter_summary": summary(product_params),
                }
            )
    return rank_candidates(candidates)


def bom(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            headers: list[str] = [str(header) for header in reader.fieldnames or []]
            missing = [column for column in REQUIRED if column not in headers]
            if missing:
                raise TmeError(f"CSV is missing required column(s): {', '.join(missing)}.")
            rows: list[dict[str, str]] = []
            for raw_row in reader:
                if None in raw_row:
                    raise TmeError(f"CSV {path} has more values than its header.")
                rows.append({str(key): str(value or "") for key, value in raw_row.items()})
    except OSError as error:
        raise TmeError(f"Could not read {path}: {error}") from error
    if not rows:
        raise TmeError("CSV has no data rows.")
    for row_number, row in enumerate(rows, start=2):
        try:
            positive(row["Qty"])
        except argparse.ArgumentTypeError as error:
            raise TmeError(f"CSV row {row_number} has invalid Qty: {error}.") from error
        if not row["Value"].strip() or not row["Footprint"].strip():
            raise TmeError(f"CSV row {row_number} needs non-empty Value and Footprint.")
    return rows, headers


def enrich(input_path: Path, output_path: Path, token: str, translation_file: Path) -> None:
    rows, headers = bom(input_path)
    fieldnames = [*headers, *[name for name in ADDED if name not in headers]]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="", buffering=1) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            manual_symbol = row.get("TME_SYMBOL", "").strip() or row.get("TME Symbol", "").strip()
            if manual_symbol:
                writer.writerow(
                    {
                        **row,
                        "TME Symbol": manual_symbol,
                        "TME Match Status": "manually_provided",
                        "TME Match Notes": (
                            "TME symbol was provided manually and not reviewed by the helper."
                        ),
                    }
                )
                continue
            options = load_footprint_options(translation_file, row["Footprint"])
            if not options:
                writer.writerow(
                    {
                        **row,
                        "TME Match Status": "needs_review",
                        "TME Match Notes": (
                            "No verified KiCad-to-TME footprint mapping. "
                            "Use discover-footprint and map-footprint first."
                        ),
                    }
                )
                continue
            if not is_searchable_phrase(row["Value"]):
                writer.writerow(
                    {
                        **row,
                        "TME Match Status": "needs_review",
                        "TME Match Notes": (
                            "BOM value is unspecified or too short for a TME search."
                        ),
                    }
                )
                continue
            candidates = search(
                row["Value"],
                row["Footprint"],
                int(row["Qty"]),
                token,
                options=options,
            )
            if not candidates:
                writer.writerow(
                    {
                        **row,
                        "TME Match Status": "no_in_stock_match",
                        "TME Match Notes": "No priced in-stock candidate found.",
                    }
                )
                continue
            best, safe = candidates[0], candidates[0]["footprint_match"]
            status = "candidate_unreviewed"
            note = "Verify all stated requirements against returned parameters."
            if not safe:
                status = "needs_review"
                note = "No confirmed exact footprint; do not select without review."
            elif best["needs_attention"]:
                status = "needs_attention"
                note = f"{best['attention_note']} Verify all stated requirements."
            writer.writerow(
                {
                    **row,
                    "TME Symbol": best["symbol"],
                    "Manufacturer Part Number": "; ".join(best["manufacturer_part_numbers"]),
                    "TME URL": best["product_url"],
                    "Unit Price PLN": f"{best['unit_price_pln']:.6f}",
                    "Line Total PLN": f"{best['line_total_pln']:.6f}",
                    "Order Qty": best["order_quantity"],
                    "Excess Qty": best["excess_quantity"],
                    "Stock Qty": best["stock_quantity"],
                    "TME Match Status": status,
                    "TME Match Notes": note,
                }
            )


def main() -> int:
    args = arguments()
    try:
        token, secret = credentials(args.env_file)
        access = bearer(token, secret)
        if args.command == "search":
            print(
                json.dumps(
                    {
                        "candidates": search(
                            args.phrase, args.footprint, args.qty, access, args.limit
                        )
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "discover-footprint":
            facets = search_facets(args.phrase, access)
            package_names = (
                "case",
                "package",
                "footprint",
                "housing",
                "mounting",
                "diameter",
                "dimension",
                "pitch",
                "height",
                "number of pins",
            )
            relevant = [
                facet
                for facet in facets
                if any(word in str(facet.get("name", "")).lower() for word in package_names)
            ]
            discovered = products(args.phrase, "", access, 20)
            symbols = [str(product["symbol"]) for product in discovered]
            product_parameters = details(symbols, access, "/products/parameters", [])
            samples = []
            for symbol, item in product_parameters.items():
                parameters = [
                    parameter
                    for parameter in params(item)
                    if any(word in str(parameter.get("name", "")).lower() for word in package_names)
                ]
                samples.append({"symbol": symbol, "parameters": parameters})
            print(
                json.dumps(
                    {"parameters": relevant, "sample_products": samples},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "map-footprint":
            exact_constraints = [parse_constraint(value) for value in args.constraint]
            _, unresolved = constraint_filters(
                search_facets(args.phrase, access), exact_constraints
            )
            if unresolved:
                formatted = ", ".join(f"{parameter}={value}" for parameter, value in unresolved)
                raise TmeError(f"TME did not expose these exact constraints: {formatted}.")
            numeric_constraints = [
                parse_constraint(value) for value in args.numeric_constraint or []
            ]
            if numeric_constraints:
                discovered = products(args.phrase, "", access, 100)
                symbols = [str(product["symbol"]) for product in discovered]
                parameters = details(symbols, access, "/products/parameters", [])
                for parameter, expected in numeric_constraints:
                    if not any(
                        has_footprint_option(params(item), [("numeric_mm", parameter, expected)])
                        for item in parameters.values()
                    ):
                        raise TmeError(f"TME did not expose numeric {parameter}={expected}mm.")
            append_footprint_mapping(
                args.translation_file,
                args.kicad_footprint,
                [
                    *[("exact", parameter, value) for parameter, value in exact_constraints],
                    *[("numeric_mm", parameter, value) for parameter, value in numeric_constraints],
                ],
                args.option,
                args.notes,
            )
            print(f"Updated verified footprint mappings in {args.translation_file}")
        else:
            enrich(args.input, args.output, access, args.translation_file)
            print(f"Wrote validated TME results to {args.output}")
    except TmeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
