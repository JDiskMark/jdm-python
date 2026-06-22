"""SQLite persistence for pydiskmark benchmark results.

Schema
------
  benchmarks     — one row per benchmark run (metadata only, no sample data)
  benchmark_ops  — one row per operation, FK → benchmarks.id

Sample data for each operation is stored as a JSON file on disk:
  ~/.pdm/<version>/ops/<benchmark_uuid>_<IoMode>.json

This mirrors jdiskmark's BenchmarkOperation @Lob pattern: samples live on
the operation, not on the benchmark.  The benchmark table holds only the
fields that belong to Benchmark.java (driveInfo, systemInfo, config, times).

DB location: ~/.pdm/<version>/pdm.db
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _db_dir() -> Path:
    import pydiskmark.app as app
    root = Path.home() / ".pdm" / app.VERSION
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_path() -> Path:
    return _db_dir() / "pdm.db"


def _ops_dir() -> Path:
    d = _db_dir() / "ops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _op_file(benchmark_id: str, io_mode: str) -> Path:
    """Return the Path for an operation's sample-data JSON file.

    Example: ~/.pdm/0.1.0/ops/abc123_Write.json
    """
    safe_mode = io_mode.replace(" ", "_").replace("&", "and")
    return _ops_dir() / f"{benchmark_id}_{safe_mode}.json"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS benchmarks (
    id             TEXT PRIMARY KEY,
    drive_model    TEXT,
    partition_id   TEXT,
    profile        TEXT,
    benchmark_type TEXT,
    start_time     TEXT,
    elapsed_ms     INTEGER,
    username       TEXT,
    metadata_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_ops (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_id  TEXT    NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
    io_mode       TEXT,
    block_order   TEXT,
    num_samples   INTEGER,
    num_blocks    INTEGER,
    block_size_kb INTEGER,
    num_threads   INTEGER,
    lat_avg_ms    REAL,
    iops          INTEGER,
    bw_mb_sec     REAL,
    data_file     TEXT    NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_benchmark(benchmark) -> None:
    """Persist *benchmark* to the DB and write per-operation sample files."""
    from .exporter import _benchmark_dict, _op_dict, _config_dict

    d = _benchmark_dict(benchmark)

    benchmark_id = str(benchmark.id)
    drive_model  = d["driveInfo"]["driveModel"]
    partition_id = d["driveInfo"]["partitionId"]
    profile      = d["config"].get("profile", "")
    b_type       = d["config"].get("benchmarkType", "")
    start_time   = d.get("startTime", "")

    elapsed_ms: Optional[int] = None
    if benchmark.start_time and benchmark.end_time:
        elapsed_ms = int(
            (benchmark.end_time - benchmark.start_time).total_seconds() * 1000
        )

    # Compact metadata JSON (no operations/samples — those live in op files)
    metadata = {
        "_id":        d["_id"],
        "username":   d["username"],
        "systemInfo": d["systemInfo"],
        "driveInfo":  d["driveInfo"],
        "config":     d["config"],
        "startTime":  d.get("startTime", ""),
        "endTime":    d.get("endTime", ""),
    }
    metadata_json = json.dumps(metadata)

    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO benchmarks
              (id, drive_model, partition_id, profile, benchmark_type,
               start_time, elapsed_ms, username, metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                benchmark_id, drive_model, partition_id, profile, b_type,
                start_time, elapsed_ms, benchmark.username, metadata_json,
            ),
        )

        for op in benchmark.operations:
            op_d = _op_dict(op)
            io_mode_str = op_d["ioMode"]

            # Write the operation's sample data to its own file
            op_path = _op_file(benchmark_id, io_mode_str)
            op_path.write_text(json.dumps(op_d), encoding="utf-8")

            # Store only the relative path (relative to db dir) for portability
            rel_path = op_path.relative_to(_db_dir()).as_posix()

            conn.execute(
                """
                INSERT INTO benchmark_ops
                  (benchmark_id, io_mode, block_order, num_samples, num_blocks,
                   block_size_kb, num_threads, lat_avg_ms, iops, bw_mb_sec, data_file)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    benchmark_id,
                    io_mode_str,
                    op_d["blockOrder"],
                    op_d["numSamples"],
                    op_d["numBlocks"],
                    op_d["blockSize"] // 1024,
                    op_d["numThreads"],
                    op_d["latency"],
                    op_d["iops"],
                    op_d["bandwidth"],
                    rel_path,
                ),
            )


