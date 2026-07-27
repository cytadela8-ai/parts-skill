---
name: find-tme-parts
description: Use when selecting TME parts for Poland from a component description, MPN, BOM, or CSV.
---

# Find TME Parts

Use `scripts/tme_parts.py`; do not create ad hoc `curl` commands or hand-roll TME API requests. The
helper authenticates with `.env` credentials, searches TME for Poland, obtains PLN prices and stock,
applies an available-stock filter, normalizes MOQ/order multiples, and ranks candidates using
payable line cost and the 25%-window surplus rule defined below.

In every command below, `<skill-dir>` means the directory containing this `SKILL.md`. For the
human review app, `<repo-root>` means the parent directory containing `pyproject.toml`.

## Translate KiCad footprints before selecting parts

Treat `footprint-translations.csv` as the authoritative KiCad-to-TME package mapping. Constraints
sharing a `kicad_footprint` and `option` form an AND condition; different options are alternatives.
Do not select a CSV-row candidate until it fulfils every constraint in one option.

When a KiCad footprint has no mapping, first ask TME for package vocabulary in the relevant
component search:

```bash
python3 <skill-dir>/scripts/tme_parts.py discover-footprint \
  --phrase '10k resistor 1%'
```

Compare that vocabulary and the returned sample-product parameters with the KiCad footprint's case,
diameter, pitch, mounting, and pin arrangement. For capacitors, use diameter and mounting;
**height is never a matching requirement**. When two diameters are allowed, create one option for
each permitted diameter. Then append only verified constraints:

```bash
python3 <skill-dir>/scripts/tme_parts.py map-footprint \
  --kicad-footprint 'Resistor_SMD:R_0805_2012Metric' \
  --phrase '10k resistor 1%' \
  --constraint 'Case - inch=0805' \
  --constraint 'Mounting=SMD' \
  --notes 'KiCad 2012 metric / 0805 imperial chip resistor'
```

Use `--numeric-constraint 'Diameter=5'` for a millimetre diameter verified in a returned product
parameter. `map-footprint` validates categorical values against live TME facets and numeric
dimensions against live product parameters before it changes the translation file. Do not map a
footprint from name similarity alone. If its mechanical equivalence is uncertain, leave it unmapped
and report `needs_review`.

## Inputs

- Treat a full manufacturer part number as an exact-part request. Treat a partial identifier, an
  `xxx` placeholder, or a family name as a family request.
- For a generic component, distinguish exact requirements from minimum requirements. Value,
  function, polarity, footprint, pin arrangement, mounting, and explicitly stated technology are
  exact. Higher voltage/current/power rating, tighter tolerance, and wider temperature range are
  acceptable when the input expresses a minimum.
- Start with a broad category or family phrase and the verified footprint constraints. Narrow the
  phrase or add TME parameter filters only when the broad result has no cheap candidate that fulfils
  every requirement.
- For CSV input, require `Reference`, `Value`, `Footprint`, and `Qty`. Preserve other columns.
  A non-empty `TME_SYMBOL` or `TME Symbol` is a manual selection: pass it through with status
  `manually_provided` without TME lookup or review. `TME_SYMBOL` takes precedence if both exist.
- Treat `Qty` as the requested total. Judge price and stock using `order_quantity`, which accounts
  for TME MOQ and multiples.

## Search and validate one component

Run a broad search first, then inspect the ranked candidates:

```bash
python3 <skill-dir>/scripts/tme_parts.py search \
  --phrase '10k ohm resistor 1% 0.1W' --footprint 0402 --qty 25
```

The helper returns in-stock candidates with footprint constraints, stock, price breaks, and full
parameters. Validate candidates in the helper's ranked order. If none fulfils the requested
requirements, refine the phrase or use additional TME parameter filters and search again.

Before selecting a candidate, compare its `description` and `parameters` to every stated
requirement. Select a technically better part when it fulfils the same function and all exact
requirements. **Never select a part whose footprint is not confirmed as an exact match.** A
text-only `footprint_match` is not enough: verify the mapped package/case/mounting parameters
yourself. If TME does not expose the required footprint or no in-stock candidate has it, report no
safe match rather than substituting another footprint.

