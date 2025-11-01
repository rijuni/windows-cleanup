# cleanup_windows.py — Windows-friendly Python script
# Run with: python cleanup_windows.py [--yes|--no] [--dry-run] ...
import os
import sys
import shutil
import subprocess
import tempfile
import ctypes
import time
import argparse
from pathlib import Path
import threading
import json
import fnmatch
from datetime import datetime, timedelta

# Lightweight color/emoji UI helpers
try:
    # Optional: make ANSI colors work reliably on Windows
    import colorama  # type: ignore
    colorama.just_fix_windows_console()
except Exception:
    pass

# Set UTF-8 encoding for stdout/stderr to handle Unicode characters
if os.name == "nt":  # Windows
    try:
        # Try to set console output to UTF-8
        if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        # If reconfigure fails (older Python or non-reconfigurable streams), try alternative method
        try:
            import io
            if hasattr(sys.stdout, 'buffer'):
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
            if hasattr(sys.stderr, 'buffer'):
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        except Exception:
            pass


class _C:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    MAGENTA = "\x1b[35m"
    CYAN = "\x1b[36m"


def c(text: str, *styles: str) -> str:
    if not styles:
        return text
    return "".join(styles) + text + _C.RESET


class Spinner:
    def __init__(self, message: str):
        self.message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _run(self):
        i = 0
        while not self._stop.is_set():
            frame = self.frames[i % len(self.frames)]
            print("\r" + c(f" {frame} ", _C.CYAN) + self.message + " " * 10, end="", flush=True)
            time.sleep(0.08)
            i += 1

    def __enter__(self):
        print(c("⏳ ", _C.YELLOW) + self.message, end="", flush=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.2)
        # Clear line and print done/failed
        status = c("✓ Done", _C.GREEN, _C.BOLD) if exc is None else c("✗ Failed", _C.RED, _C.BOLD)
        print("\r" + " " * 80, end="\r")
        print(f"{status}")


DEFAULT_OWNER_NAME = "Amlan"


# Runtime config/state (set in main)
CONFIG: dict = {
    "exclude_patterns": [],            # list[str]
    "older_than_days": None,           # int | None
    "verbosity": 1,                    # 0 quiet, 1 normal, 2 verbose
    "log_file": None,                  # Path | None
    "dry_run": False,                  # bool
    "confirm_each": False,             # bool: prompt before each action
    "assume_yes": None,                # True/False/None from --yes/--no
}

STATS: dict = {
    "files_deleted": 0,
    "dirs_deleted": 0,
    "bytes_deleted": 0,
    "locked_or_failed": 0,
    "scheduled_on_reboot": 0,
    "skipped_by_exclude": 0,
    "skipped_by_age": 0,
}


def _log(message: str, level: int = 1) -> None:
    if CONFIG.get("verbosity", 1) >= level:
        print(message)
    log_path: Path | None = CONFIG.get("log_file")
    if log_path:
        try:
            with log_path.open("a", encoding="utf-8", errors="ignore") as f:
                f.write(f"{datetime.now().isoformat()} \t {message}\n")
        except Exception:
            pass


def is_windows() -> bool:
    return os.name == "nt"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(argv: list[str]) -> None:
    # Relaunch the current python interpreter with elevated rights.
    # Ensure we append an internal marker to avoid infinite relaunch loops.
    argv2 = list(argv)
    if "--_elevated" not in argv2:
        argv2.append("--_elevated")
    # First parameter must be the script path (resolved path to current executable script)
    script_path = str(Path(sys.argv[0]).resolve())
    params = " ".join([f'"{script_path}"'] + [f'"{arg}"' for arg in argv2])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def print_header(owner_name: str):
    title = "Windows Care"
    subtitle = "Cleanup • Privacy • Updates"
    top = "╔" + "═" * 46 + "╗"
    bottom = "╚" + "═" * 46 + "╝"
    try:
        print(c(top, _C.CYAN))
        print(c("║ ", _C.CYAN) + c("🧹 " + title, _C.BOLD) + " " * (46 - len(title) - 2) + c("║", _C.CYAN))
        print(c("║ ", _C.CYAN) + c(subtitle, _C.DIM) + " " * (46 - len(subtitle) - 1) + c("║", _C.CYAN))
        print(c(bottom, _C.CYAN))
        print(c(f"👤 Owner: {owner_name}", _C.MAGENTA))
        print(c("──────────", _C.DIM))
    except UnicodeEncodeError:
        # Fallback to ASCII-safe version if Unicode encoding fails
        print("=" * 48)
        print("  Windows Care - Cleanup • Privacy • Updates")
        print("=" * 48)
        print(f"Owner: {owner_name}")
        print("-" * 48)


