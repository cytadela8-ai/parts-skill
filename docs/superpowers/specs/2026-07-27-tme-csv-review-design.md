# TME CSV Review App Design

## Goal

Provide a local Flask web application for reviewing an AI-enriched TME BOM CSV,
persisting final selections and final order counts in a sibling final CSV.

## Scope

The application accepts one AI-processed CSV path. On its first launch for that
path, it creates `<stem>-final.csv` beside it by copying all rows and columns and
adding `Final Item Count`. Later launches use that final CSV as the sole writable
state and never recalculate a non-empty final count.

`Final Item Count` is initialized to `max(Qty + 1, ceil(Qty * 1.15))`. Users can
edit it in the browser and those edits are immediately persisted.

## Architecture

- `find-tme-parts/scripts/review_parts.py` is a Flask CLI entry point. It locates
  the final CSV, initializes it when absent, and starts a loopback-only server.
- A small CSV state module owns header validation, initialization, row updates,
  and atomic writes. It keeps the final CSV as the only application state; no
  database is used.
- A TME product service reuses the existing authenticated API helpers in
  `tme_parts.py`. It fetches a product by TME symbol and normalizes the concise
  product fields, description, full parameters, product URL, and datasheet URL.
- Flask JSON endpoints serve rows, product details, final-count edits, and
  substitutions. The single-page UI is rendered from a Flask template with
  locally served CSS and small browser-side JavaScript. It needs no frontend
  build step or additional JavaScript dependency.

## User Experience

The page uses a responsive, keyboard-friendly table. Status chips and readable
numeric columns make outstanding work obvious. Selecting a row opens a fixed
detail pane on wide screens and an inline panel on narrow screens. It presents a
short part summary first, then expandable full TME properties, and visible links
to the TME product page and datasheet when TME supplies them.

The user can paste a replacement TME symbol in the selected row's detail pane.
The server fetches and validates that product before persisting its TME symbol,
manufacturer part number, product URL, unit price, line total based on the final
count, stock, description, datasheet URL, and `manually_substituted` status. An
invalid symbol or TME/API failure shows an actionable error and leaves the final
CSV unchanged.

## Launch and Dependencies

The project uses `uv`, not Pipenv or pipx. A `pyproject.toml` declares an exact
Flask 3.1.x dependency, and `uv.lock` provides reproducible installs. The skill
directs agents to launch the app with:

```bash
uv run --project <skill-path> <skill-path>/scripts/review_parts.py AI_RESULTS.csv
```

The command accepts an optional `--port`; it binds to `127.0.0.1` only.

## Error Handling and Data Integrity

The app fails before starting if required CSV headers or quantities are invalid.
All writes use a temporary sibling file followed by replacement, so a failed
write does not corrupt the final CSV. Credentials stay in the existing `.env` or
environment flow and never appear in the page or error text.

## Tests and Documentation

Behavior tests cover final-file creation, initial count calculations, preserving
manual count edits, row selection payloads, successful replacement updates, and
non-mutating replacement errors. TME calls are mocked at the service boundary.
The README, DEV.md, and skill instructions document architecture, prerequisites,
and the agent-friendly launch command.
