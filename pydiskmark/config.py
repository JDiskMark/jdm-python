"""Settings persistence for pydiskmark.

Mirrors App.loadConfig() / App.saveConfig() in jdm-java.

Java uses java.util.Properties (flat key=value text file).  The Python
equivalent is configparser, which wraps the same key=value pairs inside a
single [pdm] section header so the stdlib can parse it without a custom
reader.

File location (mirrors jdm-java's ~/.jdm/<VERSION>/jdm.properties):
    ~/.pdm/<VERSION>/pdm.properties

Example file content:
    # pydiskmark 0.1.0 Properties File
    [pdm]
    benchmarkType = WRITE
    blockSequence = SEQUENTIAL
    numOfSamples = 200
    theme = dark
    ...
"""
from __future__ import annotations

import configparser
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths — mirrors App.APP_CACHE_DIR / App.PROPERTIES_FILE in jdm-java
# ---------------------------------------------------------------------------

# Deferred import to avoid circular import at module load time; resolved in
# _get_version() below.
def _get_version() -> str:
    try:
        from . import app as _app
        return _app.VERSION
    except Exception:
        return "0.0"


def _cache_dir() -> Path:
    return Path.home() / ".pdm" / _get_version()


def _properties_file() -> Path:
    return _cache_dir() / "pdm.properties"


# Expose as module-level convenience properties (evaluated lazily at call time
# so that app.VERSION is already set when these are first read).
@property
def APP_CACHE_DIR() -> Path:       # noqa: N802
    return _cache_dir()


@property
def PROPERTIES_FILE() -> Path:     # noqa: N802
    return _properties_file()


_SECTION = "pdm"


# ---------------------------------------------------------------------------
# load_config()
# ---------------------------------------------------------------------------

def load_config() -> None:
    """Read pdm.properties and apply saved values to pydiskmark.app globals.

    If the file does not exist, save_config() is called first to generate it
    with current defaults — exactly as App.loadConfig() does in jdm-java.
    """
    import pydiskmark.app as app
    from .benchmark import BenchmarkType, BlockSequence, IoEngine, SectorAlignment
    from .benchmark_profile import BenchmarkProfile

    props_file = _properties_file()
    cache_dir = _cache_dir()

    if not props_file.exists():
        # No properties file yet — defaults already live in app globals.
        # We do NOT call save_config() here: in GUI mode that would import
        # sv_ttk and call get_theme() before a Tk root exists, which causes
        # tkinter to auto-create a stray bare Tk() window (the ghost "tk"
        # dialog) and breaks theming.  The file is written naturally by the
        # shutdown hook in gui/__init__.py when the window closes.
        log.info("%s does not exist — using defaults, will save on exit", props_file)
        return

    log.info("loading: %s", props_file)

    cp = configparser.ConfigParser()
    # Preserve key case (Java properties are case-sensitive)
    cp.optionxform = str  # type: ignore[assignment]

    try:
        cp.read(props_file, encoding="utf-8")
    except Exception as exc:
        log.error("Failed to read %s: %s", props_file, exc)
        return

    if _SECTION not in cp:
        log.warning("Section [%s] missing from %s — skipping load", _SECTION, props_file)
        return

    def _get(key: str, default: str) -> str:
        return cp[_SECTION].get(key, default)

    # --- active profile ---
    value = _get("activeProfile", app.active_profile.name)
    try:
        app.active_profile = BenchmarkProfile[value.upper()]
    except KeyError:
        log.warning("Invalid activeProfile '%s', using default", value)

    # --- profileModified ---
    app.profile_modified = _get("profileModified", str(app.profile_modified)).lower() == "true"

    # --- benchmarkType ---
    value = _get("benchmarkType", app.benchmark_type.name)
    try:
        app.benchmark_type = BenchmarkType[value.upper()]
    except KeyError:
        log.warning("Invalid benchmarkType '%s', using default", value)

    # --- blockSequence ---
    value = _get("blockSequence", app.block_sequence.name)
    try:
        app.block_sequence = BlockSequence[value.upper()]
    except KeyError:
        log.warning("Invalid blockSequence '%s', using default", value)

    # --- numeric settings ---
    try:
        app.num_of_samples = int(_get("numOfSamples", str(app.num_of_samples)))
    except ValueError:
        pass

    try:
        app.num_of_blocks = int(_get("numOfBlocks", str(app.num_of_blocks)))
    except ValueError:
        pass

    try:
        app.block_size_kb = int(_get("blockSizeKb", str(app.block_size_kb)))
    except ValueError:
        pass

    try:
        app.num_of_threads = int(_get("numOfThreads", str(app.num_of_threads)))
    except ValueError:
        pass

    # --- IO engine ---
    value = _get("ioEngine", app.io_engine.name)
    try:
        app.io_engine = IoEngine[value.upper()]
    except KeyError:
        log.warning("Invalid ioEngine '%s', using default", value)

    # --- bool flags ---
    app.direct_enable     = _get("directEnable",     str(app.direct_enable)).lower()     == "true"
    app.write_sync_enable = _get("writeSyncEnable",  str(app.write_sync_enable)).lower() == "true"
    app.multi_file        = _get("multiFile",        str(app.multi_file)).lower()        == "true"
    app.auto_remove_data  = _get("autoRemoveData",   str(app.auto_remove_data)).lower()  == "true"
    app.auto_reset        = _get("autoReset",        str(app.auto_reset)).lower()        == "true"

    # --- sector alignment ---
    value = _get("sectorAlignment", app.sector_alignment.name)
    try:
        app.sector_alignment = SectorAlignment[value.upper()]
    except KeyError:
        log.warning("Invalid sectorAlignment '%s', using default", value)

    # --- location dir (restore last-used location) ---
    saved_loc = _get("locationDir", "")
    if saved_loc:
        from pathlib import Path as _Path
        candidate = _Path(saved_loc)
        if candidate.is_dir():
            app.location_dir = str(candidate)
            app.data_dir = str(candidate / app.DATADIRNAME)
        else:
            log.warning("Saved locationDir '%s' no longer exists — ignoring", saved_loc)

    # --- theme (GUI only — read the raw string; applied by gui/__init__.py) ---
    # We store it as a plain string on the app module so the GUI can apply it
    # before MainWindow is constructed (sv_ttk must be called after Tk root exists).
    saved_theme = _get("theme", "")
    if saved_theme in ("dark", "light"):
        app._saved_theme = saved_theme  # type: ignore[attr-defined]
    else:
        app._saved_theme = "dark"       # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# save_config()