def _on_rm_error(func, path, exc_info):
    try:
        os.chmod(path, 0o700)
        func(path)
    except Exception:
        # Give up on this path; continue best-effort
        pass


def _should_exclude(path: Path) -> bool:
    patterns: list[str] = CONFIG.get("exclude_patterns", []) or []
    if not patterns:
        return False
    text = str(path)
    for pat in patterns:
        try:
            if fnmatch.fnmatch(text, pat):
                return True
        except Exception:
            # ignore bad patterns
            continue
    return False


def _passes_age_filter(path: Path) -> bool:
    days: int | None = CONFIG.get("older_than_days")
    if not days or days <= 0:
        return True
    try:
        threshold = time.time() - (days * 86400)
        stat = path.stat()
        mtime = stat.st_mtime
        ctime = getattr(stat, "st_ctime", mtime)
        atime = getattr(stat, "st_atime", mtime)
        newest = max(mtime, ctime, atime)
        return newest < threshold
    except Exception:
        # If we cannot stat, err on the safer side: require explicit deletion (treat as not passing)
        return False


def _path_size_bytes(path: Path) -> int:
    try:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        for root, dirs, files in os.walk(path, topdown=True):
            for name in files:
                fp = Path(root) / name
                try:
                    total += fp.stat().st_size
                except Exception:
                    continue
        return total
    except Exception:
        return 0


def safe_delete(path: Path, dry_run: bool = False) -> bool:
    """Attempt to delete a path. Returns True on success, False on failure.

    In dry-run mode this prints what would be removed and returns False.
    """
    try:
        if not path.exists():
            return True
        if _should_exclude(path):
            STATS["skipped_by_exclude"] += 1
            return True
        if not _passes_age_filter(path):
            STATS["skipped_by_age"] += 1
            return True
        # Optional per-action confirmation
        try:
            if not _maybe_confirm(f"Delete {'directory' if path.is_dir() else 'file'}: {path}?", default_no=False):
                _log(c(f"Skipped by user: {path}", _C.DIM), level=2)
                return True
        except Exception:
            # If confirmation fails, do not proceed
            return False
        if dry_run:
            _log(f"DRY-RUN would remove: {path}", level=2)
            return False
        bytes_before = _path_size_bytes(path)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False, onerror=_on_rm_error)
        else:
            path.unlink()
        deleted = not path.exists()
        if deleted:
            if path.is_dir():
                STATS["dirs_deleted"] += 1
            else:
                STATS["files_deleted"] += 1
            STATS["bytes_deleted"] += bytes_before
        return deleted
    except Exception:
        try:
            os.chmod(path, 0o700)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False, onerror=_on_rm_error)
            else:
                path.unlink()
            deleted = not path.exists()
            if deleted:
                STATS["bytes_deleted"] += _path_size_bytes(path)
            else:
                STATS["locked_or_failed"] += 1
            return deleted
        except Exception:
            # Schedule deletion on reboot as a last resort
            try:
                schedule_delete_on_reboot(path)
                STATS["scheduled_on_reboot"] += 1
            except Exception:
                pass
            STATS["locked_or_failed"] += 1
            return False


def delete_contents(dir_path: Path, dry_run: bool = False):
    try:
        if not dir_path.exists():
            return
        for entry in dir_path.iterdir():
            safe_delete(entry, dry_run=dry_run)
    except Exception:
        # Skip unreadable directories
        pass


