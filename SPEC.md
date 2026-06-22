# pydiskmark — Implementation Specification

> **Source of truth:** `jdm-java` (`smart` branch) as of 2026-06-20.
> This document describes the **behaviour** of the Java implementation so that a
> functionally-equivalent Python port can be built without referencing the Java
> source directly.

---

## 1. Overview

**pydiskmark** is a cross-platform disk benchmark utility. It measures sustained
sequential and random I/O performance and reports bandwidth (MB/s), latency
(ms/block), and IOPS.

The tool runs in two modes:
- **CLI** — primary mode; invoked with `python -m pydiskmark run`.
- **GUI** — desktop interface built with Tkinter + matplotlib; launch with `python -m pydiskmark gui`.

---

## 2. Terminology

| Term | Meaning |
|---|---|
| **Benchmark** | A single top-level run, may contain one WRITE and/or one READ operation. |
| **Operation** | One directed I/O phase — either WRITE or READ — within a benchmark. |
| **Sample** | A single timed measurement unit: writes/reads `numBlocks` blocks and records elapsed time. |
| **Block** | The atomic I/O unit; size is configurable (e.g. 512 KB, 4 KB). |
| **txSize** | Total KB transferred by one operation = `blockSizeKb × numBlocks × numSamples`. |
| **Bandwidth** | MB/s = bytes-transferred / elapsed-seconds. |
| **Latency** | Average time per block in milliseconds = `elapsed_ns / 1_000_000 / numBlocks`. |
| **IOPS** | Blocks (ops) per second across the entire operation = `totalOps / elapsed_sec`. |

---

## 3. Constants & Units

```
KILOBYTE = 1_024          # bytes
MEGABYTE = 1_024 * 1_024  # bytes
GIGABYTE = 1_024 * 1_024 * 1_024
APP_NAME  = "pydiskmark"
DATADIRNAME = "pdm-data"  # sub-directory created inside the chosen location
PROPERTIES_FILENAME = "pdm.properties"
```

Binary units (powers of 2) are used throughout — never decimal SI units.

---

## 4. Enumerations

### 4.1 `BenchmarkType`
```
READ       — read-only benchmark (files written silently in a prep phase)
WRITE      — write-only benchmark
READ_WRITE — write phase followed by a read phase on the same data
```

### 4.2 `IOMode`
```
READ
WRITE
```

### 4.3 `BlockSequence`
```
SEQUENTIAL — blocks accessed in order 0, 1, 2 … N-1
RANDOM     — each block index chosen via randint(0, numBlocks-1)
```

### 4.4 `IoEngine`
```
MODERN — positional I/O via os.pwrite / os.pread (POSIX) or
          CreateFileW / WriteFile (Windows), with optional Direct I/O
          (O_DIRECT on Linux/macOS; FILE_FLAG_NO_BUFFERING on Windows).
          Aligned buffers are required when Direct I/O is active.
```

### 4.5 `SectorAlignment`
```
NONE    — OS chooses buffer alignment
ALIGN_512   — 512-byte alignment
ALIGN_4K    — 4 096-byte alignment   ← default for MODERN engine
ALIGN_8K    — 8 192-byte alignment
ALIGN_16K   — 16 384-byte alignment
ALIGN_64K   — 65 536-byte alignment
```

When `MODERN` engine is used with Direct I/O, the write/read buffer **must**
be aligned to the selected value. In Python use `bytearray` allocated via
`mmap` or a ctypes buffer with the required alignment.

---

## 5. Data Model

### 5.1 `BenchmarkConfig` (parameter snapshot)

Captured once at benchmark start; immutable during a run.

| Field | Type | Default | Notes |
|---|---|---|---|
| `app_version` | str | from version file | |
| `profile` | BenchmarkProfile | QUICK_TEST | |
| `profile_modified` | bool | False | |
| `benchmark_type` | BenchmarkType | WRITE | |
| `block_order` | BlockSequence | SEQUENTIAL | |
| `num_blocks` | int | 32 | blocks per sample |
| `block_size` | int | bytes (e.g. 512*1024) | `block_size_kb * KILOBYTE` |
| `num_samples` | int | 200 | |
| `num_threads` | int | 1 | |
| `tx_size` | int | KB | `block_size_kb * num_blocks * num_samples` |
| `io_engine` | IoEngine | MODERN | |
| `direct_io_enabled` | bool | False | |
| `write_sync_enabled` | bool | False | |
| `sector_alignment` | SectorAlignment | ALIGN_4K | |
| `multi_file_enabled` | bool | True | |
| `test_dir` | str | path | absolute path to data directory |

Helper predicates:
```python
def has_write_operation(self) -> bool:
    return self.benchmark_type in (BenchmarkType.WRITE, BenchmarkType.READ_WRITE)

def has_read_operation(self) -> bool:
    return self.benchmark_type in (BenchmarkType.READ, BenchmarkType.READ_WRITE)
```

### 5.2 `BenchmarkSystemInfo`

Captured once at benchmark start from the runtime environment.

| Field | Type | Notes |
|---|---|---|
| `os` | str | `platform.system()` |
| `arch` | str | `platform.machine()` |
| `processor_name` | str | CPU brand string |
| `runtime` | str | `"Python x.y.z"` replaces Java's `jdk` field |
| `location_dir` | str | absolute path of the test location |

