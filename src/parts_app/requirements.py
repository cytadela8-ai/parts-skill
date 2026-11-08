"""Deterministic passive/Zener value comparisons; unknowns remain explicit, not inferred."""

import re
from decimal import Decimal, InvalidOperation

PREFIXES = {
    "p": "1e-12",
    "n": "1e-9",
    "u": "1e-6",
    "µ": "1e-6",
    "m": "1e-3",
    "k": "1e3",
    "K": "1e3",
    "M": "1e6",
    "": "1",
    "R": "1",
}


def magnitude(text: str) -> Decimal | None:
    """Parse a leading decimal with SI prefix, preserving milli/mega distinction."""
    text = text.strip().replace(",", ".").replace("μ", "µ").replace("Ω", "ohm")
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([pnuµmkKMR]?)", text)
    if not match:
        return None
    number, prefix = match.groups()
    try:
        return Decimal(number) * Decimal(PREFIXES[prefix])
    except InvalidOperation:
        return None


def requested_value(fields: dict) -> tuple[str, Decimal] | None:
    """Find only explicitly numeric R/C/L or Zener-voltage requirements."""
    value = fields.get("Value", "").strip()
    if "zener" in value.lower():
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*V", value)
        return ("zener voltage", Decimal(match[1].replace(",", "."))) if match else None
    reference = fields.get("Reference", "").split(",")[0].strip()
    category = next(
        (
            name
            for prefix, name in [("R", "resistance"), ("C", "capacitance"), ("L", "inductance")]
            if re.fullmatch(prefix + r"\d+", reference)
        ),
        None,
    )
    if category is None:
        return None
    number = magnitude(value)
    return (category, number) if number is not None else None


def compare_value(fields: dict, product: dict) -> dict:
    """Return a measured value comparison without promoting absent data to a match."""
    request = requested_value(fields)
    if request is None:
        return {"outcome": "unknown", "reason": "No supported explicit numeric requirement."}
    category, expected = request
    for parameter in product.get("parameters", []):
        name = parameter.get("name", "").lower()
        if name == category or (
            category == "zener voltage" and "zener" in name and "voltage" in name
        ):
            values = parameter.get("values", [])
            actual = magnitude(str(values[0].get("value", ""))) if values else None
            if actual is not None:
                delta = abs(actual - expected) / max(abs(expected), Decimal("1e-18"))
                return {
                    "outcome": "pass" if actual == expected else "fail",
                    "requirement": category,
                    "expected": str(expected),
                    "actual": str(actual),
                    "relative_difference": float(delta),
                }
    return {"outcome": "unknown", "reason": f"TME does not expose {category}."}


def shortlist_candidates(fields: dict, candidates: list[dict]) -> list[dict]:
    """Prioritize exact values over cheap unrelated results before the model sees a shortlist."""
    annotated = []
    for product in candidates:
        check = compare_value(fields, product)
        annotated.append(product | {"numeric_requirement": check})
    return sorted(
        annotated,
        key=lambda product: (
            {"pass": 0, "unknown": 2, "fail": 3}[product["numeric_requirement"]["outcome"]],
            product["numeric_requirement"].get("relative_difference", 0),
            not product.get("footprint_match", False),
            product.get("line_total_pln", 0),
            product["symbol"],
        ),
    )


def footprint_conflicts(fields: dict, product: dict) -> list[str]:
    """Reject explicit mounting/chip-case conflicts, retaining candidates with missing data."""
    footprint = fields.get("Footprint", "")
    chip = re.search(r"(?:R|C|L)_(\d{4})_\d{4}Metric", footprint)
    mounting = "SMD" if "_SMD" in footprint else None
    conflicts = []
    for parameter in product.get("parameters", []):
        name = parameter.get("name", "").lower()
        values = [str(item.get("value", "")).upper() for item in parameter.get("values", [])]
        if mounting and name == "mounting" and any(value == "THT" for value in values):
            conflicts.append("BOM requires SMD; TME explicitly specifies THT.")
        if chip and name == "case - inch" and values and chip[1] not in values:
            conflicts.append(f"BOM requires {chip[1]} chip case; TME specifies {values}.")
    return conflicts
