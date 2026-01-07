from typing import Literal
import datetime
import sys
import threading
from pathlib import Path

DEBUG = False
_PROJECT_ROOT = Path(__file__).resolve().parent
_STORAGE_ROOT: Path | None = None
references = {}
_DEBUG_PRINT_LOCK = threading.Lock()

def set_reference(name: Literal["GPTManager", "OnlineDatabase", "OnlineStorage", "DiscordBot", "AssistantManager", "GoogleSheets"], reference: object) -> None:
    debug_print("tools", f"Setting reference for {name}.")
    references[name] = reference

def get_reference(name: Literal["GPTManager", "OnlineDatabase", "OnlineStorage", "DiscordBot", "AssistantManager", "GoogleSheets"]) -> object:
    debug_print("tools", f"Getting reference for {name}.")
    return references.get(name, None)

def set_debug(value: bool) -> None:
    print(f"Setting debug mode to {value}.")
    global DEBUG
    DEBUG = value

def get_debug() -> bool:
    return DEBUG

def debug_print(module_name: str = None, text: str = None) -> None:
    if not module_name or not text:
        print("debug_print called without required parameters.")
        return
    if not DEBUG:
        return
    time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"[{time}][DEBUG][{module_name}] {text}"
    # Avoid deadlocking if another thread dies while holding the lock.
    if not _DEBUG_PRINT_LOCK.acquire(timeout=0.5):
        return
    try:
        stream = getattr(sys, "stdout", None) or getattr(sys, "__stdout__", None)
        if stream is None:
            return
        print(message, file=stream, flush=True)
    finally:
        _DEBUG_PRINT_LOCK.release()

def get_app_root() -> Path:
    """Return the bundle root (repo root in dev, _MEIPASS/exe folder when frozen)."""
    if getattr(sys, "frozen", False):  # Running under PyInstaller/Freeze
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return _PROJECT_ROOT

def path_from_app_root(*parts: str) -> Path:
    """Join paths relative to the runtime root."""
    return get_app_root().joinpath(*parts)

def get_storage_root() -> Path:
    """Return the per-install data directory (always sibling 'data' next to the executable)."""
    global _STORAGE_ROOT
    if _STORAGE_ROOT is not None:
        return _STORAGE_ROOT

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = _PROJECT_ROOT

    root = base / "data"
    root.mkdir(parents=True, exist_ok=True)
    _STORAGE_ROOT = root
    return _STORAGE_ROOT


def path_from_storage_root(*parts: str) -> Path:
    """Join paths relative to the persistent writable storage directory."""
    base = get_storage_root()
    return base.joinpath(*parts) if parts else base