### 5.3 `BenchmarkDriveInfo`

| Field | Type | Notes |
|---|---|---|
| `drive_model` | str | human-readable device model |
| `partition_id` | str | drive letter (Windows) or partition path (Linux/macOS) |
| `percent_used` | int | 0–100 |
| `used_gb` | float | |
| `total_gb` | float | |

### 5.4 `Sample`

One timed I/O measurement.

| Field | JSON key | Type | Notes |
|---|---|---|---|
| `type` | — | IOMode | not serialised |
| `sample_num` | `sn` | int | 1-based sample index |
| `bw_mb_sec` | `bw` | float | bandwidth for this sample (MB/s) |
| `cum_avg` | `bt` | float | running cumulative average bandwidth |
| `cum_max` | `mx` | float | running cumulative maximum |
| `cum_min` | `mn` | float | running cumulative minimum |
| `access_time_ms` | `la` | float | latency: elapsed_ns/1e6 / num_blocks |
| `cum_acc_time_ms` | `lt` | float | running cumulative average latency |

All float fields are rounded to 4 decimal places in JSON/YAML/CSV output.

### 5.5 `BenchmarkOperation`

Aggregated results of one I/O phase.

| Field | Type | Notes |
|---|---|---|
| `io_mode` | IOMode | READ or WRITE |
| `block_order` | BlockSequence | |
| `num_blocks` | int | |
| `block_size` | int | bytes |
| `num_samples` | int | |
| `tx_size` | int | KB |
| `num_threads` | int | |
| `write_sync_enabled` | bool \| None | None for READ operations |
| `start_time` | datetime | set on construction |
| `end_time` | datetime \| None | set after all samples complete |
| `samples` | list[Sample] | all samples in insertion order |
| `bw_avg` | float | final average bandwidth (MB/s) |
| `bw_max` | float | |
| `bw_min` | float | |
| `acc_avg` | float | final average latency (ms) |
| `iops` | int | total blocks / elapsed seconds (see §8.4) |

Display helpers:
```python
def get_mode_display(self) -> str:
    if self.io_mode == IOMode.WRITE and self.write_sync_enabled:
        return "Write*"
    return self.io_mode.value

def get_duration_ms(self) -> int | None:
    if self.end_time is None:
        return None
    return int((self.end_time - self.start_time).total_seconds() * 1000)
```

### 5.6 `Benchmark`

Top-level container for a complete benchmark run.

| Field | JSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | str (UUID4) | primary key; string in JSON |
| `username` | — | str | `os.getlogin()` or `"anonymous"` |
| `system_info` | — | BenchmarkSystemInfo | |
| `drive_info` | — | BenchmarkDriveInfo | |
| `config` | — | BenchmarkConfig | |
| `start_time` | — | datetime | set by `record_start_time()` |
| `end_time` | — | datetime \| None | set by `record_end_time()` |
| `operations` | — | list[BenchmarkOperation] | 1 or 2 elements |

Result text output format:
```
-------------------------------------------
pydiskmark Benchmark Results (vX.Y)
-------------------------------------------
Profile: <name>
Benchmark: <type>
Drive: <model>
Capacity: <percent>% (<used>/<total> GB)
Timestamp: <start_time>
CPU: <processor_name>
System: <os> / <arch>
Runtime: <runtime_string>
Path: <location_dir>
-------------------------------------------
Order: SEQUENTIAL|RANDOM
IOMode: Read|Write
Thread(s): N
Blocks(size): N(B)
Samples: N
TxSize(KB): N
Speed(MB/s): N.NN
SpeedMin(MB/s): N.NN
SpeedMax(MB/s): N.NN
Latency(ms): N.NN
IOPS: N
-------------------------------------------
```

---

## 6. Pre-defined Profiles (`BenchmarkProfile`)

Each profile is a named constant with fixed defaults. CLI users can override
individual parameters without switching profiles.

| Symbol | Name | Type | Order | Threads | Samples | Blocks | BlockKB | Direct | Sync | Alignment | MultiFile |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `QUICK_TEST` | Quick Test | READ_WRITE | SEQ | 1 | 50 | 32 | 1024 | Yes | No | 4K | No |
| `MAX_THROUGHPUT` | Max Throughput | READ_WRITE | SEQ | 1 | 100 | 256 | 1024 | Yes | No | 4K | No |
| `HIGH_LOAD_RANDOM_T32` | Random 4K (T32) | READ_WRITE | RAND | 32 | 200 | 128 | 4 | Yes | No | 4K | Yes |
| `LOW_LOAD_RANDOM_T1` | Random 4K (T1) | READ_WRITE | RAND | 1 | 150 | 64 | 4 | Yes | No | 4K | No |
| `MAX_WRITE_STRESS` | Max Write Stress (T4) | WRITE | SEQ | 4 | 250 | 512 | 512 | Yes | Yes | 4K | Yes |
| `MEDIA_PLAYBACK` | Media Playback | READ | SEQ | 1 | 160 | 64 | 2048 | Yes | No | 4K | No |
| `VIDEO_EXPORTING` | Video Exporting | WRITE | SEQ | 4 | 500 | 128 | 1024 | Yes | No | 4K | No |
| `PHOTO_LIBRARY` | Photo Library | READ | RAND | 8 | 1000 | 8 | 128 | Yes | No | 4K | Yes |

