# Manual TME symbol passthrough

## Scope

When enriching a BOM CSV, a row with a non-empty manually supplied TME symbol must bypass
footprint validation, TME API calls, and candidate selection.

## Input and output

The helper recognizes either `TME_SYMBOL` or `TME Symbol` as the manual-symbol input column.
If both are present, `TME_SYMBOL` takes precedence. Whitespace-only values are treated as absent.

For a manual-symbol row, the output preserves original columns, writes the symbol to the canonical
`TME Symbol` output column, and sets `TME Match Status` to `manually_provided`. Its match note
states that the symbol was provided manually and not reviewed by the helper.

## Processing order

`enrich()` evaluates the manual-symbol bypass before loading footprint mappings, validating a
search phrase, or calling TME search/detail endpoints. Rows without a manual symbol retain the
existing enrichment behavior.

## Validation and documentation

Behavior tests will verify both accepted input headers, precedence, whitespace handling, canonical
output, `manually_provided` status, and that the bypass does not invoke lookup work. The README
will document the optional manual-symbol columns and their bypass behavior.
