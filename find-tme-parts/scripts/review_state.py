"""Persistent CSV state for the local TME BOM review application."""

from __future__ import annotations

import csv
import math
import os
import sys
import tempfile
from argparse import ArgumentTypeError
from contextlib import contextmanager
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from tme_parts import ADDED, REQUIRED, TmeError, positive

FINAL_COUNT = "Final Item Count"

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def final_path(input_path: Path) -> Path:
    """Return the sibling final CSV path for an AI-processed CSV."""
    return input_path.with_name(f"{input_path.stem}-final{input_path.suffix}")


def load_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read a BOM CSV after validating its required fields and quantities."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            headers = [str(header) for header in reader.fieldnames or []]
            missing = [column for column in REQUIRED if column not in headers]
            if missing:
                raise TmeError(f"CSV is missing required column(s): {', '.join(missing)}.")
            rows = []
            for raw_row in reader:
                if None in raw_row:
                    raise TmeError(f"CSV {path} has more values than its header.")
                rows.append({str(key): str(value or "") for key, value in raw_row.items()})
    except OSError as error:
        raise TmeError(f"Could not read CSV {path}: {error}") from error
    if not rows:
        raise TmeError(f"CSV {path} has no data rows.")
    for row_number, row in enumerate(rows, start=2):
        try:
            positive(row["Qty"])
        except (KeyError, TypeError, ValueError, ArgumentTypeError) as error:
            raise TmeError(f"CSV row {row_number} has invalid Qty.") from error
    return rows, headers


def initial_count(quantity: str) -> int:
    """Calculate the initial order count with the requested spare-part rule."""
    requested = positive(quantity)
    return max(requested + 1, math.ceil(requested * 1.15))


def initialize_final_csv(input_path: Path) -> Path:
    """Create the final CSV once and initialize blank final count values."""
    output_path = final_path(input_path)
    with csv_lock(output_path):
        source_path = output_path if output_path.exists() else input_path
        rows, headers = load_rows(source_path)
        for column in (*ADDED, FINAL_COUNT):
            if column not in headers:
                headers.append(column)
        changed = not output_path.exists()
        for row in rows:
            if not row.get(FINAL_COUNT, "").strip():
                row[FINAL_COUNT] = str(initial_count(row["Qty"]))
                changed = True
        if changed:
            write_rows(output_path, rows, headers)
    return output_path


def update_row(path: Path, index: int, updates: Mapping[str, str]) -> dict[str, str]:
    """Apply a validated set of column values to one final CSV row."""
    with csv_lock(path):
        rows, headers = load_rows(path)
        if not 0 <= index < len(rows):
            raise TmeError(f"Row index {index} does not exist.")
        unknown = sorted(set(updates) - set(headers))
        if unknown:
            raise TmeError(f"Final CSV has no column(s): {', '.join(unknown)}.")
        rows[index].update(updates)
        write_rows(path, rows, headers)
        return rows[index]


@contextmanager
def csv_lock(path: Path):
    """Serialize final CSV updates across concurrent local web requests."""
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        acquire_lock(lock_file)
        try:
            yield
        finally:
            release_lock(lock_file)


def acquire_lock(lock_file: TextIO) -> None:
    """Acquire a whole-file advisory lock using the host platform's stdlib API."""
    if sys.platform == "win32":
        lock_file.write("0")
        lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def release_lock(lock_file: TextIO) -> None:
    """Release the advisory lock acquired by `acquire_lock`."""
    if sys.platform == "win32":
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_rows(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    """Atomically replace a CSV file while preserving its declared column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        Path(temporary_name).replace(path)
    except OSError as error:
        Path(temporary_name).unlink(missing_ok=True)
        raise TmeError(f"Could not write final CSV {path}: {error}") from error