---

## 7. Test File Layout

```
<location_dir>/
└── pdm-data/               ← DATADIRNAME
    ├── testdata.pdm         ← single-file mode
    └── testdata0.pdm        ← multi-file mode (one per sample)
        testdata1.pdm
        …
        testdataN.pdm
```

File path selection (per sample):
```python
def get_test_file(sample_num: int, config: BenchmarkConfig) -> Path:
    if config.multi_file_enabled:
        return Path(config.test_dir) / f"testdata{sample_num}.pdm"
    return Path(config.test_dir) / "testdata.pdm"
```

---

## 8. Core Benchmark Algorithm

### 8.1 Thread Range Partitioning

Samples are divided evenly across threads. Remainder samples are distributed
one-per-thread to the leading threads.

```python
def divide_into_ranges(start_index: int, end_index: int, num_threads: int) -> list[tuple[int,int]]:
    """Returns list of (start, end) exclusive ranges, one per thread."""
    n = end_index - start_index
    ranges = []
    range_size, remainder = divmod(n, num_threads)
    start = start_index
    for i in range(num_threads):
        end = start + range_size + (1 if remainder > 0 else 0)
        remainder = max(0, remainder - 1)
        ranges.append((start, end))
        start = end
    return ranges
```

### 8.2 `BenchmarkRunner.execute()` — Top-Level Orchestration

```
1. Compute total units (for progress bar):
   blocks_per_phase = num_blocks × num_samples
   w_units = blocks_per_phase  if has_write OR is READ (prep phase reuses write counter)
   r_units = blocks_per_phase  if has_read
   units_total = w_units + r_units

2. Query drive info (model, partition, disk usage).

3. Construct Benchmark object; populate system/drive info.

4. Partition sample range into thread ranges:
   start = next_sample_number (1-based, global counter)
   end   = start + num_samples
   ranges = divide_into_ranges(start, end, num_threads)

5. record_start_time()

6. Execution:
   a. If has_write:  run_operation(WRITE, ranges)
   b. Else (READ-only): run_read_preparation(ranges)  # writes files silently

7. Force progress to 100 % (throttled_progress_update(force=True))

8. Cache drop (if not cancelled AND has_read AND not direct_io OR (direct_io AND macOS)):
   call listener.attempt_cache_drop()

9. If has_read AND not cancelled: run_operation(READ, ranges)

10. record_end_time()

11. Return benchmark object.
```

### 8.3 `run_operation(mode, ranges)` — Single I/O Phase

```
1. Create BenchmarkOperation for this mode (copy params from config).

2. Launch one thread per range.

3. Per thread, for each sample index s in range:
   a. Create Sample(type=mode, sample_num=s)
   b. Perform I/O (measure_write or measure_read)
   c. update_metrics(sample)  — update global running stats
   d. Update op cumulative stats: bw_max, bw_min, bw_avg, acc_avg
   e. op.samples.append(sample)
   f. Increment write_units_complete or read_units_complete (thread-safe)
   g. listener.on_sample_complete(sample)
   h. throttled_progress_update()

4. Wait for all threads to finish.

5. Set op.end_time; compute op.iops (see §8.4).
```

### 8.4 IOPS Calculation

```python
def set_total_ops(op: BenchmarkOperation, total_ops: int) -> None:
    elapsed_ns = (op.end_time - op.start_time).total_seconds() * 1e9
    if elapsed_ns > 0:
        op.iops = round(total_ops / (elapsed_ns / 1e9))
```

`total_ops` is the sum of blocks completed across all threads for this mode
(i.e. `write_units_complete` or `read_units_complete`).

### 8.5 Cumulative Metrics (`update_metrics`)

Called after each sample, before notifying the listener.

```python
# For WRITE samples:
if w_max == -1 or sample.bw_mb_sec > w_max: w_max = sample.bw_mb_sec
if w_min == -1 or sample.bw_mb_sec < w_min: w_min = sample.bw_mb_sec

n = sample.sample_num
if w_avg == -1:
    w_avg = sample.bw_mb_sec
else:
    w_avg = ((n - 1) * w_avg + sample.bw_mb_sec) / n

if w_acc == -1:
    w_acc = sample.access_time_ms
else:
    w_acc = ((n - 1) * w_acc + sample.access_time_ms) / n

sample.cum_avg = w_avg
sample.cum_max = w_max
sample.cum_min = w_min
sample.cum_acc_time_ms = w_acc
# Mirror for READ using r_* variables
```

### 8.6 Progress Throttling

Progress updates to the listener are rate-limited:

```python
UPDATE_INTERVAL_MS = 25

def throttled_progress_update(force: bool = False) -> None:
    now_ms = time.monotonic_ns() // 1_000_000
    elapsed = now_ms - last_update_ms
    completed = write_units_complete + read_units_complete
    if force or elapsed >= UPDATE_INTERVAL_MS:
        percent = int(completed / units_total * 100)
        percent = max(0, min(100, percent))
        listener.on_progress_update(percent, 100)
        last_update_ms = now_ms
```

---

## 9. I/O Measurement (`Sample`)

### 9.1 WRITE — Modern Engine (`measure_write`)

