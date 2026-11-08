# Evaluation against real, human-reviewed projects

2026-11-07. Reference CSVs were copied byte-for-byte into tests/fixtures; the originals were not
changed. Both imports round-trip all original fields, selections, counts and review states.

## Method

There are 84 input rows: 37 VFD and 47 in the other project. Forty rows have purchased selections
and positive counts: 23 VFD and 17 other. The remaining 44 are not-needed decisions, not evidence
that the associated component is electrically incompatible; they are excluded from positive tests.

Two tests ran against all 40 purchased rows:

- Validate the human-selected symbol against original KiCad properties and downloaded TME data.
- Search without the selected symbol, previous notes, statuses, pricing, or confidence fields;
  then independently validate any proposed component. Original quantities/custom KiCad properties
  remain available. A different symbol is disagreement, not automatically an incorrect substitute.

Five synthetic negatives swapped clearly incompatible products across component families/values.
They are constructed controls, not labels inferred from not-needed rows.

Multiple live rounds exposed technical failures and were followed by targeted reruns. The reports
use the latest completed attempt per dataset/reference, including the SMD-capacitor regression.
The current deterministic publication/score checks were replayed on stored model/API evidence;
this replay performs no model or network calls. Thus the application verdict and capped score in
these reports can differ from a previous live run's original verdict. Raw model scores/findings
remain available. This is a development evaluation on known projects, not a held-out benchmark.

## Results

| Test | VFD | Other project |
| --- | ---: | ---: |
| Purchased rows tested | 23 | 17 |
| Technical execution failures after fixes/reruns | 0 | 0 |
| Blind search proposed a candidate | 18 | 16 |
| Blind search exactly reproduced human symbol | 2 | 7 |
| Blind result still needs review | 17 | 17 |
| Blind result classified no safe match | 6 | 0 |

When revalidating the human selections, current publication rules yielded 31 needs_review and
9 no_safe_match, with zero unconditional validated matches. That is a high review burden and
includes false rejections. It is not evidence that the human selections were bad. Explicit BOM
mismatches, missing mappings, insufficient source citations, and supplier-data problems all
contribute. The system is not ready for unattended selection or confidence-driven ordering.
All five constructed incompatible substitutions were flagged, with reported scores 10–20.
Five negatives are too few to establish a false-acceptance rate.

The representative completed blind search+validation attempts cost $0.038169 total: approximately
$0.000954 per BOM row on average, with a maximum of $0.002154. These exclude earlier debugging
attempts, separately recorded in summary.json, and OpenRouter credit purchase fees. All experiments
combined stayed below the $0.25 conservative allowance, including uncertain reservations.
The cost goal is feasible; answer quality and missing evidence are the present constraints.

## Important findings

1. TME metadata is not always correct. For KF50BDT-TR, the live API reports output current 12mA;
   the original KiCad description says 500mA. ST's manufacturer datasheet explicitly rates the
   regulator for up to 500mA and lists a 12mA maximum quiescent-current condition. The validator
   initially flags the API discrepancy as incompatibility, even though the user successfully used
   this part. This was initially suspected to be a model-reading error; the raw API evidence proved
   the supplier field itself was wrong. No hardcoded per-product correction was added.
   Manufacturer source: https://www.st.com/resource/en/datasheet/kfxx.pdf (pages 1 and 8).
2. The accepted VFD choices include 47uF for a 40uF entry, longer header strips, and a different
   EEPROM. A successful project may include adaptations not represented in its original BOM.
   Strict comparison must surface these differences while preserving human approval. They cannot
   all be counted as model errors or all silently accepted as equivalent.
3. Confidence was initially inflated, including high scores accompanied by failed checks. The
   application now keeps the original model score and displays a conservative checked score:
   technical failures cap it at 20; unresolved technical checks/evidence cap it at 60. These are
   transparent policy caps, not calibrated probabilities. Full model comments/checklists remain.
4. Passives and exact MPNs are easier than mechanical substitutions and custom footprints. Even
   the easier cases currently suffer excessive abstention from incomplete citations/mappings.
5. Historical stock/price fields cannot be used as current availability labels. Fresh TME lookups
   showed shortages and an unavailable price; these are separate from technical compatibility.

## Technical fixes made during evaluation

- Exclude previous result/answer columns from all agent requirement inputs.
- Keep all original human decisions and manual-selection provenance through validation/reimport.
- Select by bounded candidate IDs instead of requiring models to transcribe supplier symbols.
- Normalize explicit model abstentions such as the JSON string "None" into null.
- Compact parameters under their actual names; preserve original raw API snapshots.
- Check citations against the same evidence object the agent actually received.
- Validate technical suitability even when current pricing is unavailable.
- Separate commercial failures from electrical/mechanical mismatches.
- Parse real BOM notation including 40m, 4,7k, micro/nano units and kilo/mega distinctions.
- Prioritize numeric value matches before forming the model shortlist; reject explicit SMD/THT
  and chip-case conflicts. Deterministic value mismatches override optimistic model output.
- Serialize model requests, add bounded 429 backoff and one schema-repair attempt, and reconcile
  known failed-call costs. Unused per-request reservations no longer consume the row allowance.
- Stop a stale combined search before it can validate/publish over a newer human edit.
- Add explicit --host binding and permit remote hosts only when remote binding was requested,
  while preserving mutation-origin checks. The running instance binds 0.0.0.0:5050.

## Artifacts and reproduction

- validation.csv: each human-selected part, model comment, score, uncertainties and current verdict.
- blind-search.csv: independent selections compared with human selections.
- summary.json: counts and measured inference costs, including all development attempts.
- Local parts-data/evaluation*.jsonl: full API/model evidence, checkpoints and attempts.

```bash
uv run python scripts/evaluate_reviewed.py --phase validate --output parts-data/my-validation.jsonl \
  --env-file /path/to/.env
uv run python scripts/evaluate_reviewed.py --phase both --output parts-data/my-search.jsonl \
  --env-file /path/to/.env
```

Evaluation scripts make paid calls only when explicitly run. They checkpoint completed rows and
bound row and experiment spending. Automated pytest tests are entirely offline.

Next useful work is explicit project overrides for accepted substitutions and targeted manufacturer
checks when distributor data conflicts with requirements. Bigger generic prompts alone will not
correct supplier metadata or reconstruct undocumented project decisions.
