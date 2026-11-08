# TME parts application — proposal for approval

Status: proposal only. No application implementation is authorized by this document.
Prepared 2026-11-07 from the existing skill, Python helpers, review UI, and example results.

## What changes

Replace the Codex skill with a normal local Python application launched with `uv run parts-app`.
Open its web UI, upload a KiCad BOM or previous result CSV, select rows, and click procedures.
Python owns execution, persistence, retries, limits, and status transitions. Small row agents
interpret the BOM, search through constrained TME tools, and assess compatibility.
There is no conversational agent orchestrating the application.

Assume one local user, Poland delivery, PLN pricing, and a loopback-only server for version one.
Public hosting, authentication, direct KiCad project editing, and placing TME orders are outside
this proposal. The browser UI and CLI use the same Python operations.

## Requirements retained from the existing skill

- Preserve original CSV columns and row order. Require Reference, Value, Footprint, and Qty;
  retain symbol name, description, Datasheet, Opis, Comment, and custom properties when provided.
  Support grouped references as a single BOM row. Qty is already the requested total.
- An original manual TME_SYMBOL or TME Symbol remains authoritative and is not automatically
  searched, replaced, or validated. TME_SYMBOL takes precedence when both are present.
  The user can explicitly validate a manual selection from the UI.
- Interpret full MPNs as exact requests; partial identifiers, xxx placeholders, and family names
  as family requests. Do not discard meaningful suffixes during MPN normalization.
- Exact requirements include value, function, polarity, footprint, pin arrangement, mounting,
  and explicitly specified technology. Improve ratings only when the requirement is a minimum;
  do not assume every larger rating or tighter specification is an interchangeable substitute.
- Use verified KiCad-to-TME mappings: constraints within an option are AND, options are OR.
  Discover TME vocabulary and verify parameters before proposing missing mappings. Never derive
  mechanical equivalence from name similarity alone. Uncertain geometry stays needs_review.
- For capacitors, retain the existing diameter/mounting policy, allowed diameter alternatives,
  and the rule that height is not a matching requirement. Record this as a project policy rather
  than claiming clearance has been validated. Any later height-policy change needs approval.
- Start with a broad category/family query plus verified footprint filters, then narrow only when
  needed. Compare description and parameters with all requirements before selection.
- Require stock to cover MOQ/multiple-adjusted order quantity. Calculate payable PLN line cost.
  After technical rejection, find the cheapest line, retain candidates within 25%, and favor
  greatest purchased quantity; break ties by lower line total, then TME symbol.
- Flag excessive surplus, surprising MOQ, high line cost, and expensive IC orders. Do not silently
  replace that ranking policy with lowest unit price. Report every characteristic deviation.
- If no exact part exists, a closest alternative must still have an exactly confirmed footprint;
  retain it as an explicitly described alternative and expose every mismatch for human review.
- Make a corrected recovery search when initial results are empty, the component type is wrong,
  or candidates fail requirements. Preserve selected candidates through later processing.
- Distinguish needs_review, no_safe_match, and no_in_stock_match. API/authentication failures are
  execution errors, never evidence of absent stock. Missing TME fields never establish a match.
- Keep AI statuses manually_provided, validated_match, needs_attention, needs_review,
  no_safe_match, and no_in_stock_match. candidate_unreviewed is an intermediate search result,
  never a completed validation result. Human statuses remain approved, not needed, and
  manually_substituted; agents cannot grant human approval.
- Retain manual substitutions, count edits, approval, not-needed actions, side-by-side TME/KiCad
  comparison, photos, full-precision exported prices, two-decimal PLN display, and basket CSV.
- Initialize Final Item Count once with max(Qty + 1, ceil(Qty * 1.15)); preserve later edits.
  MOQ/multiples and prices must be recalculated against the final requested count for ordering.
  Not-needed rows keep count zero and are excluded from the basket.
- Replace the old chat summary with a UI run summary: status counts, iterations/calls, concrete
  unresolved reasons per reference, rejected alternatives, assumptions, and commercial warnings.