```
Open file with: WRITE | CREATE
                + DSYNC if write_sync_enabled
                + O_DIRECT if direct_io_enabled (Linux/macOS only)

Allocate aligned buffer of block_size bytes (alignment = sector_alignment.bytes,
or default system alignment if NONE).

For b in range(num_blocks):
    if cancelled: break
    block_index = randint(0, num_blocks-1) if RANDOM else b
    byte_offset = block_index * block_size
    pwrite(fd, buffer, byte_offset)  or equivalent positional write
    total_bytes_written += block_size
    runner.update_write_progress()

elapsed_ns = end_ns - start_ns
access_time_ms = (elapsed_ns / 1e6) / num_blocks
bw_mb_sec = (total_bytes_written / MEGABYTE) / (elapsed_ns / 1e9)
```

### 9.2 READ — Modern Engine (`measure_read`)

```
Open file with: READ
                + O_DIRECT if direct_io_enabled

For b in range(num_blocks):
    if cancelled: break
    block_index = randint(0, num_blocks-1) if RANDOM else b
    byte_offset = block_index * block_size
    pread(fd, buffer, byte_offset)
    total_bytes_read += block_size
    runner.update_read_progress()

elapsed_ns = end_ns - start_ns
access_time_ms = (elapsed_ns / 1e6) / num_blocks
bw_mb_sec = (total_bytes_read / MEGABYTE) / (elapsed_ns / 1e9)
```

### 9.3 READ Preparation (`prepare_read`)

Used when `benchmark_type == READ` (no prior WRITE phase). Writes sequential
data to the test file *without* timing or recording bandwidth. Uses the write
units counter so the progress bar reflects preparation work.

```
Open file with: WRITE | CREATE | TRUNCATE

For b in range(num_blocks):
    if cancelled: break
    byte_offset = b * block_size
    pwrite(fd, buffer, byte_offset)
    runner.update_write_progress()
```

---

## 10. OS-Specific Behaviours

### 10.1 Cache Drop

Must be attempted before the READ phase of a `READ` or `READ_WRITE` benchmark
(to prevent reads hitting the page cache).

| OS | Privileged | Action |
|---|---|---|
| Linux | root | `sync` then `echo 1 > /proc/sys/vm/drop_caches` |
| Linux | non-root | Print instructions; block until user presses Enter |
| macOS | root | `sync; sudo purge` |
| macOS | non-root | Print instructions; block until user presses Enter |
| Windows | admin | Run `EmptyStandbyList.exe` from install dir |
| Windows | non-admin | Print instructions; block until user presses Enter |

Skip cache drop entirely when:
- Direct I/O is enabled **and** OS is not macOS (kernel bypasses page cache).
- Benchmark was cancelled.

### 10.2 Drive Model Detection

| OS | Method |
|---|---|
| Linux | `lsblk -o NAME,MODEL`, resolve via `/sys/block/<dev>/device/model` |
| macOS | `diskutil info <device>` → `Device / Media Name` |
| Windows | WMI query `Win32_DiskDrive` → `Model`, mapped via drive letter |

### 10.3 Partition / Drive Letter

| OS | Field |
|---|---|
| Linux | `/proc/mounts` or `df` → resolves symlinks to `/dev/sdX` or `/dev/nvmeXnY` |
| macOS | `df` then `diskutil info` |
| Windows | Extract drive letter from path root (`C`, `D`, …) |

### 10.4 Disk Usage

| OS | Command |
|---|---|
| Linux / macOS | `df -k <path>` — parse `1K-blocks`, `Used`, `Use%` columns |
| Windows | WMI `Win32_LogicalDisk` or `GetDiskFreeSpaceEx` |

Result fields: `percent_used`, `used_gb`, `total_gb`.

### 10.5 Processor Name

| OS | Method |
|---|---|
| Linux | Parse `/proc/cpuinfo` → `model name` |
| macOS | `sysctl -n machdep.cpu.brand_string` |
| Windows | `WMIC CPU get Name` or `winreg` → `HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0\ProcessorNameString` |

### 10.6 Direct I/O

| OS | Support |
|---|---|
| Linux | `os.O_DIRECT` flag; buffer must be sector-aligned |
| macOS | `fcntl.F_NOCACHE` via `fcntl(fd, fcntl.F_NOCACHE, 1)` |
| Windows | `FILE_FLAG_NO_BUFFERING` via `CreateFile` (ctypes or pywin32) |

If Direct I/O open fails, fall back silently to buffered I/O and log a warning.

### 10.7 Write Sync

| OS | Mechanism |
|---|---|
| Linux | Open with `O_SYNC` or `O_DSYNC`; alternatively `os.fsync(fd)` per write |
| macOS | Same as Linux |
| Windows | `FILE_FLAG_WRITE_THROUGH` via `CreateFile` |

---

## 11. Configuration Persistence

Settings are stored in a Java `.properties`-style flat-text file:

```
~/.pdm/<version>/pdm.properties
```

Format: `key=value`, one per line, `#` comment lines.

| Key | Default | Notes |
|---|---|---|
| `activeProfile` | `QUICK_TEST` | |
| `profileModified` | `false` | |
| `benchmarkType` | `WRITE` | |
| `blockSequence` | `SEQUENTIAL` | |
| `numOfSamples` | `200` | |
| `numOfBlocks` | `32` | |
| `blockSizeKb` | `512` | |
| `numOfThreads` | `1` | |
| `ioEngine` | `MODERN` | |
| `writeSyncEnable` | `false` | |
| `directEnable` | `false` | |
| `sectorAlignment` | `ALIGN_4K` | |
| `multiFile` | `true` | |
| `autoRemoveData` | `true` | |
| `autoReset` | `true` |

