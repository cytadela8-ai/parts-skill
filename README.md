# TME Parts Desk

A local Python application for sourcing and reviewing KiCad BOM components through a browser.
Python controls each row's search, validation, persistence, and spending. You choose what runs.

## Start

Requires Python 3.13+ and uv. Copy `.env.example` to `.env` and fill in your credentials:

```dotenv
APP_TOKEN=your_tme_app_token
APP_SECRET=your_tme_app_secret
OPENROUTER_KEY=your_openrouter_key
```

```bash
uv sync
uv run parts-app
```

Open <http://127.0.0.1:5000>. Alternative options:

```bash
uv run parts-app --port 5050 --database parts-data/my-project.sqlite3 --env-file /path/to/.env
```

The server listens on loopback by default. Use `--host 0.0.0.0` to accept connections on
all interfaces. Mutation-origin checks remain active. Keys remain on the server. The initial agent model is
`google/gemini-2.5-flash-lite`, chosen after live structured-output testing.

## Use

1. Upload a CSV with `Reference`, `Value`, `Footprint`, and `Qty`. Preview and confirm the import.
   UTF-8 BOM, comma/semicolon/tab delimiters, grouped references and custom columns are supported.
   Include symbol name, description, ratings, MPN, and other KiCad properties as extra columns:
   agents receive these properties. Properties absent from CSV cannot be recovered from KiCad.
2. Select rows using checkboxes, Shift-click ranges, or **Select all filtered**. Sorting preserves
   selected IDs. Choose **Search TME**, **Validate selections**, or **Search then validate**.
3. Preview the selected references and spend allowance, then run. Search skips protected manual,
   approved, substituted, and not-needed rows. Validation can assess manually selected components.
   **Allow automated replacement** explicitly removes that protection. Not-needed rows remain excluded.
4. Open a row to compare its KiCad properties and TME evidence. Search may select a tentative part
   with doubts. Validation reports Pass, Attention, or Fail with comments for each check.
   Uncertain or incompatible candidates remain visible for human review. Evidence-audit
   warnings are separate from agent check outcomes and human approval.
5. Edit requirements/counts, save replacements, approve, or mark not needed. Changed requirements
   invalidate current validation and approval. Stale worker responses cannot overwrite edits.
6. Export the enriched CSV, or approve all needed rows before downloading the basket CSV.
   **Back up project database** downloads full state, evidence and history. To restore, launch
   with `--database /path/to/downloaded/parts.sqlite3`. A CSV alone cannot restore API evidence.

API failures are visible per job. Pause prevents further requests; in-flight calls can finish and
cost money. Cancel prevents publishing subsequent results. Interrupted jobs require explicit
resume after restart. Uncertain timeout charges remain reserved. Failed jobs require a fresh run.

## Matching policies

Original manual `TME_SYMBOL` takes precedence over `TME Symbol` in an original BOM. Enriched CSVs
retain the selected canonical symbol and existing status. Reimport does not convert AI choices
into original manual choices. Keep enriched status columns when saving previous results.

Full manufacturer part numbers are exact requests; partial identifiers and xxx placeholders are
family requests. Value, function, applicable pin arrangement, footprint, mounting and explicitly
specified technology are exact constraints. Higher ratings are acceptable only for minimum
requirements. Missing evidence remains unknown.

Verified footprint constraints are AND within an option, OR across options. Capacitors retain the
original diameter/mounting policy: height is not a matching requirement. This does not establish
mechanical clearance. Unmapped/custom packages remain reviewable tentative candidates; missing
geometry or pinout prevents a validated match. Search can propose row-local footprint constraints from returned TME parameters. Inspect them and
use **Accept footprint constraints for this row** before validation can rely on them. Reusable
mappings live in `src/parts_app/footprint-translations.csv`. Global mapping editing is not exposed
in the UI. Never add mappings based solely on similar package names.

Stock must cover MOQ/multiple-adjusted quantities. After technical eligibility, prefer the greatest
order quantity within 25% of the lowest payable PLN line total, then lower total and TME symbol.
All mismatches and commercial concerns must be reported. Final counts initialize once to
`max(Qty + 1, ceil(Qty * 1.15))`; subsequent edits are retained. Not-needed means count zero.