The old requirement to open the review app only after processing is intentionally replaced:
the web app is now the control surface throughout. Whole-file regeneration and sidecar AI-choice
overlays are replaced by persisted row runs; this preserves their purpose without repeating work.

## Browser workflow

1. Upload CSV. Preview detected delimiter/encoding, required-column mappings, manual selections,
   prior statuses, and import errors before creating a project. Accept UTF-8 BOM, comma or
   semicolon CSV, quoted fields, and grouped references. Empty Value/Footprint rows can be kept
   for review but cannot produce validated matches without adequate requirements.
2. Select rows with checkboxes, Shift-click ranges, select all filtered rows, or select all rows.
   Selection uses stable IDs and survives sorting/filtering. Show selected and excluded counts.
3. Choose Search TME, Validate selections, or Search then validate. The combined action is exactly
   those two procedures, with validation starting only after its row's search has completed.
4. Inspect a run preview: selected references, procedure, protected/manual rows skipped, model,
   concurrency, maximum calls, estimated cost, and maximum reserved spend. Click Run to start.
5. Watch progress and inspect each row's query history, candidate evidence, result, and actual cost.
   Pause stops new requests; in-flight requests may finish and incur cost. Cancel prevents new
   requests and publication of canceled results. Resume unfinished work explicitly after restart.
6. Compare KiCad data with downloaded TME parameters and photos. Edit requirements/notes/count,
   paste a replacement TME symbol, approve, or mark not needed. Manual substitutions fetch exact
   product details. Editing a selection or requirements invalidates validation/approval for that
   changed revision; historical decisions remain visible. Workers cannot overwrite newer edits.
7. Download enriched CSV or a project archive containing rows, evidence, history, and costs.
   A CSV alone preserves visible fields/statuses but cannot reconstruct evidence it never held.
   Reimported AI results are not reclassified as original manual symbols. Conflicting legacy
   columns/statuses get an import warning and explicit resolution, never a silent guess.

Approved, manually substituted, original-manual, and not-needed rows are protected from automatic
search replacement by default. An explicit row action can unlock/re-search them. Validation of a
protected selection can add findings without removing the human selection or historical approval.

## Procedure A: one search agent per selected row

Python freezes the row revision and supplies all relevant input fields and existing mapping rules.
The agent returns typed requirements with source fields, exact/minimum distinction, unknowns,
MPN/family interpretation, and short query plans. It can call only these read-only TME operations:

- search_catalog: query of 2–40 characters, verified parameter filters, bounded pages/results;
- discover_parameters: relevant category/package vocabulary;
- get_products: exact returned symbols, product parameters, stock, and PLN price breaks.

Python enforces argument schemas, result limits, caching, timeouts, and API authentication.
Unknown tool names, arbitrary URLs, shell commands, file edits, and purchasing are unavailable.
Product descriptions and CSV cells are untrusted data, not instructions.

Use up to three query attempts, including the corrected recovery query when required. Up to
three pages per query and 300 products per query may be examined; return at most ten compact
candidates to the model, retaining full API evidence locally. Use facets to narrow large result
sets. If truncation prevents a reliable conclusion, return needs_review with incomplete coverage;
never claim exhaustive stock absence or global cheapest pricing from a bounded search.

Python rejects measurable incompatibilities and calculates commercial ranking using Decimal.
The agent assesses semantic compatibility for the shortlist; ranking runs only on technically
eligible candidates. Proposals cite candidate IDs and evidence fields from actual tool results.
If a rejected shortlist hides other eligible candidates, inspect the next bounded shortlist or
report the coverage limit. Preserve exact-footprint alternatives and explicit mismatch notes.

Unknown mappings can be proposed with evidence. They remain row-local until the user accepts them
for reuse; an agent cannot silently alter global compatibility rules for unrelated rows.
Search saves its proposal as candidate_unreviewed. Missing evidence or exhausted budgets produce
needs_review; recovery determines no_safe_match versus no_in_stock_match when coverage suffices.

## Procedure B: one validation agent per selected row