> **Note:** The Python port does not need to replicate GUI-only properties
> (`theme`, `palette`, `showMaxMin`, `showDriveAccess`, `showSingleOp`,
> `sharePortal`, `uploadResourceLocator`, `uploadProtocol`).

---

## 12. CLI Interface

Entry point: `python -m pydiskmark run [OPTIONS]`

### 12.1 Sub-command: `run`

| Option | Short | Type | Default | Description |
|---|---|---|---|---|
| `--profile` | `-p` | str | `QUICK_TEST` | Named profile |
| `--type` | `-t` | str | profile default | `READ`, `WRITE`, `READ_WRITE` |
| `--threads` | `-T` | int | profile default | Number of concurrent threads |
| `--order` | `-o` | str | profile default | `SEQUENTIAL`, `RANDOM` |
| `--blocks` | `-b` | int | profile default | Blocks per sample |
| `--block-size` | `-z` | int | profile default | Block size in KB |
| `--samples` | `-n` | int | profile default | Number of samples |
| `--direct` | `-d` | flag | False | Enable Direct I/O |
| `--write-sync` | `-y` | flag | False | Enable write-sync |
| `--alignment` | `-a` | str | `NONE` | Sector alignment |
| `--multi-file` | `-m` | flag | False | One file per sample |
| `--location` | `-l` | path | `$HOME` | Directory for test files |
| `--export` | `-e` | path | None | Export results to JSON file |
| `--save` | `-s` | flag | False | Persist to local database |
| `--clean` | `-c` | flag | False | Delete existing data dir first |
| `--verbose` | `-v` | flag | False | Verbose logging |

**Override precedence:** explicit CLI option > profile default.

### 12.2 Execution Flow (CLI)

```
1. Load profile defaults into App state.
2. Apply any explicit CLI overrides.
3. Set location_dir; derive data_dir = location_dir / "pdm-data".
4. Validate location_dir is writable.
5. If --clean and data_dir exists: delete recursively.
6. Create data_dir if not present.
7. init() — collect OS/CPU info.
8. Print progress bar during benchmark.
9. Print result text after completion.
10. Export JSON if --export specified.
11. Save to DB if --save specified.
12. Remove data_dir if auto_remove_data is True.
```

### 12.3 Progress Bar (CLI)

```
Progress: [##########          ]  50% (50/100 units)
```

- Length: 50 characters of `#` / space.
- Rendered with `\r` (carriage return, no newline) at `UPDATE_INTERVAL = 25 ms`.
- Cursor hidden during run (`\x1b[?25l`), restored after (`\x1b[?25h`).

---

## 13. Export Formats

### 13.1 JSON (`.json`)

Full serialisation of the `Benchmark` object tree using the JSON field names
defined in §5. Pretty-printed, 2-space indent.

```json
{
  "_id": "a1b2c3d4-e5f6-...",
  "username": "james",
  "config": { ... },
  "systemInfo": { ... },
  "driveInfo": { ... },
  "startTime": "2026-06-20T14:30:00",
  "endTime": "2026-06-20T14:31:30",
  "operations": [
    {
      "ioMode": "WRITE",
      "samples": [
        { "sn": 1, "bw": 523.4, "bt": 523.4, "la": 0.95, "lt": 0.95, "mn": 523.4, "mx": 523.4 }
      ],
      "bandwidth": 523.4,
      "latency": 0.95,
      "iops": 12345
    }
  ]
}
```

### 13.2 YAML (`.yml`)

Same structure as JSON, emitted as YAML without `---` document-start marker.

### 13.3 CSV (`.csv`)

Flat table of samples with metadata header as `#` comment lines.

```
# pydiskmark x.y Benchmark Summary
# ---------------------------
# Date: 2026-06-20 14:30:00
# Model: Samsung 990 Pro
# Profile: Quick Test
# Type: READ_WRITE
# Threads: 1
# Order: SEQUENTIAL
# Blocks: 32
# BlockSize: 524288
# Samples: 50
# WRITE Result: bw 523.40 MB/s, lat 0.95 ms, iops 12345
# READ Result: bw 610.20 MB/s, lat 0.82 ms, iops 14321
# ---------------------------

sn,ioMode,bw,bt,la,lt,mn,mx
1,WRITE,523.4,523.4,0.95,0.95,523.4,523.4
...
```

---

## 14. Local Database (optional — `--save`)

The Java version uses Apache Derby (embedded SQL) with JPA/Hibernate. For the
Python port use **SQLite** (`sqlite3` stdlib) as the embedded database.

Schema tables: `benchmark`, `benchmark_operation`.

- `benchmark.id` — UUID string (primary key)
- `benchmark_operation.benchmark_id` — foreign key

The database file lives at:
```
~/.pdm/<version>/pdm.db
```

Operations to implement: `save`, `find_all`, `delete_all`, `delete_by_ids`.

---

## 15. Listener / Callback Interface

`BenchmarkRunner` is decoupled from output via a listener protocol:

```python
class BenchmarkListener(Protocol):
    def on_sample_complete(self, sample: Sample) -> None: ...
    def on_progress_update(self, completed: int, total: int) -> None: ...
    def is_cancelled(self) -> bool: ...
    def attempt_cache_drop(self) -> None: ...
```