# ---------------------------------------------------------------------------

def save_config() -> None:
    """Snapshot current pydiskmark.app globals to pdm.properties.

    Mirrors App.saveConfig() in jdm-java.  Safe to call from a shutdown hook
    or at any point during the session.
    """
    import pydiskmark.app as app

    cache_dir = _cache_dir()
    props_file = _properties_file()

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Cannot create cache dir %s: %s", cache_dir, exc)
        return

    cp = configparser.ConfigParser()
    cp.optionxform = str  # type: ignore[assignment]
    cp[_SECTION] = {}

    s = cp[_SECTION]

    s["activeProfile"]    = app.active_profile.name
    s["profileModified"]  = str(app.profile_modified).lower()
    s["benchmarkType"]    = app.benchmark_type.name
    s["blockSequence"]    = app.block_sequence.name
    s["numOfSamples"]     = str(app.num_of_samples)
    s["numOfBlocks"]      = str(app.num_of_blocks)
    s["blockSizeKb"]      = str(app.block_size_kb)
    s["numOfThreads"]     = str(app.num_of_threads)
    s["ioEngine"]         = app.io_engine.name
    s["directEnable"]     = str(app.direct_enable).lower()
    s["writeSyncEnable"]  = str(app.write_sync_enable).lower()
    s["sectorAlignment"]  = app.sector_alignment.name
    s["multiFile"]        = str(app.multi_file).lower()
    s["autoRemoveData"]   = str(app.auto_remove_data).lower()
    s["autoReset"]        = str(app.auto_reset).lower()

    if app.location_dir:
        s["locationDir"]  = app.location_dir

    # --- theme (GUI only — read from sv_ttk if available and in GUI mode) ---
    # Guard: sv_ttk.get_theme() returns the underlying Tk theme name (e.g. "vista")
    # when called outside a GUI session, so we only trust it when the result is
    # one of the sv_ttk values we actually set ("dark" or "light").
    import pydiskmark.app as _app_ref
    written_theme = getattr(_app_ref, "_saved_theme", "dark")
    if _app_ref.mode == _app_ref.Mode.GUI:
        try:
            import sv_ttk
            candidate = sv_ttk.get_theme()
            if candidate in ("dark", "light"):
                written_theme = candidate
        except Exception:
            pass
    s["theme"] = written_theme

    try:
        with open(props_file, "w", encoding="utf-8") as fh:
            from . import app as _app
            fh.write(f"# pydiskmark {_app.VERSION} Properties File\n")
            cp.write(fh)
        log.info("Config saved to %s", props_file)
    except OSError as exc:
        log.error("Failed to write %s: %s", props_file, exc)
