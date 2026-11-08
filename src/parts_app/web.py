"""Local browser UI and atomic HTTP operations; credentials remain server-side."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

from parts_app import tme
from parts_app.procedures import fetch
from parts_app.review import ProductCache, row_review
from parts_app.store import Store
from parts_app.worker import Worker, product_fields


def create_app(path: Path, *, allow_remote: bool = False) -> Flask:
    """Create the local parts application using a portable SQLite project database."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    store = Store(path)
    worker = Worker(store)
    products = ProductCache(lambda *args, **kwargs: fetch(*args, **kwargs))
    app.extensions.update(parts_store=store, parts_worker=worker)

    @app.before_request
    def local_requests():
        if not allow_remote and request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return jsonify(error="Use localhost to access this local application."), 403
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Cross-origin changes are not allowed."), 403
        return None

    @app.errorhandler(tme.TmeError)
    @app.errorhandler(UnicodeDecodeError)
    @app.errorhandler(TypeError)
    @app.errorhandler(KeyError)
    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 422

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        with store.connect() as db:
            runs = [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY rowid DESC")]
            jobs = [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY id DESC")]
            events = [
                dict(row) for row in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 200")
            ]
            rows = [row_review(row, db) for row in store.rows()]
            spend = {}
            for cost in db.execute(
                "SELECT rows.project, events.payload FROM events "
                "JOIN rows ON rows.id=events.row_id WHERE events.kind='model_cost'"
            ):
                project = cost["project"]
                spend[project] = spend.get(project, 0) + json.loads(cost["payload"])["usd"]
        return jsonify(rows=rows, runs=runs, jobs=jobs, events=events, spend=spend)

    @app.get("/api/rows/<row_id>/product")
    def product(row_id):
        return jsonify(product=products.get(store.row(row_id)))

    register_upload(app, store)

    @app.post("/api/runs/preview")
    def preview_run():
        data = request.get_json()
        return jsonify(rows=worker.preview(data["rows"], data["procedure"]))

    @app.post("/api/runs")
    def start():
        data = request.get_json()
        if not isinstance(data, dict):
            raise TypeError("Expected a run request object.")
        return jsonify(run=worker.start(data["rows"], data["procedure"], float(data["allowance"])))

    @app.post("/api/runs/<run_id>/<action>")
    def control(run_id, action):
        worker.control(run_id, action)
        return jsonify(ok=True)

    register_edits(app, store)

    @app.get("/api/export/<project>.csv")
    def export(project):
        content = store.export_csv(project, basket=request.args.get("basket") == "1")
        return Response(
            content,
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="parts.csv"'},
        )

    register_backup(app, store)
    register_mapping(app, store)

    return app


def register_edits(app, store):
    @app.patch("/api/rows/<row_id>")
    def edit(row_id):
        data = request.get_json()
        if not isinstance(data, dict):
            raise TypeError("Expected a row update object.")
        fields = data.get("fields", {})
        row = store.row(row_id)
        if "Final Item Count" in fields and row["selection"]:
            quantity = int(fields["Final Item Count"])
            if quantity < 1:
                raise ValueError("Use Not needed to exclude a row.")
            product = fetch(row["selection"], quantity)
            data["fields"] = fields | product_fields(product)
            data["evidence"] = {"product": product}
        return jsonify(row=store.edit(row_id, int(data["revision"]), data))

    @app.post("/api/rows/<row_id>/substitute")
    def substitute(row_id):
        data = request.get_json()
        row = store.row(row_id)
        product = fetch(str(data["symbol"]).strip(), int(row["fields"]["Final Item Count"]))
        return jsonify(
            row=store.edit(
                row_id,
                int(data["revision"]),
                {
                    "selection": product["symbol"],
                    "evidence": {"product": product},
                    "review": "manually_substituted",
                    "fields": product_fields(product),
                },
            )
        )


def register_mapping(app, store):
    @app.post("/api/rows/<row_id>/mapping")
    def accept_mapping(row_id):
        row = store.row(row_id)
        proposal = row["evidence"].get("proposed_mapping") or {}
        product = row["evidence"].get("product") or {}
        constraints = [item for option in proposal.values() for item in option]
        if not constraints or any("height" in item[1].lower() for item in constraints):
            raise ValueError("No acceptable footprint constraints were proposed.")
        if not tme.has_footprint_options(product.get("parameters", []), proposal):
            raise ValueError("Proposed constraints do not match the downloaded TME parameters.")
        revision = int(request.get_json()["revision"])
        return jsonify(
            row=store.edit(
                row_id, revision, {"evidence": row["evidence"] | {"mapping_approved": True}}
            )
        )


def register_upload(app, store):
    @app.post("/api/import")
    def upload():
        file = request.files.get("file")
        if file is None:
            raise ValueError("Choose a CSV file.")
        content = file.read()
        if request.args.get("preview") == "1":
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                preview = Store(Path(directory) / "preview.sqlite3")
                project = preview.import_csv(content)
                return jsonify(rows=preview.rows(project))
        return jsonify(project=store.import_csv(content))


def register_backup(app, store):
    @app.get("/api/backup")
    def backup():
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "parts.sqlite3"
            with store.connect() as source, sqlite3.connect(target) as destination:
                source.backup(destination)
            content = target.read_bytes()
        return Response(
            content,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="parts.sqlite3"'},
        )


def main():
    """Launch the local UI without a reloader or background automatic resume."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("parts-data/parts.sqlite3"))
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    load_dotenv(args.env_file)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from waitress import serve

    print(f"Parts desk: http://{args.host}:{args.port}")
    serve(
        create_app(args.database, allow_remote=args.host != "127.0.0.1"),
        host=args.host,
        port=args.port,
    )
