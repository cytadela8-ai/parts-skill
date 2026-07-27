# Inline TME Review Design

## Goal

Improve the local TME BOM review app so users can compare quantities and prices in a wide, sortable
table and inspect one selected part inline.

## User Interface

The page uses the available viewport width for a single review table. Its columns are Reference,
Value, original `Qty`, final item count, selected TME symbol, unit price in PLN, line total in PLN,
and match status. Every column heading is an accessible button that toggles ascending and descending
sorting. Numeric columns use numeric comparison and empty values sort after populated values.

Clicking a data row expands one detail row immediately below it. Selecting a different row closes the
previous one. The detail row contains the API-provided product photo when TME returns one, product
description, current TME product link, full parameter list, final-count editor, and replacement form.
The existing TME product link remains clickable. No datasheet link or TME product-page fetching is
implemented: the TME API product response does not provide a direct datasheet URL, and the app must
not scrape product pages.

## Data Flow

The existing row endpoint supplies table fields. The product-detail service extends its normalized
TME API result with `photo_url`, derived only from the product response's `assets.primary_photo`
fields. A missing photo is represented by an empty URL and simply hides the image element.

Sorting happens in browser memory and never changes final CSV row order. The selected row is tracked
by its persistent CSV index, so it remains selected after a sort. Count edits and substitutions
continue using the existing row-index endpoints and atomic final-CSV updates.

## Testing

Tests cover API-photo normalization, table payloads containing original and final counts plus prices,
and the rendered shell's inline-detail/table controls. Existing replacement and count-edit tests
continue to guard persistence behavior.