The CLI implementation of `attempt_cache_drop` calls the OS-specific cache
drop logic (§10.1) and blocks until the user confirms or completes.

---

## 16. Application State

These globals are equivalent to Java's static `App.*` fields.
In Python, encapsulate in an `AppState` dataclass or module-level variables.

| Variable | Type | Default | Notes |
|---|---|---|---|
| `location_dir` | Path | None | where test files go |
| `data_dir` | Path | None | `location_dir / "pdm-data"` |
| `export_path` | Path | None | |
| `auto_save` | bool | False | persist to DB |
| `verbose` | bool | False | |
| `multi_file` | bool | True | |
| `auto_remove_data` | bool | True | delete data dir after run |
| `auto_reset` | bool | True | reset running stats before run |
| `direct_enable` | bool | False | |
| `write_sync_enable` | bool | False | |
| `io_engine` | IoEngine | MODERN | |
| `sector_alignment` | SectorAlignment | ALIGN_4K | |
| `active_profile` | BenchmarkProfile | QUICK_TEST | |
| `profile_modified` | bool | False | |
| `benchmark_type` | BenchmarkType | WRITE | |
| `block_sequence` | BlockSequence | SEQUENTIAL | |
| `num_of_samples` | int | 200 | |
| `num_of_blocks` | int | 32 | |
| `block_size_kb` | int | 512 | |
| `num_of_threads` | int | 1 | |
| `next_sample_number` | int | 1 | global monotonic counter |
| `os` | str | — | from platform |
| `arch` | str | — | from platform |
| `processor_name` | str | — | |
| `username` | str | — | from os |

Running stats (reset between benchmarks when `auto_reset = True`):

```python
w_max = w_min = w_avg = w_acc = w_iops = -1.0
r_max = r_min = r_avg = r_acc = r_iops = -1.0
```

---

## 17. Module Structure (Suggested)

```
jdm-python/
├── pydiskmark/
│   ├── __init__.py
│   ├── __main__.py          ← entry point: python -m pydiskmark
│   ├── app.py               ← global state, init(), get_config()
│   ├── benchmark.py         ← Benchmark, BenchmarkConfig, BenchmarkSystemInfo,
│   │                           BenchmarkDriveInfo, BenchmarkType, IOMode,
│   │                           BlockSequence, IoEngine, SectorAlignment
│   ├── benchmark_operation.py
│   ├── benchmark_profile.py ← enum of pre-defined profiles
│   ├── benchmark_runner.py  ← BenchmarkRunner, BenchmarkListener
│   ├── cli.py               ← argparse entry point, CliListener, results printer
│   ├── exporter.py          ← JSON / YAML / CSV export
│   ├── io_engine.py         ← cross-platform Direct I/O: alloc_aligned, open_file,
│   │                           pwrite, pread, close_file, free_aligned
│   ├── sample.py            ← Sample, measure_write, measure_read, prepare_read
│   ├── util.py              ← randint, delete_directory, etc.
│   └── util_os.py           ← OS-specific: drive model, cache drop, disk usage,
│                               processor name, partition id
├── tests/
│   ├── test_phase1.py
│   ├── test_phase2.py
│   └── test_phase3.py
├── pyproject.toml
├── README.md
└── SPEC.md                  ← this file
```

---

## 18. Key Differences from Java — Python Implementation Notes

| Java concern | Python equivalent |
|---|---|
| `ExecutorService.newFixedThreadPool(N)` | `concurrent.futures.ThreadPoolExecutor(N)` |
| `LongAdder` / `AtomicLong` | `threading.Lock` + `int`, or `threading.local` partial sums |
| `RandomAccessFile` (legacy) | removed — only MODERN engine is implemented |
| `FileChannel` + `MemorySegment` (modern) | `os.open()` + `os.pwrite()` / `os.pread()` |
| `ExtendedOpenOption.DIRECT` | `os.O_DIRECT` (Linux); `fcntl.F_NOCACHE` (macOS) |
| `StandardOpenOption.DSYNC` | `os.O_DSYNC` (Linux/macOS) or `os.fsync()` per write |
| JPA/Derby database | `sqlite3` (stdlib) |
| Jackson JSON serialiser | `json` stdlib or `dataclasses-json` |
| `System.nanoTime()` | `time.perf_counter_ns()` |
| GC detection / hints | Not applicable in Python — removed from model |
| `picocli` | `argparse` or `click` |
| Single-instance lock (`FileLock`) | Not required for CLI-only port |

---

## 19. Out of Scope

- SMART data collection (`Smart.java`, `SmartPanel.java`)
- Community portal upload (`Portal.java`)
- Windows MSI / Linux DEB / macOS PKG packaging

---

## 20. GUI — Desktop Interface (Phase 5)

> **Status:** Implemented. Launch with `python -m pydiskmark gui`.

### 20.1 Overview

The GUI provides a visual desktop interface that replicates the Java Swing
frontend's layout and functionality. It uses the same `BenchmarkRunner` /
`BenchmarkListener` pipeline as the CLI — no changes to the engine layer.

### 20.2 Toolkit Decision

**Chosen: Tkinter + matplotlib + sv-ttk**