Use a fresh agent context containing the original BOM fields, confirmed policies, the selected
TME product's downloaded evidence, and deterministic comparison results. Do not supply the search
agent's persuasive explanation as evidence. The validator may inspect stored product evidence;
validation does not silently initiate recovery search or change the selected product.

Return a typed checklist for component type, value, footprint/mounting/geometry, polarity/pinout,
explicit technology, each required rating, exact MPN or family membership, and commercial concerns.
Each check is pass, fail, unknown, or not_applicable and includes source references. Python rejects
nonexistent evidence references and forbids validated_match when required checks fail or are unknown.
An unchanged original manual row remains manually_provided unless validation was explicitly requested.

Fresh context reduces anchoring but does not make two calls to the same model independent experts.
Use the same cheap model initially; benchmark a different cheap validator before choosing it.
Human approval is still distinct from AI validation. No numeric model confidence authorizes a match.

Complex ICs, switches, connectors, and custom footprints may need datasheet/pinout evidence absent
from CSV/TME parameters. Version one links documentation and reports the missing check. Automatic
PDF/vision analysis is outside the default cheap procedure and would need a separately approved
extension. Good prompts cannot recover missing electrical requirements or schematic intent.

## Execution and persistence proposal

Reuse Flask and the existing plain-JavaScript review UI; add upload, selection, procedure controls,
and progress polling. Do not introduce a separate frontend build or hosted website service.
Use pydantic-ai-slim[openrouter] for typed agents/tools. Use Python's sqlite3 for one local project
database plus immutable JSON evidence files; CSV remains the input/export format.
SQLite is proposed because multiple workers, budgets, revisions, and run histories require atomic
updates; CSV alone is a poor transaction store. Back up/download the complete project directory.

One Python process owns the UI and a bounded worker pool, initially two row workers. One active
procedure per row, stable row IDs, transactional claims, and revision checks prevent duplicate
execution or overwriting manual edits. Disable Flask's development reloader for workers.
Persist queued/running/completed/failed/canceled/interrupted execution states separately from
technical validation and human review. Restart marks unfinished claims interrupted rather than
automatically retrying potentially billed model calls.

TME authentication and 429 handling are centralized; refresh expiring bearer tokens and apply
bounded backoff. A global credential failure stops dispatch. Other row failures remain visible
without destroying completed rows. Agent result repair and network retries count toward limits.
Unknown-charge timeouts are recorded conservatively rather than retried without accounting.

Keep APP_TOKEN and APP_SECRET; add OPENROUTER_API_KEY to .env.example. Keys stay server-side and
out of logs/exports/model prompts. Retain loopback binding and validate mutation request origins.
No credentialed API calls or paid model calls were made during this investigation.

## Cost proposal

Prices checked against OpenRouter's live model catalog on 2026-11-07:

| Candidate | Input / million tokens | Output / million tokens | Proposed use |
| --- | ---: | ---: | --- |
| qwen/qwen3.5-flash-02-23 | $0.065 | $0.26 | Initial search and validator |
| google/gemini-2.5-flash-lite | $0.10 | $0.40 | Benchmark alternative validator |

Both advertise tools and structured outputs. Endpoint support must be checked at runtime.
Require parameter support and price ceilings; do not allow automatic escalation to expensive
models. Resolve/persist provider and price data for each run. Disable optional reasoning for the
initial baseline where supported and bound all billable output, including reasoning.

Treat the desired $0.01 as search PLUS validation per BOM row, not per physical unit. This is a
stricter target than search alone. Initial envelopes, including repair attempts:

| Procedure | Model responses | Aggregate input | Aggregate output | Qwen estimate |
| --- | ---: | ---: | ---: | ---: |
| Search | 5 maximum | 24,000 tokens | 4,000 tokens | $0.00260 |
| Validation | 2 maximum | 8,000 tokens | 2,000 tokens | $0.00104 |
| Combined | 7 maximum | 32,000 tokens | 6,000 tokens | $0.00364 |