For an exact MPN, ensure the returned manufacturer part number exactly matches after normalizing
harmless punctuation only. Do not accept a family name, suffix variant, or similar product as
exact. For a family request, use the same ranking hierarchy as any other request and select an
in-stock family member with the confirmed footprint and stated requirements. Show the requested
family and selected MPN in `TME Match Notes`, and list any characteristics that were not specified
or independently validated.

Apply this ranking hierarchy only after rejecting candidates that fail the technical and footprint
requirements:

1. Require TME stock to cover the MOQ/multiple-adjusted `order_quantity`.
2. Calculate `line_total_pln = order_quantity × unit_price_pln` and find the lowest line total.
3. Keep candidates whose line total is no more than 25% above that minimum.
4. From that set, select the candidate with the largest `order_quantity`. Break ties by lower line
   total, then TME symbol.

This deliberately favors more purchased units when the added spend is modest. Flag commercially
suspicious selections for attention—for example, a large surplus, unexpectedly high MOQ, high line
total, or an expensive IC order. State why it is suspicious; do not exclude it automatically. Flag
any deviation in value, tolerance, voltage/current/power rating, dielectric, temperature grade,
packing, or other stated characteristic. When no exact in-stock part exists, select the closest
candidate **only if its footprint is exact**, and enumerate every mismatch.

## Report a single part

State whether it is exact or a closest alternative. Include:

- TME product number (`symbol`) and manufacturer part number (`manufacturer_part_numbers`)
- TME product link (`product_url`)
- PLN unit price, payable line total, requested quantity, MOQ/multiple-adjusted order quantity,
  excess quantity, and stock
- A concise risk/attention note, including anything not independently validated

Do not claim that a requirement matches merely because it was absent from TME data.

## Process a CSV and improve the helper

Create an initial enriched file with the helper:

```bash
python3 <skill-dir>/scripts/tme_parts.py csv input.csv --output tme-results.csv
```

Use this AI processing loop. **Do not launch or use the review app during this loop.**

1. Run the helper over the complete original CSV. It validates headers and quantities, preserves
   row order and all original columns, and adds TME result columns. Manual-symbol rows are marked
   `manually_provided` without lookup. For every other row, a missing footprint mapping becomes
   `needs_review`; it never falls back to matching the raw KiCad footprint string.
2. Review every row that is not `manually_provided` or `validated_match`. Determine whether the
   cause is a missing or incorrect footprint mapping, an incorrect component type or search phrase,
   a helper/API limitation, or genuinely no suitable in-stock part.
3. For every initial `no_in_stock_match`, and whenever the helper's candidate has the wrong
   component type or fails a stated requirement, make one recovery call to `search`. Use a corrected
   phrase that names the component type and important requirements, the mapped TME package/case
   value, and the row quantity:

   ```bash
   python3 <skill-dir>/scripts/tme_parts.py search \
     --phrase '<corrected component phrase>' --footprint '<mapped package>' --qty <Qty>
   ```

   Inspect the returned description and parameters. When a candidate is safe, persist its TME
   symbol immediately in `tme-ai-selections.csv`:

   ```bash
   python3 <skill-dir>/scripts/tme_parts.py record-selection \
     --file tme-ai-selections.csv --reference '<Reference>' --symbol '<TME symbol>'
   ```

   The selection file contains only `Reference,TME Symbol`. `record-selection` creates it and
   replaces an earlier selection for the same exact, trimmed KiCad `Reference`. Never record a
   selection for a row with a manual symbol in the original BOM.

   Record the diagnosis in working notes; final statuses are applied only after the last helper
   call. The final result is `no_in_stock_match` only when the recovery search returns no priced
   in-stock candidate. If it returns candidates but none is safe, the final result is
   `no_safe_match`; record the specific reason. Never classify a wrong automatic candidate as
   `no_in_stock_match` without this recovery search.
