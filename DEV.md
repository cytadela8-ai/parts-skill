# Technical notes

`src/parts_app` replaces the old skill and scripts. The original mapping behavior is retained in
`tme.py`; it owns OAuth, bounded paginated searches, exact lookup and product normalization.
`agents.py` owns typed structured JSON calls and prompts. `procedures.py` owns row search,
independent validation, and pre-request spending reservations. `store.py` owns SQLite transactions,
CSV import/export, stable IDs and optimistic revisions. `worker.py` owns explicit job dispatch,
pause/cancel/resume and publication. `web.py` exposes those operations to the plain-JS browser UI.

The SQLite database is portable and contains rows, machine verdicts, human review states, jobs,
run limits, reservations and evidence events. It contains no credentials. Original BOM columns are
stored as JSON and exported alongside current selections and validation fields. Never use reference
strings or visible positions as row identities: grouped/duplicate references are valid.

Worker results publish only against their frozen revision. Human edits increment it. Separate human
review and machine verdict columns prevent a validator from granting human approval. Successful
searches in combined runs are persisted before validation; phase/revision data permit resumption.
The current dispatch lock serializes runs; this deliberately prioritizes controlled TME access over
throughput. The executor is bounded and does not spawn an agent orchestrator.

No model gets arbitrary URLs, filesystem access, credentials, shell tools or purchasing tools.
Python executes the finite search plan and supplies actual TME results. Prompt contents are data;
explicit schemas and deterministic checks guard publication. Confidence is kept even when checklist
coverage fails. Complete checklists and real source paths are required for validated_match; existence
of a cited field is not a mathematical proof that the model interpreted it correctly.

Pydantic AI's OpenRouter native structured output was exercised live. Qwen's endpoint rejected
forced tool output, and native output did not validate in the smoke test, so the shipping default
is Gemini 2.5 Flash Lite. SDK retries and automatic provider fallback are disabled. Failed calls keep
reservations if billing cannot be established. Model calls reserve an upper bound using UTF-8 byte
counts plus schema/instruction margin, capped output and provider price ceilings. OpenRouter's cost
metadata reconciles completed calls. The SDK incorrectly types fractional max_price fields as int;
a narrowly documented type suppression preserves the actual fractional API values.

Supplier reads use a bounded 15-minute cache. Model requests have bounded rate-limit retries
and one schema repair; uncertain charges remain reserved. Browsing a CSV does not establish
stock freshness. Manual count changes fetch current pricing before publication. Basket export
requires explicit approval. Manufacturer PDF/pinout analysis and global mapping editing remain
outside the app's current scope.

Tests mock model/network boundaries. Historical BOMs, live evaluation costs and compatibility
limitations are documented in the evaluation report. Exact human-symbol agreement alone does
not establish correctness, and accepted substitutions may rely on unstated project context.

Real-data evaluation and fixes are documented in docs/evaluation/README.md. Parameters sent to
models are keyed by their actual names; raw API records remain unchanged. Candidate IDs prevent
part-number transcription errors. Original manual provenance is persisted in evidence and the
Selection Origin export column. Schema repair and 429 retries are bounded and cost-accounted.
