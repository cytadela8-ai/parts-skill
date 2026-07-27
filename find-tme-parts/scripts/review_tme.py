"""Fetch and normalize TME records for the local BOM review interface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from urllib.parse import quote

from tme_parts import TmeError, order_qty, product_records, unit_price


def fetch_product(symbol: str, quantity: int, token: str) -> dict[str, object]:
    """Return a presentation-ready product record with its price at `quantity`."""
    cleaned_symbol = symbol.strip()
    if not cleaned_symbol:
        raise TmeError("A replacement TME symbol is required.")
    records = product_records(cleaned_symbol, token)
    product = records["product"]
    if not isinstance(product, Mapping):
        raise TmeError(f"TME returned an invalid product record for '{cleaned_symbol}'.")
    ordered_quantity = order_qty(product, quantity)
    data = records["data"]
    if not isinstance(data, Mapping):
        raise TmeError(f"TME returned no stock and price data for '{cleaned_symbol}'.")
    price = unit_price(data, ordered_quantity)
    if price is None:
        raise TmeError(f"TME returned no PLN price for '{cleaned_symbol}'.")
    tme_symbol = str(product.get("symbol", cleaned_symbol))
    raw_parameters = records["parameters"]
    parameters = (
        [cast(Mapping[str, object], item) for item in raw_parameters if isinstance(item, Mapping)]
        if isinstance(raw_parameters, list)
        else []
    )
    return {
        "symbol": tme_symbol,
        "description": str(product.get("description", "")),
        "manufacturer_part_number": "; ".join(
            str(value) for value in product.get("manufacturer_symbols", [])
        ),
        "product_url": f"https://www.tme.eu/pl/details/{quote(tme_symbol.lower(), safe='')}/",
        "photo_url": photo_url(product),
        "parameters": normalize_parameters(parameters),
        "unit_price_pln": f"{price:.6f}",
        "line_total_pln": f"{price * ordered_quantity:.6f}",
        "order_qty": str(ordered_quantity),
        "excess_qty": str(ordered_quantity - quantity),
        "stock_qty": str(int(data.get("stock_quantity") or 0)),
    }


def normalize_parameters(parameters: list[Mapping[str, object]]) -> list[dict[str, str]]:
    """Convert TME's nested parameter values into simple display rows."""
    normalized: list[dict[str, str]] = []
    for parameter in parameters:
        values = parameter.get("values", [])
        if not isinstance(values, list):
            continue
        rendered_values: list[str] = []
        for raw_value in values:
            if not isinstance(raw_value, Mapping):
                continue
            value = cast(Mapping[str, object], raw_value).get("value")
            if value:
                rendered_values.append(str(value))
        normalized.append(
            {
                "name": str(parameter.get("name", "")),
                "value": ", ".join(rendered_values),
            }
        )
    return normalized


def photo_url(product: Mapping[str, object]) -> str:
    """Return TME's primary product photo without requesting its product page."""
    assets = product.get("assets")
    if not isinstance(assets, Mapping):
        return ""
    primary_photo = cast(Mapping[str, object], assets).get("primary_photo")
    if not isinstance(primary_photo, Mapping):
        return ""
    photo = cast(Mapping[str, object], primary_photo)
    source = photo.get("prime") or photo.get("high_resolution")
    if not isinstance(source, str):
        return ""
    if source.startswith("//"):
        return f"https:{source}"
    if source.startswith(("https://", "http://")):
        return source
    return ""
