"""Audit model evidence before any model pass can reach the human checklist.

A source must resolve to real, nonempty evidence. Technical passes need BOM requirements and
supplier evidence; mechanical pin assignment cannot be established by unrelated LED properties.
"""

import re

from parts_app.agents import Validation
from parts_app.requirements import scalar

PIN_EVIDENCE = (
    "pinout",
    "pin configuration",
    "pin assignment",
    "terminal assignment",
    "polarity",
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


def audit_checks(result: Validation, evidence: dict):
    """Downgrade unsupported passes and require actual pin assignment for polarized parts."""
    fields = evidence["bom"]
    identity = " ".join(str(v) for k, v in fields.items() if k not in BOM_METADATA).lower()
    identity = (
        identity.replace("non-polarized", "")
        .replace("non polarized", "")
        .replace("unpolarized", "")
    )
    polarized = bool(
        re.search(
            r"\bled(?:_|\b)|diode|\bpolarized\b|transistor|package_|cp_|connector|terminalblock",
            identity,
        )
    )
    for check in result.checks:
        if check.category == "polarity_pinout" and polarized:
            relevant = [
                path
                for path in check.sources
                if path.startswith("product.parameters.")
                and any(name in path.lower() for name in PIN_EVIDENCE)
                and evidence_value(evidence, path)
            ]
            if check.outcome in {"pass", "not_applicable"} and not relevant:
                check.outcome = "unknown"
                check.evidence = "Pin/pad assignment or polarity is required but not evidenced."
        if (
            not polarized
            and check.category == "polarity_pinout"
            and check.requirement.lower() == "not_applicable"
        ):
            check.outcome = "not_applicable"
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
