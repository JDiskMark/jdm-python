# pydiskmark

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A cross-platform disk benchmark utility written in Python.
Sister project to [jdm-java](https://github.com/jdm-java/jdm-java).

## Requirements

- Python 3.11+
- Windows, macOS, or Linux
- `matplotlib>=3.7`, `sv-ttk>=2.5` (GUI)
- `Pillow>=9` *(optional — splash and About icons; falls back to emoji if absent)*

## Install

```bash
pip install -e .
```

## Launch the GUI

```bash
pydiskmark gui
```

The GUI opens with a splash screen while the main window builds off-screen.
Settings (theme, last location, benchmark type, etc.) are persisted to
`~/.pdm/<version>/pdm.properties` and restored on next launch.

## CLI quick start

```bash
# Run the default quick test (50 samples, 1 MB blocks, read & write)
python -m pydiskmark run -p QUICK_TEST

# Write-only, 10 samples, export results to JSON
python -m pydiskmark run -t WRITE -n 10 -e results.json

# See all options
python -m pydiskmark run --help
```

## Common CLI flags

| Flag | Description |
|---|---|
| `-p PROFILE` | `QUICK_TEST`, `MAX_THROUGHPUT`, `HIGH_LOAD_RANDOM_T32`, `LOW_LOAD_RANDOM_T1`, `MAX_WRITE_STRESS`, `MEDIA_PLAYBACK`, `VIDEO_EXPORTING`, `PHOTO_LIBRARY` |
| `-t TYPE` | `READ`, `WRITE`, or `READ_WRITE` (default: `READ_WRITE`) |
| `-n N` | Number of samples |
| `-b N` | Blocks per sample |
| `-z KB` | Block size in KB |
| `-T N` | Number of threads |
| `-o ORDER` | `SEQUENTIAL` or `RANDOM` |
| `-d` | Enable Direct I/O (bypass OS page cache) |
| `-a ALIGN` | Sector alignment: `NONE`, `ALIGN_512`, `ALIGN_4K` (default), `ALIGN_8K`, `ALIGN_16K`, `ALIGN_64K` |
| `-y` | Enable write-sync (fsync after each block) |
| `-l DIR` | Directory for test files |
| `-e FILE` | Export results (`.json`, `.yml`, `.csv`) |
| `-v` | Verbose — print each sample as it completes |

## Running tests

```bash
python -m pytest tests/
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
