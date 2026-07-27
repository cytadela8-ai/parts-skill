# Development notes

## Structure

- `find-tme-parts/scripts/tme_parts.py` implements the TME API client, footprint mappings, BOM
  validation, and CSV enrichment CLI.
- `find-tme-parts/scripts/review_state.py` owns final CSV initialization, count calculation, and
  atomic CSV updates.
- `find-tme-parts/scripts/review_tme.py` converts current TME API product records into review data.
- `find-tme-parts/scripts/review_parts.py` exposes the loopback-only Flask review app and JSON API.
- `find-tme-parts/templates/review.html` and `find-tme-parts/static/` contain the dependency-free
  responsive review interface.
- `find-tme-parts/footprint-translations.csv` stores verified KiCad-to-TME footprint constraints.
- `tests/test_tme_parts.py` contains behavior tests for mapping and CSV enrichment.

## CSV enrichment

The required BOM columns are `Reference`, `Value`, `Footprint`, and `Qty`. A non-empty
`TME_SYMBOL` or `TME Symbol` is an authoritative manual selection: it is emitted in the canonical
`TME Symbol` output column with status `manually_provided`, and bypasses TME search and footprint
review. `TME_SYMBOL` wins if both columns are present.

Run the test suite with:

```bash
uv run python -m unittest discover -s tests -q
```

## Final CSV review

`review_parts.py` takes an AI-processed CSV and creates a sibling `<stem>-final.csv` if absent.
That final CSV is the only writable state. It adds `Final Item Count`, using
`max(Qty + 1, ceil(Qty * 1.15))` on creation and preserving every non-empty later value.

The app fetches part description, parameters, and the API-provided primary product photo from TME
when a row is selected. It displays the detail inline below the selected row; sorting does not change
the CSV order. Substitution fetches a pasted TME symbol before atomically writing its product,
pricing, stock, and status fields. The server listens only on `127.0.0.1`. Use `uv run --project <repo-root>
find-tme-parts/scripts/review_parts.py AI_RESULTS.csv` to launch it.
