# TME CSV Review App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Flask app that turns an enriched TME CSV into a persistent, editable final BOM.

**Architecture:** A focused CSV state module owns final-file initialization and safe updates. A Flask application combines that state with the existing TME client for row details and replacements; a self-contained template, stylesheet, and JavaScript client render the review experience.

**Tech Stack:** Python 3.13, Flask 3.1.2, uv, unittest, ruff, ty.

## Global Constraints

- Bind only to `127.0.0.1`; never expose credentials in HTML, JSON, or errors.
- Persist all app state in a sibling `<stem>-final.csv`; use atomic CSV replacements.
- Initialize `Final Item Count` with `max(Qty + 1, ceil(Qty * 1.15))`, but never overwrite it once non-empty.
- Reuse the existing TME API authentication and request helpers.
- Keep lines to 100 characters, public non-trivial APIs documented, and functions below 100 lines.

---

### Task 1: Project setup and final CSV state

**Files:**
- Create: `pyproject.toml`
- Create: `find-tme-parts/scripts/review_state.py`
- Modify: `tests/test_tme_parts.py`

**Interfaces:**
- Produces: `final_path(input_path: Path) -> Path`, `initialize_final_csv(input_path: Path) -> Path`, `load_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]`, and `update_row(path: Path, index: int, updates: Mapping[str, str]) -> dict[str, str]`.

- [ ] **Step 1: Write failing state tests**

```python
def test_initialize_final_csv_adds_default_count_once(self) -> None:
    final = review_state.initialize_final_csv(input_path)
    self.assertEqual(read_rows(final)[0]["Final Item Count"], "15")
    read_rows(final)[0]["Final Item Count"] = "99"
    write_rows(final, read_rows(final))
    review_state.initialize_final_csv(input_path)
    self.assertEqual(read_rows(final)[0]["Final Item Count"], "99")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_tme_parts.FinalCsvTests -v`

Expected: FAIL because `review_state` does not exist.

- [ ] **Step 3: Add exact Flask project metadata and minimal state implementation**

```toml
[project]
name = "find-tme-parts"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flask==3.1.2"]
```

Implement CSV header validation, count initialization, and atomic write through a
temporary sibling file, then replace it with `Path.replace`.

- [ ] **Step 4: Run state tests and create the lockfile**

Run: `uv lock && uv run python -m unittest tests.test_tme_parts.FinalCsvTests -v`

Expected: PASS.

### Task 2: Normalize TME product details

**Files:**
- Create: `find-tme-parts/scripts/review_tme.py`
- Modify: `tests/test_tme_parts.py`

**Interfaces:**
- Consumes: `tme_parts.details`, `tme_parts.params`, `tme_parts.order_qty`, and `tme_parts.unit_price`.
- Produces: `fetch_product(symbol: str, quantity: int, token: str) -> dict[str, object]`.

- [ ] **Step 1: Write a failing normalized-product test**

```python
def test_fetch_product_includes_summary_links_and_current_price(self) -> None:
    with mock.patch.object(review_tme, "product_records", return_value=records):
        product = review_tme.fetch_product("ABC-1", 12, "token")
    self.assertEqual(product["manufacturer_part_number"], "MFG-1")
    self.assertEqual(product["datasheet_url"], "https://example.test/data.pdf")
    self.assertEqual(product["line_total_pln"], "6.000000")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_tme_parts.TmeReviewTests -v`

Expected: FAIL because `review_tme` does not exist.

- [ ] **Step 3: Implement the TME boundary and normalization**

Use `/products/parameters` and `/products/data` through `tme_parts.details`.
Normalize description, parameter name/value pairs, product URL, datasheet URL when
present, stock, current unit price, and quantity-based line total. Reject blank
symbols and missing product records with actionable `TmeError` messages.

- [ ] **Step 4: Run the normalized-product tests**

Run: `uv run python -m unittest tests.test_tme_parts.TmeReviewTests -v`

Expected: PASS.

### Task 3: Flask API and replacement persistence

**Files:**
- Create: `find-tme-parts/scripts/review_parts.py`
- Modify: `tests/test_tme_parts.py`

**Interfaces:**
- Consumes: `review_state` and `review_tme.fetch_product`.
- Produces: `create_app(final_csv: Path, env_file: Path) -> Flask` and a CLI accepting `input_csv`, `--port`, and `--env-file`.

