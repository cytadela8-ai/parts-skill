"""Audit model evidence before any model pass can reach the human checklist.

A source must resolve to real, nonempty evidence. Technical passes need BOM requirements and
supplier evidence. Pinout applies to assigned multi-terminal devices; assembly orientation of
standard two-terminal parts is outside this compatibility check.
"""

import re

from parts_app.agents import Validation
from parts_app.requirements import scalar

PIN_EVIDENCE = (
    "pinout",
    "pin configuration",
    "pin assignment",
    "terminal assignment",
    "lead configuration",
)
BOM_METADATA = {"Reference", "Qty", "Final Item Count", "Datasheet"}


def evidence_value(evidence: dict, path: str):
    """Resolve one literal dot path; missing or empty paths cannot prove a pass."""
    value = evidence
    try:
        for key in path.split("."):
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    return value


def pinout_scope(fields: dict, parameters: dict) -> str:
    """Separate standard two-terminal parts from devices needing pin assignment evidence."""
    identity = " ".join(str(v) for k, v in fields.items() if k not in BOM_METADATA).lower()
    for name in ("Number of pins", "Number of terminals", "Number of contacts", "No. of colours"):
        value = str(fields.get(name, parameters.get(name, "")))
        count = re.search(r"\d+", value)
        maximum = 1 if name == "No. of colours" else 2
        if count and int(count[0]) > maximum:
            return "required"
    if fields.get("Pinout", "").strip():
        return "required"
    if re.search(
        r"rgb|array|network|coupled|tapped|transformer|bridge|common (?:anode|cathode)", identity
    ):
        return "required"
    if re.search(
        r"transistor|mosfet|igbt|logic|integrated circuit|connector|terminalblock", identity
    ):
        return "required"
    if "package_" in identity or re.match(r"[UQ]\d+\b", fields.get("Reference", "")):
        return "required"
    if re.search(r"resistor|capacitor|inductor|\bled(?:_|\b)|diode", identity):
        return "not_applicable"
    if re.match(r"[RCLD]\d+\b", fields.get("Reference", "")):
        return "not_applicable"
    return "agent"


def audit_pinout(result: Validation, evidence: dict):
    """Apply pinout scope before generic source auditing or confidence scoring."""
    scope = pinout_scope(evidence["bom"], evidence["product"].get("parameters", {}))
    for check in result.checks:
        if check.category != "polarity_pinout":
            continue
        if scope == "not_applicable":
            check.outcome = "not_applicable"
            check.requirement = "Pinout"
            check.sources = []
            check.evidence = (
                "Standard two-terminal component; assembly orientation is outside this check."
            )
        elif scope == "required":
            relevant = [
                path
                for path in check.sources
                if path.startswith("product.parameters.")
                and any(name in path.lower() for name in PIN_EVIDENCE)
                and evidence_value(evidence, path)
            ]
            if check.outcome in {"pass", "not_applicable"} and not relevant:
                check.outcome = "unknown"
                check.evidence = "Required pin/pad assignment is not evidenced."
    if scope == "not_applicable":
        clear_orientation_warnings(result)


def clear_orientation_warnings(result: Validation):
    """Discard assembly-only warnings without dropping mixed technical concerns."""
    other = (
        r"footprint|mounting|geometry|rating|voltage|resistance|capacitance|value|mpn|stock|price"
    )
    result.uncertainties = [
        note
        for note in result.uncertainties
        if not re.search(r"pinout|polarity|pin assignment|orientation", note, re.IGNORECASE)
        or re.search(other, note, re.IGNORECASE)
    ]
    remaining = any(
        c.outcome in {"fail", "unknown"} and c.category not in {"polarity_pinout", "commercial"}
        for c in result.checks
    )
    if not remaining:
        result.likely_bad = False
    if re.search(r"pinout|polarity|orientation", result.comment, re.IGNORECASE):
        result.comment = "Pinout is not applicable for this standard two-terminal component."


def audit_checks(result: Validation, evidence: dict):
    """Downgrade unsupported passes and apply component-specific pin assignment policy."""
    fields = evidence["bom"]
    audit_pinout(result, evidence)
    for check in result.checks:
        if check.outcome != "pass":
            continue
        valid = [path for path in check.sources if evidence_value(evidence, path)]
        bom = [
            path
            for path in valid
            if path.startswith("bom.") and path.split(".")[1] not in BOM_METADATA
        ]
        supplier = [path for path in valid if path.startswith("product.")]
        if (
            len(valid) != len(check.sources)
            or not supplier
            or (check.category != "commercial" and not bom)
        ):
            check.outcome = "unknown"
            check.evidence = (
                "Evidence audit: pass lacks a nonempty BOM requirement and "
                "relevant supplier evidence. Original claim: " + check.evidence
            )
    normalize_unspecified(result, fields)


def normalize_unspecified(result: Validation, fields: dict):
    """Keep pure generic BOMs from inheriting requirements from supplier properties."""
    # Generic symbol values do not specify ratings, technology, a full MPN or numeric value.
    generic = scalar(fields.get("Value", "")) is not None or fields.get(
        "Value", ""
    ).strip().lower() in {
        "led",
        "r",
        "c",
        "l",
        "resistor",
        "capacitor",
        "inductor",
    }
    extra = [
        str(v).strip().lower()
        for k, v in fields.items()
        if k not in BOM_METADATA | {"Value", "Footprint"} and str(v).strip()
    ]
    simple_descriptions = {"led", "resistor", "capacitor", "inductor", "polarized capacitor"}
    if generic and all(value in simple_descriptions for value in extra):
        for check in result.checks:
            if check.category in {"ratings", "mpn", "technology"}:
                requirement_sources = [
                    path
                    for path in check.sources
                    if path.startswith("bom.")
                    and path.split(".")[1] not in BOM_METADATA | {"Value", "Footprint"}
                ]
                if not requirement_sources:
                    check.outcome = "not_applicable"
                    check.requirement = f"No specified {check.category} requirement"
                    check.evidence = "No requirement for this property is specified in BOM inputs."
            if (
                check.category == "value"
                and fields.get("Value", "").lower() == "led"
                and not any(
                    k.lower() in {"led colour", "colour", "color", "wavelength"} for k in fields
                )
            ):
                check.outcome = "not_applicable"
                check.evidence = "Generic LED has no specified numerical or colour value."