# ---------------------------------------------------------------------------
# Load history (for the history Treeview)
# ---------------------------------------------------------------------------

def load_history() -> list[dict]:
    """Return rows for the history Treeview, newest benchmark first.

    JOINs benchmarks ← benchmark_ops so each op row gets the benchmark-level
    display columns without duplication in the DB.
    """
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    bo.id,
                    bo.benchmark_id,
                    b.drive_model,
                    b.profile,
                    b.benchmark_type,
                    bo.io_mode,
                    bo.block_order,
                    bo.num_samples,
                    bo.num_blocks,
                    bo.block_size_kb,
                    bo.num_threads,
                    b.start_time,
                    b.elapsed_ms,
                    bo.lat_avg_ms,
                    bo.iops,
                    bo.bw_mb_sec
                FROM benchmark_ops bo
                JOIN benchmarks b ON b.id = bo.benchmark_id
                ORDER BY b.start_time DESC, bo.id ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Load benchmark metadata (for chart replay UI restoration)
# ---------------------------------------------------------------------------

def load_benchmark(benchmark_id: str) -> Optional[dict]:
    """Return the metadata dict for *benchmark_id* (no sample data)."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM benchmarks WHERE id = ?",
                (benchmark_id,),
            ).fetchone()
        if row:
            return json.loads(row["metadata_json"])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_benchmark(benchmark_id: str) -> None:
    """Delete a benchmark and all its operations by benchmark UUID.

    ON DELETE CASCADE on benchmark_ops ensures op rows are removed too.
    Also removes per-operation sample JSON files from disk.
    """
    try:
        # Collect op file paths before deleting rows
        with _connect() as conn:
            rows = conn.execute(
                "SELECT data_file FROM benchmark_ops WHERE benchmark_id = ?",
                (benchmark_id,),
            ).fetchall()
            op_files = [_db_dir() / r["data_file"] for r in rows]
            conn.execute("DELETE FROM benchmarks WHERE id = ?", (benchmark_id,))

        # Remove sample data files (best-effort)
        for f in op_files:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def delete_all_benchmarks() -> None:
    """Delete every benchmark and all associated operations."""
    try:
        with _connect() as conn:
            # Collect all op file paths
            rows = conn.execute(
                "SELECT data_file FROM benchmark_ops"
            ).fetchall()
            op_files = [_db_dir() / r["data_file"] for r in rows]
            conn.execute("DELETE FROM benchmarks")

        for f in op_files:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Load operation list for chart replay
# ---------------------------------------------------------------------------

def load_benchmark_ops(benchmark_id: str) -> list[dict]:
    """Return op rows for *benchmark_id*, Write-first then Read.

    Each dict has the benchmark_ops columns plus a resolved absolute
    'data_file_path' key pointing to the operation JSON file.
    """
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, benchmark_id, io_mode, block_order, num_samples,
                       num_blocks, block_size_kb, num_threads, lat_avg_ms,
                       iops, bw_mb_sec, data_file
                FROM benchmark_ops
                WHERE benchmark_id = ?
                ORDER BY CASE io_mode WHEN 'Write' THEN 0 ELSE 1 END, id
                """,
                (benchmark_id,),
            ).fetchall()

        db_dir = _db_dir()
        result = []
        for r in rows:
            d = dict(r)
            d["data_file_path"] = str(db_dir / d["data_file"])
            result.append(d)
        return result
    except Exception:
        return []


def load_op_data(data_file_path: str) -> Optional[dict]:
    """Load and return the operation JSON dict from *data_file_path*."""
    try:
        return json.loads(Path(data_file_path).read_text(encoding="utf-8"))
    except Exception:
        return None
