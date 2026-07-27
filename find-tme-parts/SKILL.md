---
name: find-tme-parts
description: Find electronic components on TME for delivery to Poland. Use when given a component description, manufacturer part number, BOM, or CSV with Reference, Value, Footprint, and Qty columns and the task requires in-stock TME alternatives, PLN pricing, MOQ-aware quantities, TME links, or validated CSV results.
---

# Find TME Parts

Use `scripts/tme_parts.py`; do not create ad hoc `curl` commands or hand-roll TME API requests. The helper authenticates with `.env` credentials, searches TME for Poland, obtains PLN prices and stock, applies an available-stock filter, normalizes MOQ/order multiples, and ranks candidates using payable line cost and the small-order stock rule.

## Translate KiCad footprints before selecting parts

Treat `footprint-translations.csv` as the authoritative KiCad-to-TME package mapping. Constraints sharing a `kicad_footprint` and `option` form an AND condition; different options are alternatives. Do not select a CSV-row candidate until it fulfils every constraint in one option.

When a KiCad footprint has no mapping, first ask TME for package vocabulary in the relevant component search:

```bash
python3 <skill-path>/scripts/tme_parts.py discover-footprint \
  --phrase '10k resistor 1%'
```

Compare that vocabulary and the returned sample-product parameters with the KiCad footprint's case, diameter, pitch, mounting, and pin arrangement. For capacitors, use diameter and mounting; **height is never a matching requirement**. When two diameters are allowed, create one option for each permitted diameter. Then append only verified constraints:

```bash
python3 <skill-path>/scripts/tme_parts.py map-footprint \
  --kicad-footprint 'Resistor_SMD:R_0805_2012Metric' \
  --phrase '10k resistor 1%' \
  --constraint 'Case - inch=0805' \
  --constraint 'Mounting=SMD' \
  --notes 'KiCad 2012 metric / 0805 imperial chip resistor'
```

Use `--numeric-constraint 'Diameter=5'` for a millimetre diameter verified in a returned product parameter. `map-footprint` validates categorical values against live TME facets and numeric dimensions against live product parameters before it changes the translation file. Do not map a footprint from name similarity alone. If its mechanical equivalence is uncertain, leave it unmapped and report `needs_review`.

## Inputs

- Treat a full manufacturer part number as an exact-part request. Treat a partial identifier, an `xxx` placeholder, or a family name as a family request.
- For a generic component, distinguish exact requirements from minimum requirements. Value, function, polarity, footprint, pin arrangement, mounting, and explicitly stated technology are exact. Higher voltage/current/power rating, tighter tolerance, and wider temperature range are acceptable when the input expresses a minimum.
- Start with a broad category or family phrase and the verified footprint constraints. Narrow the phrase or add TME parameter filters only when the broad result has no cheap candidate that fulfils every requirement.
- For CSV input, require `Reference`, `Value`, `Footprint`, and `Qty`. Preserve other columns.
  A non-empty `TME_SYMBOL` or `TME Symbol` is a manual selection: pass it through with status
  `manually_provided` without TME lookup or review. `TME_SYMBOL` takes precedence if both exist.
- Treat `Qty` as the requested total. Judge price and stock using `order_quantity`, which accounts for TME MOQ and multiples.

## Search and validate one component

Run a broad search first, then inspect the cheapest candidates:

```bash
python3 <skill-path>/scripts/tme_parts.py search \
  --phrase '10k ohm resistor 1% 0.1W' --footprint 0402 --qty 25
```

The helper returns in-stock candidates with footprint constraints, stock, price breaks, and full parameters. It ranks candidates using the applicable purchase cost and the small-order stock rule. Validate the cheapest candidates in that order. If none fulfils the requested requirements, refine the phrase or use additional TME parameter filters and search again.

Before selecting a candidate, compare its `description` and `parameters` to every stated requirement. Select a technically better part when it fulfils the same function and all exact requirements. **Never select a part whose footprint is not confirmed as an exact match.** A text-only `footprint_match` is not enough: verify the mapped package/case/mounting parameters yourself. If TME does not expose the required footprint or no in-stock candidate has it, report no safe match rather than substituting another footprint.

For an exact MPN, ensure the returned manufacturer part number exactly matches after normalizing harmless punctuation only. Do not accept a family name, suffix variant, or similar product as exact. For a family request, select the cheapest in-stock family member with the confirmed footprint and stated requirements. Mark it `family_match`, show the requested family and selected MPN, and list any characteristics that were not specified or independently validated.

