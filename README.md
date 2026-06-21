# pydiskmark

A cross-platform disk benchmark utility written in Python.
Sister project to [jdm-java](https://github.com/jdm-java/jdm-java).

## Requirements

- Python 3.11+
- Windows, macOS, or Linux

## Install

```bash
pip install -e .
```

## Quick start

```bash
# Run the default quick test (50 samples, 1 MB blocks, read & write)
python -m pydiskmark run -p QUICK_TEST

# Write-only, 10 samples, export results to JSON
python -m pydiskmark run -t WRITE -n 10 -e results.json

# See all options
python -m pydiskmark run --help
```

## Common flags

| Flag | Description |
|---|---|
| `-p PROFILE` | Named profile (`QUICK_TEST`, `MAX_THROUGHPUT`, …) |
| `-t TYPE` | `READ`, `WRITE`, or `READ_WRITE` |
| `-n N` | Number of samples |
| `-b N` | Blocks per sample |
| `-z KB` | Block size in KB |
| `-T N` | Number of threads |
| `-o ORDER` | `SEQUENTIAL` or `RANDOM` |
| `-d` | Enable Direct I/O (bypass OS page cache) |
| `-l DIR` | Directory for test files |
| `-e FILE` | Export results (`.json`, `.yml`, `.csv`) |
| `-v` | Verbose — print each sample as it completes |

## Running tests

```bash
python -m pytest tests/
```
