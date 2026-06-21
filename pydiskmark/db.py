"""SQLite persistence for pydiskmark benchmark results.

DB location: ~/.pdm/<version>/pdm.db

Schema: one row per benchmark operation so the history table
matches jdm-java's "Benchmark Operations" view.
Each row stores the full JSON payload for chart reload.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# DB location
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    import pydiskmark.app as app
    root = Path.home() / ".pdm" / app.VERSION
    root.mkdir(parents=True, exist_ok=True)
    return root / "pdm.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS benchmark_ops (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      TEXT    NOT NULL,
    drive_model   TEXT,
    partition_id  TEXT,
    profile       TEXT,
    benchmark_type TEXT,
    io_mode       TEXT,
    block_order   TEXT,
    num_samples   INTEGER,
    num_blocks    INTEGER,
    block_size_kb INTEGER,
    num_threads   INTEGER,
    start_time    TEXT,
    elapsed_ms    INTEGER,
    lat_avg_ms    REAL,
    iops          INTEGER,
    bw_mb_sec     REAL,
    data_json     TEXT    NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_benchmark(benchmark) -> None:
    """Persist all operations from *benchmark* to the DB."""
    from .exporter import benchmark_to_dict

    d = benchmark_to_dict(benchmark)
    data_json = json.dumps(d)

    group_id = str(benchmark.id)
    drive_model = d["driveInfo"]["driveModel"]
    partition_id = d["driveInfo"]["partitionId"]
    profile = d["config"]["profile"]
    benchmark_type = d["config"]["benchmarkType"]
    start_time = d.get("startTime", "")

    elapsed_ms: Optional[int] = None
    if benchmark.start_time and benchmark.end_time:
        elapsed_ms = int(
            (benchmark.end_time - benchmark.start_time).total_seconds() * 1000
        )

    with _connect() as conn:
        for op in d.get("operations", []):
            conn.execute(
                """
                INSERT INTO benchmark_ops
                  (group_id, drive_model, partition_id, profile, benchmark_type,
                   io_mode, block_order, num_samples, num_blocks, block_size_kb,
                   num_threads, start_time, elapsed_ms, lat_avg_ms, iops,
                   bw_mb_sec, data_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    group_id, drive_model, partition_id, profile, benchmark_type,
                    op["ioMode"],
                    op["blockOrder"],
                    op["numSamples"],
                    op["numBlocks"],
                    op["blockSize"] // 1024,
                    op["numThreads"],
                    start_time,
                    elapsed_ms,
                    op["latency"],
                    op["iops"],
                    op["bandwidth"],
                    data_json,
                ),
            )


# ---------------------------------------------------------------------------
# Load history (for treeview)
# ---------------------------------------------------------------------------

def load_history() -> list[dict]:
    """Return a list of row dicts for the history Treeview, newest first."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, drive_model, profile, benchmark_type, io_mode,
                       block_order, num_samples, num_blocks, block_size_kb,
                       num_threads, start_time, elapsed_ms, lat_avg_ms,
                       iops, bw_mb_sec
                FROM benchmark_ops
                ORDER BY id DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Load full benchmark JSON for chart replay
# ---------------------------------------------------------------------------

def load_benchmark_json(row_id: int) -> Optional[dict]:
    """Return the full benchmark dict for a given history row ID."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT data_json FROM benchmark_ops WHERE id = ?", (row_id,)
            ).fetchone()
        if row:
            return json.loads(row["data_json"])
    except Exception:
        pass
    return None
