# Development notes

## Structure

- `find-tme-parts/scripts/tme_parts.py` implements the TME API client, footprint mappings, BOM
  validation, and CSV enrichment CLI.
- `find-tme-parts/footprint-translations.csv` stores verified KiCad-to-TME footprint constraints.
- `tests/test_tme_parts.py` contains behavior tests for mapping and CSV enrichment.

## CSV enrichment

The required BOM columns are `Reference`, `Value`, `Footprint`, and `Qty`. A non-empty
`TME_SYMBOL` or `TME Symbol` is an authoritative manual selection: it is emitted in the canonical
`TME Symbol` output column with status `manually_provided`, and bypasses TME search and footprint
review. `TME_SYMBOL` wins if both columns are present.

Run the test suite with:

```bash
python3 -m unittest discover -s tests -q
```