def get_common_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        paths.append(Path(tempfile.gettempdir()))
    except Exception:
        pass
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        paths.append(Path(localapp) / "Temp")
    # Explicit TEMP/TMP envs if different
    for env_name in ("TEMP", "TMP"):
        env_val = os.environ.get(env_name)
        if env_val:
            try:
                p = Path(env_val)
                if p not in paths:
                    paths.append(p)
            except Exception:
                pass
    windir = os.environ.get("WINDIR", r"C:\\Windows")
    paths.append(Path(windir) / "Temp")
    paths.append(Path(windir) / "Prefetch")
    # Add all user profile temp directories to handle elevation context
    users_root = Path(os.environ.get("SystemDrive", "C:")) / "Users"
    try:
        if users_root.exists():
            for user_dir in users_root.iterdir():
                # Skip well-known non-user directories
                if user_dir.name in {"All Users", "Default", "Default User", "Public"}:
                    continue
                candidate = user_dir / "AppData" / "Local" / "Temp"
                if candidate.exists():
                    paths.append(candidate)
    except Exception:
        pass
    # Service profiles temps
    service_profiles = Path(windir) / "ServiceProfiles"
    for svc in ("LocalService", "NetworkService"):
        try:
            svc_temp = service_profiles / svc / "AppData" / "Local" / "Temp"
            if svc_temp.exists():
                paths.append(svc_temp)
        except Exception:
            pass
    return paths


def get_grouped_paths() -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {
        "current_user_temp": [],
        "users_temp": [],
        "service_temp": [],
        "windows_temp": [],
        "prefetch": [],
    }

    # Current user/process temps
    seen: set[str] = set()
    def add_unique(target_key: str, p: Path):
        key = str(p.resolve()) if p.exists() else str(p)
        if key.lower() not in seen:
            seen.add(key.lower())
            grouped[target_key].append(p)

    try:
        add_unique("current_user_temp", Path(tempfile.gettempdir()))
    except Exception:
        pass
    for env_name in ("TEMP", "TMP"):
        val = os.environ.get(env_name)
        if val:
            try:
                add_unique("current_user_temp", Path(val))
            except Exception:
                pass
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        add_unique("current_user_temp", Path(localapp) / "Temp")

    # Windows Temp and Prefetch
    windir = os.environ.get("WINDIR", r"C:\\Windows")
    add_unique("windows_temp", Path(windir) / "Temp")
    add_unique("prefetch", Path(windir) / "Prefetch")

    # All users temps
    users_root = Path(os.environ.get("SystemDrive", "C:")) / "Users"
    try:
        if users_root.exists():
            for user_dir in users_root.iterdir():
                if user_dir.name in {"All Users", "Default", "Default User", "Public"}:
                    continue
                candidate = user_dir / "AppData" / "Local" / "Temp"
                add_unique("users_temp", candidate)
    except Exception:
        pass

    # Service profiles temps
    service_profiles = Path(windir) / "ServiceProfiles"
    for svc in ("LocalService", "NetworkService"):
        try:
            add_unique("service_temp", service_profiles / svc / "AppData" / "Local" / "Temp")
        except Exception:
            pass

    return grouped