Among candidates with a confirmed footprint, calculate `line_total_pln = order_quantity × unit_price_pln`; TME stock must cover that order quantity. Find the lowest line total, then select the candidate with the largest order quantity whose total is no more than 25% above it. This deliberately favors extra stock when the added spend is modest. Flag commercially suspicious selections for attention—for example, a large surplus, unexpectedly high MOQ, high line total, or an expensive IC order. State why it is suspicious; do not exclude it automatically. Flag any deviation in value, tolerance, voltage/current/power rating, dielectric, temperature grade, packing, or other stated characteristic. When no exact in-stock part exists, select the closest candidate **only if its footprint is exact**, and enumerate every mismatch.

## Report a single part

State whether it is exact or a closest alternative. Include:

- TME product number (`symbol`) and manufacturer part number (`manufacturer_part_numbers`)
- TME product link (`product_url`)
- PLN unit price, payable line total, requested quantity, MOQ/multiple-adjusted order quantity, excess quantity, and stock
- A concise risk/attention note, including anything not independently validated

Do not claim that a requirement matches merely because it was absent from TME data.

## Process a CSV and improve the helper

Create an initial enriched file with the helper:

```bash
python3 <skill-path>/scripts/tme_parts.py csv input.csv --output tme-results.csv
```

## Launch the local review app for the user

After producing the AI-processed file, launch the review app for the user:

```bash
uv run --project <skill-path> <skill-path>/find-tme-parts/scripts/review_parts.py \
  tme-results.csv
```

Tell the user the browser address shown by the command. The first launch creates
`tme-results-final.csv` in the same directory. This is the final state file; reuse it on later
launches, and never regenerate it from the processed CSV after the user has edited it. The app
initializes `Final Item Count` to the larger of one extra unit or 15% extra, rounded up, and
preserves later user edits.

The user can sort the table by any column, then select each row to inspect the current TME
description, API-provided photo, and all returned properties inline. The visible TME product link
opens the selected product page. The user may paste a replacement TME symbol; the app fetches it
through the authenticated API and persists the replacement fields with status
`manually_substituted`. Remind the user that accepting a symbol does not establish footprint safety;
they should compare the displayed properties against the BOM footprint and stated requirements.

Use this full loop:

1. Run the helper over the complete original CSV. It validates headers and quantities, preserves row order and all original columns, and adds TME result columns. Manual-symbol rows are marked `manually_provided` without lookup. For every other row, a missing footprint mapping becomes `needs_review`; it never falls back to matching the raw KiCad footprint string.
2. Review every row that is not a validated match. Determine whether the cause is a missing or incorrect footprint mapping, insufficient search interpretation, a helper/API limitation, or genuinely no suitable in-stock part.
3. When a safe reusable improvement exists, add verified translation constraints or update the helper. Do not change a mapping merely to make a candidate appear valid.
4. Rerun the helper over the complete original CSV, not an earlier result CSV, then review every row again.
5. Review every `candidate_unreviewed` row against the returned description and parameters before considering the CSV final. Change it to a validated match only when its component type, value, ratings, footprint, and MPN/family interpretation have been checked. Otherwise change it to `needs_review` or `no_safe_match` with the specific reason.
6. Repeat while a concrete safe improvement remains. Otherwise preserve a precise `needs_review`, `no_safe_match`, or `no_in_stock_match` explanation.

Use `family_match` for a partial IC identifier/family result. Use `needs_attention` for commercially suspicious but otherwise valid choices. Do not return an unreviewed CSV as a final recommendation.

## Give a post-processing summary in chat

After the final CSV review, give a concise summary in chat. Include the output path, number of loop iterations, and counts for manually provided rows, validated matches, `family_match`, `needs_attention`, `needs_review`, `no_safe_match`, and `no_in_stock_match`.

For every row needing review or other user action, list the reference designator, selected TME symbol if any, and the specific reason. Do not merely repeat a generic CSV note such as "verify requirements". Explain the unresolved point: missing footprint mapping, uncertain package geometry, wrong or uncertain component type, value/rating mismatch, partial-MPN family selection, unavailable stock, ambiguous BOM value, or an API limitation.

Add an **additional findings** section for material facts not represented by the CSV columns: suspicious MOQ/line cost, excess quantity, plausible alternatives rejected and why, TME rate limiting or incomplete API data, and assumptions made from an incomplete BOM description. Call out any unreviewed candidate explicitly; it is not approved for ordering.

## Credentials and failures

Read `APP_TOKEN` and `APP_SECRET` from a local `.env` file or the process environment. Never print, copy, commit, or include credentials in output. Explain authentication, network, or TME API errors and stop; do not fabricate part, price, or stock data.