- [ ] **Step 1: Write failing API tests**

```python
def test_replacement_updates_only_after_valid_tme_response(self) -> None:
    app = review_parts.create_app(final_path, env_file)
    with mock.patch.object(review_parts, "fetch_product", return_value=product):
        response = app.test_client().post("/api/rows/0/substitution", json={"symbol": "NEW"})
    self.assertEqual(response.status_code, 200)
    self.assertEqual(read_rows(final_path)[0]["TME Match Status"], "manually_substituted")

def test_failed_replacement_leaves_final_csv_unchanged(self) -> None:
    app = review_parts.create_app(final_path, env_file)
    with mock.patch.object(review_parts, "fetch_product", side_effect=TmeError("not found")):
        response = app.test_client().post("/api/rows/0/substitution", json={"symbol": "BAD"})
    self.assertEqual(response.status_code, 422)
```

- [ ] **Step 2: Run API tests to verify they fail**

Run: `uv run python -m unittest tests.test_tme_parts.ReviewAppTests -v`

Expected: FAIL because the Flask app does not exist.

- [ ] **Step 3: Implement endpoints and CLI**

Implement `GET /api/rows`, `GET /api/rows/<index>/details`, `PATCH /api/rows/<index>/count`, and `POST /api/rows/<index>/substitution`. Validate JSON, indexes, and positive counts. Fetch a replacement before updating the final CSV, calculate its line total from `Final Item Count`, and write replacement fields plus `manually_substituted` status.

- [ ] **Step 4: Run API tests**

Run: `uv run python -m unittest tests.test_tme_parts.ReviewAppTests -v`

Expected: PASS.

### Task 4: Responsive review interface

**Files:**
- Create: `find-tme-parts/templates/review.html`
- Create: `find-tme-parts/static/review.css`
- Create: `find-tme-parts/static/review.js`
- Modify: `find-tme-parts/scripts/review_parts.py`
- Modify: `tests/test_tme_parts.py`

**Interfaces:**
- Consumes: the JSON API from Task 3.
- Produces: a selectable BOM table, details panel, count editor, and replacement form.

- [ ] **Step 1: Write a failing page test**

```python
def test_review_page_serves_the_application_shell(self) -> None:
    response = app.test_client().get("/")
    self.assertEqual(response.status_code, 200)
    self.assertIn(b"Final Item Count", response.data)
    self.assertIn(b"Part details", response.data)
```

- [ ] **Step 2: Run page test to verify it fails**

Run: `uv run python -m unittest tests.test_tme_parts.ReviewAppTests.test_review_page_serves_the_application_shell -v`

Expected: FAIL because the route/template is absent.

- [ ] **Step 3: Implement the UI**

Render the page shell with accessible labels and status updates. Use CSS custom
properties, strong spacing, status chips, responsive grid behavior, focus styles,
and non-color status cues. In JavaScript, load rows, render a selected row, fetch
details on selection, show links conditionally, patch counts, and submit a pasted
replacement symbol with errors displayed next to the form.

- [ ] **Step 4: Run page and API tests**

Run: `uv run python -m unittest tests.test_tme_parts.ReviewAppTests -v`

Expected: PASS.

### Task 5: Documentation and quality checks

**Files:**
- Modify: `README.md`
- Modify: `DEV.md`
- Modify: `find-tme-parts/SKILL.md`

- [ ] **Step 1: Document launch, workflow, and CSV behavior**

Add the exact `uv run --project <skill-path> ... review_parts.py AI_RESULTS.csv`
command, explain final-file naming and persistence, and document replacement
validation and the final count rule.

- [ ] **Step 2: Run focused verification**

Run: `uv run python -m unittest discover -s tests -q && uv run ruff check . && uv run ruff format --check . && uv run ty check`

Expected: all commands exit 0 without warnings.

- [ ] **Step 3: Inspect the app manually**

Run: `uv run find-tme-parts/scripts/review_parts.py VFD-disp-tme-results.csv --port 5050`

Expected: a loopback-only server starts, initializes `VFD-disp-tme-results-final.csv`, and the browser UI loads at `http://127.0.0.1:5050`.
