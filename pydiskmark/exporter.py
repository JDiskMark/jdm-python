"""Export benchmark results to JSON, YAML, or CSV.

Format is auto-detected from the output file extension:
  .json        → JSON (2-space indent, camelCase keys)
  .yml / .yaml → YAML (via PyYAML)
  .csv         → CSV with # comment header block

All serialisation uses stdlib except YAML which needs pyyaml.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Union

from .benchmark import Benchmark, BenchmarkConfig
from .benchmark_operation import BenchmarkOperation
from .sample import Sample


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def export(benchmark: Benchmark, path: Union[str, Path]) -> None:
    """Write benchmark results to *path* in the format inferred from extension."""
    out = Path(path)
    ext = out.suffix.lower()

    if ext == ".json":
        text = to_json(benchmark)
    elif ext in (".yml", ".yaml"):
        text = to_yaml(benchmark)
    elif ext == ".csv":
        text = to_csv(benchmark)
    else:
        raise ValueError(
            f"Unsupported export format '{ext}'. Use .json, .yml, .yaml, or .csv."
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _sample_dict(s: Sample) -> dict:
    return {
        "sn": s.sample_num,
        "bw": round(s.bw_mb_sec, 4),
        "bt": round(s.cum_avg, 4),      # cumulative avg bandwidth
        "la": round(s.access_time_ms, 4),
        "lt": round(s.cum_acc_time_ms, 4),
        "mn": round(s.cum_min, 4),
        "mx": round(s.cum_max, 4),
    }


def _op_dict(op: BenchmarkOperation) -> dict:
    return {
        "ioMode": op.io_mode.value,
        "blockOrder": op.block_order.value,
        "numSamples": op.num_samples,
        "numBlocks": op.num_blocks,
        "blockSize": op.block_size,
        "numThreads": op.num_threads,
        "bandwidth": round(op.bw_avg, 4),
        "bwMax": round(op.bw_max, 4),
        "bwMin": round(op.bw_min, 4),
        "latency": round(op.acc_avg, 4),
        "iops": op.iops,
        "samples": [_sample_dict(s) for s in op.samples],
    }


def _config_dict(cfg: BenchmarkConfig) -> dict:
    return {
        "appVersion": cfg.app_version,
        "profile": cfg.profile.symbol if cfg.profile else "CUSTOM",
        "benchmarkType": cfg.benchmark_type.value,
        "blockOrder": cfg.block_order.value,
        "numSamples": cfg.num_samples,
        "numBlocks": cfg.num_blocks,
        "blockSizeKb": cfg.block_size // 1024,
        "numThreads": cfg.num_threads,
        "ioEngine": cfg.io_engine.name,
        "directIo": cfg.direct_io_enabled,
        "writeSync": cfg.write_sync_enabled,
        "multiFile": cfg.multi_file_enabled,
    }


def _benchmark_dict(benchmark: Benchmark) -> dict:
    """Convert a Benchmark to a serialisable dict (camelCase, per SPEC §13.1)."""
    d: dict[str, Any] = {
        "_id": benchmark.id,
        "username": benchmark.username,
        "config": _config_dict(benchmark.config),
        "systemInfo": {
            "os": benchmark.system_info.os,
            "arch": benchmark.system_info.arch,
            "processorName": benchmark.system_info.processor_name,
            "runtime": benchmark.system_info.runtime,
            "locationDir": benchmark.system_info.location_dir,
        },
        "driveInfo": {
            "driveModel": benchmark.drive_info.drive_model,
            "partitionId": benchmark.drive_info.partition_id,
            "percentUsed": round(benchmark.drive_info.percent_used, 1),
            "usedGb": round(benchmark.drive_info.used_gb, 2),
            "totalGb": round(benchmark.drive_info.total_gb, 2),
        },
        "startTime": benchmark.start_time.isoformat() if benchmark.start_time else None,
        "endTime": benchmark.end_time.isoformat() if benchmark.end_time else None,
        "operations": [_op_dict(op) for op in benchmark.operations],
    }
    return d


def benchmark_to_dict(benchmark: Benchmark) -> dict:
    """Public alias for _benchmark_dict — used by db.py for persistence."""
    return _benchmark_dict(benchmark)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def to_json(benchmark: Benchmark) -> str:
    """Serialise *benchmark* to a JSON string (2-space indent)."""
    return json.dumps(_benchmark_dict(benchmark), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

def to_yaml(benchmark: Benchmark) -> str:
    """Serialise *benchmark* to a YAML string via PyYAML."""
    try:
        import yaml  # pyyaml
    except ImportError as exc:
        raise ImportError(
            "YAML export requires pyyaml. Install it with: pip install pyyaml"
        ) from exc

    return yaml.dump(
        _benchmark_dict(benchmark),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def to_csv(benchmark: Benchmark) -> str:
    """Serialise *benchmark* samples to CSV with a # comment header block."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    cfg = benchmark.config
    run_date = (
        benchmark.start_time.strftime("%Y-%m-%d %H:%M:%S")
        if benchmark.start_time
        else "unknown"
    )

    # --- Comment header block ---
    comments = [
        f"# pydiskmark {cfg.app_version} Benchmark Summary",
        "# ---------------------------",
        f"# Date: {run_date}",
        f"# Model: {benchmark.drive_info.drive_model}",
        f"# Profile: {cfg.profile.symbol if cfg.profile else 'CUSTOM'}",
        f"# Type: {cfg.benchmark_type.value}",
        f"# Threads: {cfg.num_threads}",
        f"# Order: {cfg.block_order.value}",
        f"# Blocks: {cfg.num_blocks}",
        f"# BlockSize: {cfg.block_size}",
        f"# Samples: {cfg.num_samples}",
    ]
    for op in benchmark.operations:
        comments.append(
            f"# {op.io_mode.value} Result: "
            f"bw {op.bw_avg:.2f} MB/s, lat {op.acc_avg:.2f} ms, iops {op.iops}"
        )
    comments.append("# ---------------------------")

    for line in comments:
        buf.write(line + "\n")

    # --- Column headers + rows ---
    writer.writerow(["sn", "ioMode", "bw", "bt", "la", "lt", "mn", "mx"])
    for op in benchmark.operations:
        for s in op.samples:
            writer.writerow([
                s.sample_num,
                op.io_mode.value,
                f"{s.bw_mb_sec:.4f}",
                f"{s.cum_avg:.4f}",
                f"{s.access_time_ms:.4f}",
                f"{s.cum_acc_time_ms:.4f}",
                f"{s.cum_min:.4f}",
                f"{s.cum_max:.4f}",
            ])

    return buf.getvalue()