4. During the same diagnosis pass, add verified footprint constraints when a mapping is missing or
   incorrect. Do not change a mapping merely to make a candidate appear valid.
5. If a footprint mapping change is likely to improve more automatic matches, rerun generation from
   the complete original CSV and return to step 2. Always supply the accumulated selection file so
   previous AI choices survive:

   ```bash
   python3 <skill-dir>/scripts/tme_parts.py csv input.csv \
     --output tme-results.csv --ai-selections tme-ai-selections.csv
   ```

   For a matching reference, the helper loads current data for exactly the selected TME symbol; it
   does not repeat the broad automatic search. It fails on unknown or duplicate references instead
   of guessing which row was intended.
6. When no concrete mapping improvement remains, make one final helper call over the complete
   original CSV with `--ai-selections`. This is the final generated base for AI validation; do not
   rerun the helper after editing its statuses or notes.

   ```bash
   python3 <skill-dir>/scripts/tme_parts.py csv input.csv \
     --output tme-results.csv --ai-selections tme-ai-selections.csv
   ```

7. Review every row in the final generated CSV except `manually_provided`, including regenerated
   `needs_review` and `no_in_stock_match` rows. Reapply every recovery-search outcome from the
   diagnosis pass. Use `validated_match` only after checking component type, value, ratings,
   footprint, and MPN/family interpretation. Keep `needs_attention` only for a technically validated
   but commercially suspicious choice. Otherwise use `needs_review`, `no_safe_match`, or
   `no_in_stock_match` according to the definitions below. Add a specific `TME Match Notes`
   explanation for each non-validated result. Do not return a CSV containing
   `candidate_unreviewed`.

Final AI status meanings:

- `manually_provided`: supplied by the input CSV; passed through without AI or helper validation.
- `validated_match`: technically validated by the AI, including a valid family member when the
  request was for a family.
- `needs_attention`: technically validated, but commercially suspicious.
- `needs_review`: unresolved because the input, footprint mapping, geometry, or API data is
  insufficient.
- `no_safe_match`: the searches returned candidates, but none could be selected safely.
- `no_in_stock_match`: the required recovery search returned no priced in-stock candidate.

`candidate_unreviewed` is temporary helper output and is never valid in a final AI-processed CSV.

## Give a post-processing summary in chat

After the final CSV review, give a concise summary in chat. Include the output path, number of loop
iterations, and counts for `manually_provided`, `validated_match`, `needs_attention`,
`needs_review`, `no_safe_match`, and `no_in_stock_match`.

For every row needing review or other user action, list the reference designator, selected TME
symbol if any, and the specific reason. Do not merely repeat a generic CSV note such as "verify
requirements". Explain the unresolved point: missing footprint mapping, uncertain package geometry,
wrong or uncertain component type, value/rating mismatch, partial-MPN family selection, unavailable
stock, ambiguous BOM value, or an API limitation.

Add an **additional findings** section for material facts not represented by the CSV columns:
suspicious MOQ/line cost, excess quantity, plausible alternatives rejected and why, TME rate
limiting or incomplete API data, and assumptions made from an incomplete BOM description. Call out
any unreviewed candidate explicitly; it is not approved for ordering.

## Launch the human review app at the end

The review app is for the human, after the AI processing loop and chat summary are complete. Do not
use it to search, validate, or change statuses during the AI loop. After informing the user of the
overall result, start it with the completed AI-processed CSV:

```bash
uv run --project <repo-root> <skill-dir>/scripts/review_parts.py tme-results.csv
```

The app owns the human review statuses `approved`, `not needed`, and `manually_substituted`. These
are separate from the final AI statuses above.

## Credentials and failures

Read `APP_TOKEN` and `APP_SECRET` from a local `.env` file or the process environment. Never print,
copy, commit, or include credentials in output. Explain authentication, network, or TME API errors
and stop; do not fabricate part, price, or stock data.
