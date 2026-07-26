# Find TME Parts

This repository contains a Codex skill and Python helper for finding in-stock electronic
components on TME for delivery to Poland. It searches TME, verifies KiCad footprint
constraints, accounts for minimum order quantities and order multiples, and ranks candidates
by payable line cost.

## Requirements

- Python 3.10 or newer
- TME API credentials: `APP_TOKEN` and `APP_SECRET`

Credentials can be exported in the environment or placed in a local `.env` file. Start from
the included [.env.example](.env.example). Never commit real credentials.

## Usage

The helper is at `find-tme-parts/scripts/tme_parts.py`:

```bash
python3 find-tme-parts/scripts/tme_parts.py search \
  --phrase '10k ohm resistor 1% 0.1W' \
  --footprint 0402 \
  --qty 25
```

The command prints ranked, in-stock candidates as JSON. Other commands are:

```bash
# Inspect TME package vocabulary for a component family
python3 find-tme-parts/scripts/tme_parts.py discover-footprint \
  --phrase '10k resistor 1%'

# Verify constraints and append a reusable KiCad-to-TME mapping
python3 find-tme-parts/scripts/tme_parts.py map-footprint \
  --kicad-footprint 'Resistor_SMD:R_0805_2012Metric' \
  --phrase '10k resistor 1%' \
  --constraint 'Case - inch=0805' \
  --constraint 'Mounting=SMD' \
  --notes '0805 SMD chip resistor'

# Enrich a BOM CSV with TME results
python3 find-tme-parts/scripts/tme_parts.py csv input.csv \
  --output tme-results.csv
```

For numeric dimensions, use `--numeric-constraint`, for example
`--numeric-constraint 'Diameter=5'`.

## BOM CSV format

CSV input must contain `Reference`, `Value`, `Footprint`, and `Qty` columns. Other columns are
preserved. `Qty` is the requested total; the helper adjusts the order quantity for TME minimums
and multiples.

Rows without a verified footprint mapping are marked `needs_review`. Results marked
`candidate_unreviewed` must be checked against the returned TME description and parameters
before ordering. The helper does not approve a substitute with an unconfirmed footprint.

## Repository layout

```text
find-tme-parts/
├── SKILL.md                         # Agent workflow and selection rules
├── agents/openai.yaml               # Skill metadata for Codex
├── footprint-translations.csv       # Verified KiCad-to-TME mappings
└── scripts/tme_parts.py              # TME API helper and CLI
tests/test_tme_parts.py              # Behavior tests for footprint mappings
```

Run the tests with:

```bash
python3 -m unittest discover -s tests -q
```

API failures, authentication errors, and missing credentials stop the helper with an actionable
error. Credentials are never printed by the helper.