Search discovers matching categories, chooses a real category, reads its available parameters,
then filters and requests results sorted by first-quantity price. Python forces supported exact
chip-case and nominal-value filters and filters for supported explicit BOM properties, even if
an agent omits them. A chip's inch and metric codes are checked independently; 0805 and 1206
are incompatible. Missing parameters remain unknown. Explicit minimum ratings may increase;
other stated ratings remain exact. Documented footprint alternatives remain allowed.

Empty or conflicting results feed the next category/filter decision. The loop permits up to
three attempts with three pages each. Search history records categories, filters, rejected
candidates and recovery reasons. Compact model shortlists are bounded by size. It cannot establish exhaustive availability or a globally lowest price. Empty or incomplete
searches remain `needs_review` rather than falsely claiming no stock. Automatic PDF/pinout analysis
is not included; missing evidence is surfaced for human review.

Validation independently compares BOM inputs with downloaded product evidence. Application
checks override model approval for known package, nominal-value and supported explicit-property
conflicts. Unsupported passes and missing required pin assignments produce attention; supplier
ratings do not create BOM requirements. The UI calls terminal-assignment checks **Pinout**.
Standard two-terminal LEDs, capacitors (including polarized capacitors), resistors, inductors
and diodes use not applicable for pinout. Assembly orientation remains the assembler's concern.
Transistors, MOSFETs, logic gates, ICs, connectors, RGB LEDs and other multi-terminal parts still
require pin assignment checks; missing evidence produces attention. Explicit BOM pinout
requirements are retained. Raw model assessments remain in the saved evidence for
audit. Historical validations need an explicit rerun to use these rules.

## Costs and checks

Each row run reserves at most $0.008 in inference spending, shared between search and validation.
Run allowances can be lower. Requests have output/context bounds, no automatic model upgrades,
and provider price ceilings of $0.10/M input and $0.40/M output. Reservations are transactional;
unknown charges remain reserved. Later explicit reruns incur additional costs.

One live 10k/0805 search-plus-validation cost $0.001137. This is a smoke test, not a representative
accuracy or cost benchmark. OpenRouter credit purchase fees and taxes are additional. A low-cost
agent cannot establish requirements missing from the BOM; human review is still necessary.

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src
```

Implementation details: [DEV.md](DEV.md). Original proposal: [docs/parts-app-proposal.md](docs/parts-app-proposal.md).

## Evaluation on reviewed projects

Both real project CSVs were tested: 84 imported rows, 40 purchased rows evaluated with live search
and validation. Exact human-symbol agreement was 2/23 for VFD and 7/17 for the other project. The
latest representative search+validation attempts averaged about $0.000954 per row, but required
substantial manual review. See [the evaluation report](docs/evaluation/README.md) for per-row results,
false-rejection examples, supplier-data discrepancies, and measured limitations.

Exported compatibility confidence is conservatively capped at 20 for failed technical checks and
60 for unresolved checks/evidence. Original model confidence is retained as `model_confidence`
in stored validation evidence. These are policy limits, not statistical calibration; the browser
shows check outcomes instead of percentages.

The review panel opens KiCad and TME property tables side by side (stacked on narrow screens).
Opening a selected part loads its current supplier data automatically; the first five visible
selections are prefetched. Supplier reads are cached for 15 minutes and never alter approvals
or stored validation. Use **Save replacement** to change the selected TME symbol. **Allow
automated replacement** keeps the current selection but permits a future search to replace it.

**Agent checks** show Pass when applicable checks pass, Attention for unresolved checks, and
Fail for a reported conflict. Not-applicable checks are neutral. Evidence citation audit warnings
remain visible separately; a green agent assessment does not constitute human approval. The
checklist shows each requirement, outcome, comment and cited source. CSV confidence fields are
retained for export. One run panel shows current progress or the latest completed run, while BOM
inference spend accumulates across runs for that imported BOM (known charges only).

While a procedure runs, its panel shows the current reference and actual phase: planning,
TME query/page, downloading parameters or prices/stock, selecting a candidate, or validating.
Elapsed and step timers update every second; status is polled every two seconds. Model rate-limit
waits and output-repair retries appear in the activity trail. A connection warning appears if
status updates stop arriving. Row completion remains a count, not an estimated per-part percentage.

Run preview lists an action and reason for every selected row. Search-only skips protected
selections. Search then validate validates protected selections in place and searches the rest.
Not-needed rows remain excluded. Runs retain skipped rows and show their reasons separately
from processed and failed rows; the allowance defaults to the number of rows that will run.