At the alternative model's prices the same combined envelope costs $0.00560.
These are token-envelope calculations, not measured accuracy, latency, or average consumption.
They include repeated context across turns. Compact tool payloads matter more than prompt slogans.
Stock/product API caching and exact-requirement reuse reduce cost; do not reuse across differing
ratings, footprints, quantities affecting ranking, or stale stock. Do not assume cache discounts.

Proposed combined inference allowance is $0.008 per row: $0.006 search and $0.002 validation.
Before each request, atomically reserve an upper-bound request charge from both row and run budgets
using current permitted prices, conservative input accounting, and an explicit output cap.
Fail closed if price/token bounds cannot be established. Reconcile actual usage.cost afterward;
retain reservations for uncertain charges. SDK post-response limits alone cannot enforce a hard
pre-request budget. Paid retries and both stages share the row allowance within a combined run.
Explicit later reruns have a fresh displayed allowance; show lifetime row spending separately.

OpenRouter reports usage/cost and has credit purchase fees: Standard currently 5.5%, minimum $0.80
per purchase. Small top-ups can therefore dominate a small project's effective per-row cost.
Show inference cost separately from estimated purchase-fee allocation. The $0.008 inference cap
leaves room under one cent with ordinary fee allocation, but cannot guarantee an all-in one-cent
cost for arbitrarily small credit purchases, taxes, or hosting.

First measure a 20–30-row human-adjudicated pilot, including easy passives and difficult/manual
cases from both existing BOMs. Historical AI labels are not ground truth. Report false matches,
abstentions, query counts, coverage limits, and mean/p95/max cost. Stop the pilot at a displayed
total limit (proposed $0.25). Do not claim the model is adequate until those results are reviewed.

## Investigation findings and limitations

- The existing 18 tests pass with `uv run python -m unittest discover -s tests -q`.
- Existing saved inputs have 47 and 37 rows, including unresolved mappings, manual parts,
  substitutions, and excluded rows: useful regression fixtures, not certified answers.
- Initial CSV search currently uses Value alone; other symbol properties are not used to form
  searches. Agents should interpret all supplied properties, not invent fields absent from CSV.
- Current search stops at the first API page. Current exact-symbol lookup uses search rather than
  the dedicated exact-product endpoint. Address both while extracting the TME client.
- Current CSV regeneration treats any TME Symbol as manually supplied, including previous AI
  output. Explicit provenance and import handling are required for prior-run CSVs.
- Existing status strings mix execution, machine conclusions, and human review. Preserve the
  visible/exported meanings while storing those dimensions separately.
- Mechanical mappings such as package name plus mounting do not establish pinout equivalence.
  Reuse the mapping data but surface its evidence limits rather than claiming every entry is a
  proof of full compatibility. Exact-MPN suffixes and capacitor height policy need explicit care.
- Existing local modifications and result files were left untouched. No implementation refactor
  or dependency changes are part of this planning pass.

## Approval requested

Approve the retained sourcing policies and the proposed application scope above, particularly:
local Flask UI; per-row search and separate validation; protected manual decisions; evidence-based
unknowns; SQLite project state with CSV import/export; and the displayed spend/call limits.
Implementation begins only after approval. The accompanying implementation plan breaks this into
testable milestones. No unresolved question blocks reviewing this proposal.

## Sources

- Existing requirements: ../../find-tme-parts/SKILL.md and ../../README.md.
- TME current API contract: https://developers.tme.eu/openapi.json
- Model catalog: https://openrouter.ai/api/v1/models
- Qwen pricing: https://openrouter.ai/qwen/qwen3.5-flash-02-23
- Alternative pricing: https://openrouter.ai/google/gemini-2.5-flash-lite
- SDK: https://ai.pydantic.dev/models/openrouter/
- SDK usage limits: https://ai.pydantic.dev/agents/#usage-limits
- Routing: https://openrouter.ai/docs/guides/routing/provider-selection
- Cost accounting: https://openrouter.ai/docs/cookbook/administration/usage-accounting
- Fees: https://openrouter.ai/docs/faq