| Factor | Decision |
|---|---|
| Toolkit | `tkinter` (stdlib) — zero runtime cost, ships with Python |
| Theme | `sv-ttk` (Sun Valley) — modern flat dark/light appearance |
| Chart | `matplotlib` via `FigureCanvasTkAgg` — dual-axis, real-time, well-packaged |
| Threading | Queue + `root.after(50ms)` polling — all Tkinter mutations on main thread |

Additional runtime dependencies: `matplotlib>=3.7`, `sv-ttk>=2.5`.

### 20.3 Layout

```
┌────────────────────────────────────────────────────────────────┐
│  Menu: File | Action | Options | Help                          │
├───┬────────────────────────────────────────────────────────────┤
│ D │  Drives tab selected:                                      │
│ r │    DrivesPanel fills the ENTIRE content area               │
│ i │    (drive selector, info card, all-drives table, test dir) │
│ v │                                                            │
│ e │  Benchmark tab selected:                                   │
│ s │  ┌──────────────┬──────────────────────────────────────┐  │
│   │  │ ControlPanel │          ChartPanel                  │  │
│ B │  │ (320 px)     │  (matplotlib dual-axis, fills rest)  │  │
│ e │  │ settings /   │                                      │  │
│ n │  │ start/stop / │                                      │  │
│ c │  │ metrics grid │                                      │  │
│ h │  └──────────────┴──────────────────────────────────────┘  │
├───┴────────────────────────────────────────────────────────────┤
│  [Benchmark Operations] [Events]                               │
│   HistoryPanel — treeview of past operations                   │
├────────────────────────────────────────────────────────────────┤
│  Status text          [progress bar]  Total Tx (KB): N         │
└────────────────────────────────────────────────────────────────┘
```

### 20.4 Module Structure

```
pydiskmark/
├── db.py                    ← SQLite persistence (Phase 5)
└── gui/
    ├── __init__.py          ← launch_gui() entry point
    ├── theme.py             ← sv_ttk dark/light + chart colour palette
    ├── listener.py          ← GuiListener — queue-based BenchmarkListener
    ├── chart_panel.py       ← matplotlib FigureCanvasTkAgg, dual-axis
    ├── control_panel.py     ← Benchmark tab — settings combos + results grid
    ├── drives_panel.py      ← Drives tab — drive list, info card, dir chooser
    ├── history_panel.py     ← Benchmark Operations tab — DB history treeview
    └── main_window.py       ← MainWindow orchestrator
```

### 20.5 Left-Tab Panels

#### Drives Tab (`DrivesPanel`)
- Top: drive selector dropdown (Drive letter — capacity)
- Left sub-panel: Drive Info card
  - Drive model, partition, usage percentage, used/total GB
  - Access indicators: Read ✓ / Write ✓
  - Usage progress bar
- Right sub-panel: All Drives table
  - Columns: Drive/Mount | Total (GB) | Used (GB) | Free (GB) | Usage %
  - Click a row → update drive info card + set `app.location_dir`
- Bottom: Test Directory path display + Browse button

#### Benchmark Tab (`ControlPanel`)
- Profile, Type, Threads, Block Order, Blocks/Sample, Block Size (KB), Samples — dropdowns
- Start / Stop button (toggles, disables combos during run)
- Results grid: 3-column (Metric | Write | Read) for MB/s, Lat (ms), IOPS

### 20.6 Chart (`ChartPanel`)

- Left Y-axis: Bandwidth (MB/s)
- Right Y-axis: Latency (ms)
- X-axis: Sample number
- Series:
  - Write BW — solid orange line
  - Write Avg — dashed orange line
  - Write Latency — small orange square markers (right axis)
  - Read BW — solid cyan line
  - Read Avg — dashed cyan line
  - Read Latency — small cyan square markers (right axis)
- Batched redraws every 3rd sample to avoid lag at high sample counts
- `set_title(str)` — updates the chart suptitle with drive info
- `retheme()` — reapply colours after dark/light toggle

### 20.7 Threading Model

```
Worker thread (BenchmarkRunner)
  └─ GuiListener.on_sample_complete()  ──→  queue.put((EVT_SAMPLE, sample))
  └─ GuiListener.on_progress_update()  ──→  queue.put((EVT_PROGRESS, %, %))
  └─ GuiListener.attempt_cache_drop()  ──→  queue.put((EVT_CACHE_DROP, event))
                                             blocks until main thread sets event
  └─ _run_worker() completes           ──→  queue.put((EVT_COMPLETE, benchmark))

Main thread (Tkinter)
  └─ _poll_queue() every 50 ms via root.after()
       drains queue → updates chart, progress bar, metrics labels
       on EVT_COMPLETE → saves to DB, refreshes history, re-enables controls
```

After the worker thread dies, `_poll_queue` performs one extra drain to
catch `EVT_COMPLETE` posted just before thread exit (eliminates the
"Benchmark cancelled" false-positive race condition).

### 20.8 Persistence (`db.py`)

**DB location:** `~/.pdm/<version>/pdm.db` (SQLite, stdlib `sqlite3`).

**Schema** — one row per benchmark operation:

```sql
CREATE TABLE benchmark_ops (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      TEXT,    -- UUID shared by ops from the same run
    drive_model   TEXT,
    partition_id  TEXT,
    profile       TEXT,
    benchmark_type TEXT,
    io_mode       TEXT,    -- WRITE | READ
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
    data_json     TEXT     -- full benchmark JSON for chart replay
);
```

