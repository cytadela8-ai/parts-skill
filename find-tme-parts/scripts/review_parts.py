#!/usr/bin/env python3
"""Run a local web app for reviewing an AI-processed TME BOM CSV."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from review_state import FINAL_COUNT, initialize_final_csv, load_rows, update_row
from review_tme import fetch_product
from tme_parts import TmeError, bearer, credentials, positive

SCRIPT_DIRECTORY = Path(__file__).parent


def create_app(final_csv: Path, token_supplier: Callable[[], str]) -> Flask:
    """Create the local CSV review application for an initialized final BOM."""
    app = Flask(
        __name__,
        template_folder=SCRIPT_DIRECTORY.parent / "templates",
        static_folder=SCRIPT_DIRECTORY.parent / "static",
    )

    @app.get("/")
    def index() -> str:
        return render_template("review.html", final_csv_name=final_csv.name)

    @app.get("/api/rows")
    def rows() -> Any:
        loaded_rows, _ = load_rows(final_csv)
        return jsonify({"rows": [{"index": index, **row} for index, row in enumerate(loaded_rows)]})

    @app.get("/api/rows/<int:index>/details")
    def detail(index: int) -> Any:
        row = get_row(final_csv, index)
        symbol = row.get("TME Symbol", "").strip()
        if not symbol:
            return error("This row has no selected TME symbol.", 404)
        try:
            return jsonify(fetch_product(symbol, final_count(row), token_supplier()))
        except TmeError as exception:
            return error(str(exception), 422)

    @app.patch("/api/rows/<int:index>/count")
    def count(index: int) -> Any:
        payload = json_object()
        if isinstance(payload, tuple):
            return payload
        try:
            value = str(positive(str(payload.get("count", ""))))
            existing = get_row(final_csv, index)
            updates = {FINAL_COUNT: value}
            total = final_line_total(existing.get("Unit Price PLN", ""), value)
            if total is not None:
                updates["Line Total PLN"] = total
            row = update_row(final_csv, index, updates)
        except TmeError as exception:
            return error(str(exception), 422)
        except argparse.ArgumentTypeError as exception:
            return error(f"Final Item Count {exception}.", 400)
        return jsonify({"row": {"index": index, **row}})

    @app.post("/api/rows/<int:index>/substitution")
    def substitution(index: int) -> Any:
        payload = json_object()
        if isinstance(payload, tuple):
            return payload
        symbol = str(payload.get("symbol", "")).strip()
        try:
            row = get_row(final_csv, index)
            product = fetch_product(symbol, final_count(row), token_supplier())
            updated = update_row(final_csv, index, replacement_fields(product, final_count(row)))
        except TmeError as exception:
            return error(str(exception), 422)
        return jsonify({"row": {"index": index, **updated}, "product": product})

    @app.post("/api/rows/<int:index>/approve")
    def approve(index: int) -> Any:
        """Persist a user's approval after their cross-check."""
        try:
            row = update_row(
                final_csv,
                index,
                {
                    "TME Match Status": "approved",
                    "TME Match Notes": "Approved in the review app after manual cross-check.",
                },
            )
        except TmeError as exception:
            return error(str(exception), 422)
        return jsonify({"row": {"index": index, **row}})

    return app


def get_row(path: Path, index: int) -> dict[str, str]:
    """Return one row or report an actionable missing-row error."""
    rows, _ = load_rows(path)
    if not 0 <= index < len(rows):
        raise TmeError(f"Row index {index} does not exist.")
    return rows[index]


def final_count(row: dict[str, str]) -> int:
    """Return the validated persisted count for a CSV row."""
    try:
        return positive(row[FINAL_COUNT])
    except (KeyError, argparse.ArgumentTypeError) as exception:
        raise TmeError("Final Item Count must be a positive integer.") from exception


def final_line_total(unit_price: str, quantity: str | int) -> str | None:
    """Calculate a review total from the user-approved item count."""
    if not unit_price.strip():
        return None
    try:
        total = Decimal(unit_price) * Decimal(str(quantity))
    except InvalidOperation as error:
        raise TmeError(f"Unit Price PLN '{unit_price}' is not a valid decimal.") from error
    return f"{total:.6f}"


def replacement_fields(product: dict[str, object], quantity: int) -> dict[str, str]:
    """Map normalized TME data to the persistent final CSV columns."""
    return {
        "TME Symbol": str(product["symbol"]),
        "Manufacturer Part Number": str(product["manufacturer_part_number"]),
        "TME URL": str(product["product_url"]),
        "Unit Price PLN": str(product["unit_price_pln"]),
        "Line Total PLN": final_line_total(str(product["unit_price_pln"]), quantity) or "",
        "Order Qty": str(product["order_qty"]),
        "Excess Qty": str(product["excess_qty"]),
        "Stock Qty": str(product["stock_qty"]),
        "TME Match Status": "manually_substituted",
        "TME Match Notes": "Replacement TME symbol selected in the review app.",
    }


def json_object() -> dict[str, object] | tuple[Any, int]:
    """Require a JSON object request body."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error("Request body must be a JSON object.", 400)
    return payload


def error(message: str, status: int) -> tuple[Any, int]:
    """Return one safe, user-facing API error payload."""
    return jsonify({"error": message}), status


def arguments() -> argparse.Namespace:
    """Parse the local review app's command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser.parse_args()


def main() -> int:
    """Initialize final CSV state and start the loopback-only review server."""
    args = arguments()
    try:
        output_path = initialize_final_csv(args.input_csv)
        token, secret = credentials(args.env_file)
        access_token = bearer(token, secret)
    except TmeError as exception:
        print(f"Error: {exception}")
        return 2
    app = create_app(output_path, lambda: access_token)
    print(f"Reviewing {output_path} at http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