def taskkill_processes(names: list[str], force: bool = False, wait_seconds: float = 2.0):
    """
    Attempt to close processes for given executable names.
    If force is False, try a polite shutdown first and wait; if any remain and force=True, use /F.
    """
    for name in names:
        try:
            # Optional per-action confirmation for each process name
            if not _maybe_confirm(f"Close processes named {name}?", default_no=False):
                _log(c(f"Skipped closing processes: {name}", _C.DIM), level=2)
                continue
            # Try graceful shutdown first
            subprocess.run(["taskkill", "/IM", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            time.sleep(wait_seconds)
            if force:
                # Force-kill any remaining
                subprocess.run(["taskkill", "/F", "/IM", name, "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass


def clean_browser_histories(dry_run: bool = False, force: bool = False):
    # Close common browser processes to unlock files. Try graceful first; if
    # force=True we will escalate to /F after waiting.
    taskkill_processes([
        "chrome.exe", "msedge.exe", "firefox.exe",
        "brave.exe", "opera.exe"
    ], force=force)
    time.sleep(1.0)

    # No backup requested: operate directly on history files (ensure browsers are closed)

    user_profile = Path(os.environ.get("USERPROFILE", ""))
    localapp = Path(os.environ.get("LOCALAPPDATA", str(user_profile / "AppData" / "Local")))
    appdata = Path(os.environ.get("APPDATA", str(user_profile / "AppData" / "Roaming")))

    # Chromium-based
    chromium_targets = [
        localapp / "Google" / "Chrome" / "User Data",
        localapp / "Microsoft" / "Edge" / "User Data",
        localapp / "BraveSoftware" / "Brave-Browser" / "User Data",
        localapp / "Opera Software" / "Opera Stable",
        localapp / "Opera Software" / "Opera GX Stable",
    ]

    for root in chromium_targets:
        if not root.exists():
            continue
        for profile in root.glob("*"):
            if not profile.is_dir():
                continue
            for cache_dir_name in [
                "Cache", "Code Cache", "GPUCache", "Service Worker",
                "DawnCache", "ShaderCache", "GrShaderCache", "Media Cache"
            ]:
                delete_contents(profile / cache_dir_name, dry_run=dry_run)

            for hist_name in ["History", "History-journal", "History Provider Cache", "Network Action Predictor"]:
                target = profile / hist_name
                # Backup first if possible
                ok = safe_delete(target, dry_run=dry_run)
                if not ok and target.exists():
                    if dry_run:
                        # already shown
                        pass
                    else:
                        print(c(f"  Could not remove: {target} (in use or permission denied)", _C.YELLOW))

            for extra_name in ["Top Sites", "Shortcuts", "Visited Links", "Favicons", "Web Data"]:
                target = profile / extra_name
                ok = safe_delete(target, dry_run=dry_run)
                if not ok and target.exists():
                    if not dry_run:
                        print(c(f"  Could not remove: {target}", _C.YELLOW))

    # Firefox
    ff_profiles_root = appdata / "Mozilla" / "Firefox" / "Profiles"
    ff_local_profiles_root = localapp / "Mozilla" / "Firefox" / "Profiles"

    def clean_firefox_profile(profile_dir: Path):
        for fname in ("places.sqlite", "places.sqlite-wal", "places.sqlite-shm"):
            target = profile_dir / fname
            ok = safe_delete(target, dry_run=dry_run)
            if not ok and target.exists() and not dry_run:
                print(c(f"  Could not remove: {target}", _C.YELLOW))

        for f in [
            "formhistory.sqlite", "formhistory.sqlite-wal", "formhistory.sqlite-shm",
            "downloads.sqlite", "downloads.json", "sessionstore.jsonlz4"
        ]:
            ok = safe_delete(profile_dir / f, dry_run=dry_run)
            if not ok and (profile_dir / f).exists() and not dry_run:
                print(c(f"  Could not remove: {profile_dir / f}", _C.YELLOW))
        delete_contents(profile_dir / "cache2", dry_run=dry_run)
        delete_contents(profile_dir / "startupCache", dry_run=dry_run)

    for root in [ff_profiles_root, ff_local_profiles_root]:
        if root.exists():
            for profile in root.glob("*.default*"):
                if profile.is_dir():
                    clean_firefox_profile(profile)


def do_update_upgrade():
    winget = shutil.which("winget")
    choco = shutil.which("choco")
    if winget:
        print(c("⬆️  Running: winget upgrade --all ...", _C.BLUE))
        subprocess.run([
            "winget", "upgrade", "--all",
            "--accept-package-agreements", "--accept-source-agreements"
        ], check=False)
    elif choco:
        print(c("⬆️  Running: choco upgrade all -y ...", _C.BLUE))
        subprocess.run(["choco", "upgrade", "all", "-y"], check=False)
    else:
        print(c("ℹ️  No package manager found (winget/choco). Skipping upgrade.", _C.YELLOW))


def empty_recycle_bin(dry_run: bool = False, silent: bool = True):
    # Use SHEmptyRecycleBinW to empty recycle bin for all drives
    if dry_run:
        print(c("(dry-run) Would empty the Recycle Bin", _C.DIM))
        return
    try:
        SHERB_NOCONFIRMATION = 0x00000001
        SHERB_NOPROGRESSUI = 0x00000002
        SHERB_NOSOUND = 0x00000004
        flags = 0
        if silent:
            flags |= SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        # hwnd = None (0), pszRootPath = None to target all drives
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        # SHEmptyRecycleBin returns HRESULT; 0 means S_OK
        if res != 0:
            # Non-zero HRESULT, still continue without raising
            pass
    except Exception:
        # Fallback: attempt via PowerShell if available
        try:
            subprocess.run([
                "powershell", "-NoProfile", "-Command",
                "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"
            ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def schedule_delete_on_reboot(path: Path) -> None:
    # Use MoveFileExW with MOVEFILE_DELAY_UNTIL_REBOOT to delete after reboot
    MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004
    try:
        ctypes.windll.kernel32.MoveFileExW(str(path), None, MOVEFILE_DELAY_UNTIL_REBOOT)
    except Exception:
        pass


def clean_prefetch(prefetch_dir: Path, dry_run: bool = False):
    # Prefetch often has protected files like Layout.ini; delete only .pf entries
    try:
        if not prefetch_dir.exists():
            return
        for entry in prefetch_dir.iterdir():
            try:
                if entry.is_file():
                    if entry.suffix.lower() == ".pf":
                        safe_delete(entry, dry_run=dry_run)
                    elif entry.name.lower() == "layout.ini":
                        # skip
                        continue
                    else:
                        # best-effort delete other temp-like files
                        safe_delete(entry, dry_run=dry_run)
                elif entry.is_dir():
                    # Rare in Prefetch; attempt best-effort
                    delete_contents(entry, dry_run=dry_run)
                    safe_delete(entry, dry_run=dry_run)
            except Exception:
                try:
                    schedule_delete_on_reboot(entry)
                except Exception:
                    pass
    except Exception:
        pass


def prompt_yes_no(prompt: str, default_no: bool, assume_yes: bool | None) -> bool:
    if assume_yes is True:
        return True
    if assume_yes is False:
        return False
    
    # Check if running as frozen executable (PyInstaller .exe)
    is_frozen = getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')
    
    # ALWAYS use Windows MessageBox when frozen (even if stdin appears available)
    if is_frozen:
        try:
            MB_YESNO = 0x00000004
            MB_ICONQUESTION = 0x00000020
            MB_DEFBUTTON2 = 0x00000100
            MB_DEFBUTTON1 = 0x00000000
            
            flags = MB_YESNO | MB_ICONQUESTION | (MB_DEFBUTTON2 if default_no else MB_DEFBUTTON1)
            
            title = "Windows Care - Confirmation"
            # Use MessageBoxW - simple approach
            try:
                result = ctypes.windll.user32.MessageBoxW(0, prompt, title, flags)
                # IDYES = 6, IDNO = 7, IDCANCEL = 2, 0 = error
                if result == 6:  # IDYES
                    return True
                elif result == 7:  # IDNO
                    return False
                elif result == 0:  # Error occurred
                    # Fallback to console input
                    raise OSError("MessageBox returned 0 (error)")
                else:  # IDCANCEL (2) or other
                    return False
            except Exception as mb_error:
                # MessageBoxW failed, will fall through to console fallback
                raise OSError(f"MessageBox error: {mb_error}") from mb_error
        except Exception as e:
            # If MessageBox fails, try to log and fall back to console
            try:
                print(f"\nError showing dialog: {e}")
                print(f"{prompt} [Y/n]: ", end="", flush=True)
                ans = input().strip().lower()
                return ans in ("", "y", "yes") if not default_no else ans in ("y", "yes")
            except:
                # If everything fails, default to safe option (no)
                return False
    
    # For non-frozen (Python script), use console input
    # Check if stdin is available for console input
    stdin_available = True
    try:
        stdin_available = sys.stdin.isatty()
    except Exception:
        stdin_available = False
    
    if not stdin_available:
        # Stdin not available - default to safe option
        return False
    
    # Use console input when stdin is available
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        print(prompt + suffix, end="", flush=True)
        ans = input().strip().lower()
        if not ans:
            return not default_no
        return ans in ("y", "yes")
    except (EOFError, OSError, KeyboardInterrupt, Exception):
        # Stdin unavailable or interrupted - default to the safe option (no)
        return False


def _maybe_confirm(action_text: str, default_no: bool = False) -> bool:
    """Ask for confirmation if per-action confirmations are enabled.

    Respects global CONFIG for assume_yes/assume_no and confirm_each.
    """
    try:
        if not CONFIG.get("confirm_each", False):
            return True
        return prompt_yes_no(action_text, default_no=default_no, assume_yes=CONFIG.get("assume_yes"))
    except Exception:
        # On any prompt failure, err on safe side: do not proceed
        return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows cleanup and update utility")
    parser.add_argument("--owner-name", default=DEFAULT_OWNER_NAME, help="Name to display in header")
    parser.add_argument("--yes", action="store_true", help="Assume yes for prompts")
    parser.add_argument("--no", action="store_true", help="Assume no for prompts")
    parser.add_argument("--no-browser", action="store_true", help="Skip clearing browser data")
    parser.add_argument("--no-upgrade", action="store_true", help="Skip package upgrades")
    parser.add_argument("--dry-run", action="store_true", help="Do not delete anything; just show actions")
    # Internal flag to indicate the process has already relaunched elevated
    parser.add_argument("--_elevated", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help="Force kill browsers and force delete locked files")
    # New flags
    parser.add_argument("--older-than", type=int, default=None, metavar="DAYS", help="Only delete items older than DAYS")
    parser.add_argument("--exclude", action="append", default=None, metavar="GLOB", help="Glob pattern to exclude (can repeat)")
    parser.add_argument("--json", dest="json_report", default=None, metavar="PATH", help="Write summary JSON report to PATH")
    parser.add_argument("--log", dest="log_file", default=None, metavar="PATH", help="Append plaintext logs to PATH")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet mode (errors and summary only)")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity (-v, -vv)")
    parser.add_argument("--confirm-each", action="store_true", help="Prompt yes/no before each individual action")
    ns = parser.parse_args(argv)
    # If environment variable CLEANUP_FORCE_PROMPTS is set, ensure prompts are shown
    try:
        if os.environ.get("CLEANUP_FORCE_PROMPTS", "").strip() not in ("", "0", "false", "False", "no", "No"):
            setattr(ns, "yes", False)
            setattr(ns, "no", False)
    except Exception:
        pass
    return ns


def pause_on_error():
    """Pause before exiting when running as exe, so user can see error messages."""
    is_frozen = getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')
    if is_frozen:
        try:
            input("\nPress Enter to exit...")
        except:
            pass


def main(argv: list[str]) -> int:
    try:
        if not is_windows():
            print("This script is for Windows only.")
            pause_on_error()
            return 1

        args = parse_args(argv)
        print_header(args.owner_name)
        # Configure runtime options
        verbosity = 1
        if args.quiet:
            verbosity = 0
        elif args.verbose >= 2:
            verbosity = 2
        else:
            verbosity = 1 if args.verbose == 0 else 2

        CONFIG["verbosity"] = verbosity
        CONFIG["dry_run"] = bool(args.dry_run)
        CONFIG["older_than_days"] = args.older_than if getattr(args, "older_than", None) else None
        CONFIG["exclude_patterns"] = list(args.exclude or [])
        CONFIG["log_file"] = Path(args.log_file).resolve() if getattr(args, "log_file", None) else None
        CONFIG["confirm_each"] = bool(getattr(args, "confirm_each", False))
        CONFIG["assume_yes"] = True if args.yes else (False if args.no else None)

        if CONFIG["exclude_patterns"]:
            _log(c(f"Excluding patterns: {CONFIG['exclude_patterns']}", _C.DIM), level=2)
        if CONFIG["older_than_days"]:
            _log(c(f"Deleting only items older than {CONFIG['older_than_days']} day(s)", _C.DIM), level=1)

        # NOTE: We no longer force elevation at startup. Elevation is only requested
        # when the user opts into operations that require Administrator (e.g. clean
        # ALL USERS' temps, Windows Temp, Prefetch, service profiles). This avoids
        # unnecessary UAC/SmartScreen prompts when the user only wants to clean their
        # current user's data (browser history, current temp).

        # 1) Clean Temp and Prefetch with per-category prompts
        print(c("Cleanup options:", _C.BOLD))
        groups = get_grouped_paths()

        # Current user temp
        if prompt_yes_no(
            "Clean CURRENT user's TEMP directories (%TEMP%, %TMP%, %LOCALAPPDATA%\\Temp)?",
            default_no=False,
            assume_yes=(True if args.yes else (False if args.no else None)),
        ):
            with Spinner("Cleaning CURRENT user temp ..."):
                for p in groups["current_user_temp"]:
                    try:
                        print(c(f" → {p}", _C.DIM))
                        delete_contents(p, dry_run=args.dry_run)
                    except Exception as e:
                        print(c(f"  Skipped {p}: {e}", _C.YELLOW))
        else:
            print(c("Skipped CURRENT user's TEMP.", _C.DIM))

        # All users' local temp
        if prompt_yes_no(
            "Clean ALL USERS' Local Temp directories (C:\\Users\\*\\AppData\\Local\\Temp)?",
            default_no=True,
            assume_yes=(True if args.yes else (False if args.no else None)),
        ):
            # This operation requires Administrator rights. If we are not elevated,
            # relaunch elevated and pass an internal marker to avoid loops.
            if not args.dry_run and not is_admin() and not args._elevated:
                print("Administrator privileges are required to clean ALL USERS' Local Temp. Requesting elevation...")
                relaunch_as_admin(argv)
                return 0
            with Spinner("Cleaning ALL users' temp ..."):
                for p in groups["users_temp"]:
                    try:
                        print(c(f" → {p}", _C.DIM))
                        delete_contents(p, dry_run=args.dry_run)
                    except Exception as e:
                        print(c(f"  Skipped {p}: {e}", _C.YELLOW))
        else:
            print(c("Skipped ALL USERS' Local Temp.", _C.DIM))

        # Service profiles temp
        if prompt_yes_no(
            "Clean SERVICE profiles Temp (LocalService/NetworkService)?",
            default_no=True,
            assume_yes=(True if args.yes else (False if args.no else None)),
        ):
            if not args.dry_run and not is_admin() and not args._elevated:
                print("Administrator privileges are required to clean SERVICE profiles Temp. Requesting elevation...")
                relaunch_as_admin(argv)
                return 0
            with Spinner("Cleaning service profiles temp ..."):
                for p in groups["service_temp"]:
                    try:
                        print(c(f" → {p}", _C.DIM))
                        delete_contents(p, dry_run=args.dry_run)
                    except Exception as e:
                        print(c(f"  Skipped {p}: {e}", _C.YELLOW))
        else:
            print(c("Skipped SERVICE profiles Temp.", _C.DIM))

        # Windows Temp
        if prompt_yes_no(
            "Clean WINDOWS Temp (C:\\Windows\\Temp)?",
            default_no=False,
            assume_yes=(True if args.yes else (False if args.no else None)),
        ):
            if not args.dry_run and not is_admin() and not args._elevated:
                print("Administrator privileges are required to clean WINDOWS Temp. Requesting elevation...")
                relaunch_as_admin(argv)
                return 0
            with Spinner("Cleaning Windows temp ..."):
                for p in groups["windows_temp"]:
                    try:
                        print(c(f" → {p}", _C.DIM))
                        delete_contents(p, dry_run=args.dry_run)
                    except Exception as e:
                        print(c(f"  Skipped {p}: {e}", _C.YELLOW))
        else:
            print(c("Skipped WINDOWS Temp.", _C.DIM))

        # Prefetch
        if prompt_yes_no(
            "Clean PREFETCH (.pf files only)?",
            default_no=True,
            assume_yes=(True if args.yes else (False if args.no else None)),
        ):
            if not args.dry_run and not is_admin() and not args._elevated:
                print("Administrator privileges are required to clean PREFETCH. Requesting elevation...")
                relaunch_as_admin(argv)
                return 0
            with Spinner("Cleaning Prefetch (.pf) ..."):
                for p in groups["prefetch"]:
                    try:
                        print(c(f" → {p}", _C.DIM))
                        clean_prefetch(p, dry_run=args.dry_run)
                    except Exception as e:
                        print(c(f"  Skipped {p}: {e}", _C.YELLOW))
        else:
            print(c("Skipped PREFETCH.", _C.DIM))

        # 2) Browser history (ask first unless overridden)
        run_browser_cleanup = False
        if not args.no_browser:
            run_browser_cleanup = prompt_yes_no(
                "Do you want to clear browser history (Chrome/Edge/Firefox)?",
                default_no=True,
                assume_yes=(True if args.yes else (False if args.no else None)),
            )

        if run_browser_cleanup:
            print(c("🧽 Clearing browser history...", _C.BLUE))
            clean_browser_histories(dry_run=args.dry_run, force=args.force)
        else:
            print(c("Skipped clearing browser history.", _C.DIM))

        # 3) Empty Recycle Bin
        empty_bin = prompt_yes_no(
            "Empty Recycle Bin for all drives?",
            default_no=False,
            assume_yes=(True if args.yes else (False if args.no else None)),
        )
        if empty_bin:
            print(c("🗑️  Emptying Recycle Bin...", _C.BLUE))
            empty_recycle_bin(dry_run=args.dry_run, silent=True)
        else:
            print(c("Skipped emptying Recycle Bin.", _C.DIM))

        # 4) Update/Upgrade
        run_upgrades = False
        if not args.no_upgrade:
            run_upgrades = prompt_yes_no(
                "Run system package upgrades via winget/choco?",
                default_no=False,
                assume_yes=(True if args.yes else (False if args.no else None)),
            )

        if run_upgrades and not args.dry_run:
            do_update_upgrade()
        elif run_upgrades and args.dry_run:
            print(c("(dry-run) Would run package upgrades (winget/choco)", _C.DIM))
        else:
            print(c("Skipped package upgrades.", _C.DIM))

        # Final summary
        bytes_deleted = STATS.get("bytes_deleted", 0)
        def _fmt_size(n: int) -> str:
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if n < 1024 or unit == "TB":
                    return f"{n:.2f} {unit}" if unit != "B" else f"{n} {unit}"
                n = n / 1024.0
            return f"{n:.2f} TB"

        summary_line = (
            f"Deleted files: {STATS['files_deleted']}, dirs: {STATS['dirs_deleted']}, "
            f"freed: {_fmt_size(bytes_deleted)}, "
            f"skipped(exclude): {STATS['skipped_by_exclude']}, skipped(age): {STATS['skipped_by_age']}, "
            f"failed: {STATS['locked_or_failed']}, scheduled(on reboot): {STATS['scheduled_on_reboot']}"
        )
        print(c("✨ All done.", _C.GREEN, _C.BOLD))
        print(c(summary_line, _C.DIM))

        # Optional JSON report
        if getattr(args, "json_report", None):
            try:
                report_path = Path(args.json_report).resolve()
                report = {
                    "timestamp": datetime.now().isoformat(),
                    "stats": STATS,
                    "options": {
                        "dry_run": CONFIG["dry_run"],
                        "older_than_days": CONFIG["older_than_days"],
                        "exclude_patterns": CONFIG["exclude_patterns"],
                    },
                }
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                _log(c(f"JSON report written to {report_path}", _C.DIM), level=1)
            except Exception:
                print(c("Failed to write JSON report.", _C.YELLOW))
        return 0
    except KeyboardInterrupt:
        print(c("\n\n⚠️  Interrupted by user.", _C.YELLOW, _C.BOLD))
        pause_on_error()
        return 130
    except Exception as e:
        print(c(f"\n\n❌ Error occurred: {e}", _C.RED, _C.BOLD), file=sys.stderr)
        import traceback
        print(c("Traceback:", _C.RED), file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        pause_on_error()
        return 1


if __name__ == "__main__":
    try:
        exit_code = main(sys.argv[1:])
        # Pause at the end if running as exe (even on success)
        is_frozen = getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')
        if is_frozen:
            try:
                input("\nPress Enter to exit...")
            except:
                pass
        raise SystemExit(exit_code)
    except SystemExit:
        raise
    except Exception as e:
        print(c(f"\n\n❌ Fatal error: {e}", _C.RED, _C.BOLD), file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        pause_on_error()
        raise SystemExit(1)