Benchmarks are **auto-saved** after each successful run. Double-clicking a
row in the "Benchmark Operations" history tab replays that benchmark in the
chart without re-running I/O.

### 20.9 Benchmark Execution Flow (GUI)

```
1. User selects drive (Drives tab) — sets location_dir
2. User selects profile / adjusts settings (Benchmark tab)
3. User clicks Start (or Ctrl+R)
4. GUI disables controls, clears chart, resets metrics
5. BenchmarkRunner.execute() runs in a daemon thread
6. on_sample_complete() → queue → chart.add_sample() every 50 ms
7. on_progress_update() → queue → progress bar update
8. attempt_cache_drop() → queue → modal dialog, blocks worker until dismissed
9. EVT_COMPLETE → db.save_benchmark() → history.refresh() → re-enable controls
10. Elapsed time shown in status bar; drive info refreshed
```

### 20.10 Export from GUI

- **File → Export**: file dialog → JSON / YAML / CSV (reuses `exporter.export()`)
- **Double-click history row**: replay historical benchmark in chart (no re-run)

### 20.11 Design Principles

- Left-tab layout matches jdm-java (Drives | Benchmark vertical tabs)
- Dark mode by default via sv_ttk; Options → Toggle Theme switches to light
- Keyboard shortcuts: `Ctrl+R` (start), `Esc` (stop)
- About dialog centred on the parent window (`transient + grab_set`)
- Window title: `pydiskmark <version> — <arch> — <CPU>`
- Chart suptitle: `<drive model> — <partition>: <pct>% (<used>/<total> GB)`

### 20.12 Benchmark Tab — Control Panel Grid Layout

The `ControlPanel` widget uses a **3-column Tkinter grid** that replicates
jdiskmark's Swing layout. The container frame has a fixed `width=320` px
(`pack_propagate(False)`).

#### Column definitions

| Col | Role | Weight | Effect |
|-----|------|--------|--------|
| 0 | Narrow label anchor (Profile / Type rows only) | 0 (fixed) | `minsize=60 px` — holds the short "Profile" / "Type" label text |
| 1 | Middle — label overflow + combo left-edge | 2 | Receives ~40 % of the expandable space |
| 2 | Combo-only column (rows 3–7) | 3 | Receives ~60 % of the expandable space |

Columns 1 and 2 together fill all space beyond col 0's 60 px minimum.
The 2 : 3 weight ratio produces a 40 % : 60 % split, matching jdiskmark.

#### Row spanning rules

```
Row │ Widget          │ Col span (label) │ Col span (combo)
────┼─────────────────┼──────────────────┼──────────────────
 0  │ Profile         │ col 0 only       │ cols 1+2, sticky="ew"
 1  │ Type            │ col 0 only       │ cols 1+2, sticky="ew"
 2  │ Threads         │ cols 0+1         │ col 2 only, sticky="ew"
 3  │ Block Order     │ cols 0+1         │ col 2 only, sticky="ew"
 4  │ Blocks / Sample │ cols 0+1         │ col 2 only, sticky="ew"
 5  │ Block Size (KB) │ cols 0+1         │ col 2 only, sticky="ew"
 6  │ Samples         │ cols 0+1         │ col 2 only, sticky="ew"
 7  │ Start button    │ cols 0+1+2 (columnspan=3), sticky="ew"
 8  │ Separator       │ cols 0+1+2 (columnspan=3), sticky="ew"
 9  │ Results frame   │ cols 0+1+2 (columnspan=3), sticky="ew"
```

#### Visual result

```
 Col 0 (≥60 px) │  Col 1 (40 %)  │  Col 2 (60 %)  │
────────────────┼────────────────────────────────────┤
 Profile        │  [Profile combo ── spans 1+2 ──]  ▼│
 Type           │  [Type combo ──── spans 1+2 ──]  ▼│
 [Threads ─── spans 0+1 ──────]  │  [  1  ]       ▼│
 [Block Order ─ spans 0+1 ──]    │  [Sequential]  ▼│
 [Blocks / Sample ─ spans 0+1]   │  [   512  ]    ▼│
 [Block Size (KB) ─ spans 0+1]   │  [   512  ]    ▼│
 [Samples ─── spans 0+1 ──────]  │  [   250  ]    ▼│
 [────────────── Start ──────────────────────────]  │
```

#### Key rules

- **No `width=` hint on combos.** All combos use `sticky="ew"` to fill their
  column. Hardcoded `width=` values fight with Tkinter's grid geometry and
  should not be set.
- **`pack_propagate(False)`** on `ctrl_frame` is essential. Without it the
  frame would shrink to the minimum size of its children.
- **40 : 60 split** ensures the shorter numeric combos (Threads, Samples, …)
  are proportionally narrower than the Profile / Type combos, matching jdiskmark.
- **Column 0 `weight=0`** prevents the label anchor from expanding; all
  growth goes to cols 1 and 2 in a 2 : 3 ratio.

#### Relevant source

- [`control_panel.py`](pydiskmark/gui/control_panel.py) — `_build_ui()` method
- [`main_window.py`](pydiskmark/gui/main_window.py) — `ctrl_frame = ttk.Frame(bench_page, width=320)`

---

*End of specification.*
