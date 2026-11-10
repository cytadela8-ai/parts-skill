# Constraint-aware TME search implementation plan

**Goal:** Filter by actual category properties and prevent known incompatibilities from passing.
**Architecture:** Python owns bounded search iterations and deterministic requirement checks.
Agents select real categories/filters and assess remaining evidence. Persist API search history.
**Tech Stack:** Existing Python, Pydantic AI and TME v2; no new dependencies.
**Spec:** User request of 2026-11-10 (LED package mismatch and adaptive category filtering).

## Constraints and review focus

- Preserve uncertain proposals, manual decisions and the existing $0.008 row allowance.
- Exact package dimensions, mounting, values, pinout, type and technology cannot be relaxed.
- Minimum ratings can improve only when explicitly specified as minimums.
- Missing evidence is unknown; supplier properties cannot create BOM requirements.
- Preserve allowed footprint alternatives and the capacitor-height exception.
- Test wrong inch/metric case, mounting, value, explicit fields, missing evidence, invented
  filter IDs, empty results, retry feedback and an overconfident validator.

## Tasks

- [x] Add shared derived footprint constraints and deterministic checks for explicit BOM fields.
  Test LED and non-LED conflicts, missing evidence, aliases and permitted alternatives.
- [x] Replace fixed broad searches with category discovery, category-specific facet inspection,
  real facet filters, sorted results and bounded recovery informed by previous results.
  Store category/filter/count/rejection history; test actual API request construction.
- [x] Strengthen shared and role-specific prompts. Merge application checks into the validator's
  checklist so a model pass cannot hide known conflicts or unresolved required properties.
- [x] Run regression tests, lint, formatting and types; exercise live LED, passive and negative
  cases without altering existing human records. Record measured inference cost and limits.
- [x] Document behavior, commit on main, and restart the app against its existing database.
