"""DR3 — DiRT Rally 2.0 community server with GUI.

A tkinter GUI with START / STOP buttons that handles cert generation,
hosts file management, and the game server lifecycle.

PyInstaller compiles this file into a single executable via build_exe.py.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"

if IS_WIN:
    import ctypes

DR2_STEAM_APPID = 690790

# Cap elevation calls at 5 minutes — if polkit/UAC hangs, surface it as a
# failure in the GUI log instead of leaving the worker thread stuck forever.
ELEVATION_TIMEOUT_SECONDS = 300

UI_FONT = "Segoe UI" if IS_WIN else "DejaVu Sans"
MONO_FONT = "Consolas" if IS_WIN else "DejaVu Sans Mono"

def _read_version() -> str:
    """Read version from VERSION file (bundled or local)."""
    for base in [Path(getattr(sys, "_MEIPASS", "")), Path(__file__).parent]:
        vf = base / "VERSION"
        if vf.is_file():
            return vf.read_text().strip()
    return "dev"

VERSION = _read_version()

# ---------------------------------------------------------------------------
# Resource-path helpers (PyInstaller bundle detection)
# ---------------------------------------------------------------------------

def _bundle_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _data_dir() -> Path:
    return _bundle_root() / "data"


def _runtime_icon_path() -> Optional[Path]:
    """Return the 32x32 PNG used for the Tk window/taskbar icon."""
    for candidate in (
        _bundle_root() / "icon_32.png",
        Path(__file__).parent / "icon_32.png",
    ):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

if IS_WIN:
    APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    DR3_DIR = APPDATA / "DR3"
    _DEFAULT_HOSTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    HOSTS_NEWLINE = "\r\n"
else:
    _xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    DR3_DIR = Path(_xdg) / "dr3"
    _DEFAULT_HOSTS = Path("/etc/hosts")
    HOSTS_NEWLINE = "\n"

# Env override is honored on all platforms — used by CI integration tests so
# the elevation path can be exercised without touching the real system hosts
# file. Also useful for local dry-run testing.
HOSTS_FILE = Path(os.environ["DR3_HOSTS_FILE"]) if "DR3_HOSTS_FILE" in os.environ else _DEFAULT_HOSTS

CONFIG_PATH = DR3_DIR / "config.json"
CERTS_DIR = DR3_DIR / "certs"
CERT_PATH = CERTS_DIR / "dr2server-cert.pem"
KEY_PATH = CERTS_DIR / "dr2server-key.pem"
LOG_PATH = DR3_DIR / "debug.log"
LOGS_DIR = DR3_DIR / "logs"            # archived session logs (backlog #52)
CAPTURES_DIR = DR3_DIR / "captures"
LOG_ARCHIVE_KEEP = 10                  # retention cap for LOGS_DIR


class _TeeStream:
    """Forward writes to a file and (optionally) the original stream.

    Used to redirect sys.stdout / sys.stderr inside the PyInstaller --windowed
    build, where the original streams are None or point at NUL. The file is
    line-buffered so a crash still leaves us with everything up to the last
    newline.
    """

    def __init__(self, file_obj, original=None):
        self._file = file_obj
        self._original = original

    def write(self, data: str) -> int:
        try:
            if self._original is not None:
                self._original.write(data)
        except Exception:
            pass
        try:
            self._file.write(data)
            self._file.flush()
        except Exception:
            pass
        return len(data)

    def flush(self) -> None:
        for s in (self._original, self._file):
            if s is None:
                continue
            try:
                s.flush()
            except Exception:
                pass


_LOG_FILE = None  # set by _setup_file_logging() so log() can tee into it
_LOG_OPENED_AT: Optional[datetime] = None  # session start (backlog #52)


def _setup_file_logging() -> None:
    """Open debug.log and redirect stdout/stderr into it.

    Called once at process start so PyInstaller --windowed builds (where
    sys.stdout is None) still leave a debuggable trail on disk.
    """
    global _LOG_FILE, _LOG_OPENED_AT
    try:
        DR3_DIR.mkdir(parents=True, exist_ok=True)
        # Truncate per-launch so the file reflects the current session, not
        # months of accumulated history. The previous run is rotated to
        # debug.prev.log so we still have it if the user reports an issue
        # after restarting (crash fallback — clean exits archive the log
        # into logs/ instead, see _archive_debug_log).
        if LOG_PATH.exists():
            try:
                prev = LOG_PATH.with_name(f"{LOG_PATH.stem}.prev{LOG_PATH.suffix}")
                if prev.exists():
                    prev.unlink()
                LOG_PATH.rename(prev)
            except OSError:
                pass
        _LOG_FILE = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
    except OSError:
        _LOG_FILE = None
        return

    _LOG_OPENED_AT = datetime.now()
    header = (
        f"=== DR3 debug.log opened "
        f"{_LOG_OPENED_AT.isoformat(timespec='seconds')} ===\n"
    )
    _LOG_FILE.write(header)
    _LOG_FILE.flush()

    sys.stdout = _TeeStream(_LOG_FILE, original=sys.stdout)
    sys.stderr = _TeeStream(_LOG_FILE, original=sys.stderr)


def _archive_debug_log(verbose: bool) -> Optional[Path]:
    """Archive this session's debug.log on clean exit (backlog #52).

    Only when Verbose Logging is ON and the log carries content beyond the
    startup header line. The file is MOVED into logs/ as
    <version>.<session-start timestamp>.debug.log (the timestamp marks when
    this session began); the per-launch rotation to debug.prev.log stays as
    the crash fallback for sessions that never reach a clean exit. Oldest
    archives beyond LOG_ARCHIVE_KEEP are pruned. Returns the archive path,
    or None when nothing was archived.
    """
    global _LOG_FILE
    if not verbose:
        return None
    try:
        if not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0:
            return None
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if lines and lines[0].startswith("=== DR3 debug.log opened"):
            lines = lines[1:]
        if not any(line.strip() for line in lines):
            return None
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = (_LOG_OPENED_AT or datetime.now()).strftime("%Y%m%d-%H%M%S")
        dest = LOGS_DIR / f"{VERSION}.{stamp}.debug.log"
        n = 1
        while dest.exists():
            n += 1
            dest = LOGS_DIR / f"{VERSION}.{stamp}-{n}.debug.log"
        LOG_PATH.replace(dest)
        # The open handle now points at the archived file — close it and drop
        # the reference so no further line (incl. the archive confirmation)
        # lands in the archive.
        if _LOG_FILE is not None:
            try:
                _LOG_FILE.close()
            except OSError:
                pass
            _LOG_FILE = None
    except OSError:
        return None
    try:
        archives = sorted(LOGS_DIR.glob("*.debug.log"),
                          key=lambda p: p.stat().st_mtime)
        for old in archives[:-LOG_ARCHIVE_KEEP]:
            old.unlink()
    except OSError:
        pass
    return dest

REDIRECT_DOMAINS = [
    "prod.egonet.codemasters.com",
    "qa.egonet.codemasters.com",
    "terms.codemasters.com",
    "aurora.codemasters.local",
]

HOSTS_BEGIN = "# BEGIN DR3"
HOSTS_END = "# END DR3"
SERVER_IP = "127.0.0.1"
DASHBOARD_URL = "https://byagersi.com/rally/dashboard"
API_URL = "https://byagersi.com/rally"
UPSTREAM_IP = "159.153.126.42"

# Update-banner source: the site's public client-version endpoint (latest
# version + download link). Empty = disabled.
VERSION_CHECK_URL = "https://byagersi.com/rally/api/client-version"
UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000  # re-check while the app is open
UPDATES_DIR = DR3_DIR / "updates"          # staged auto-updater downloads


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    if IS_WIN:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _self_invocation_args() -> list[str]:
    """Argv that re-launches this same program (for elevated helper calls).

    PyInstaller --onefile: sys.executable IS the app .exe, no script arg needed.
    Source mode: sys.executable is python.exe, so we need to pass our .py path.
    Critical: in PyInstaller mode the bootloader can't run a .py path passed as
    argv — that's the bug that left Win11 (and Win10, silently) without hosts
    configured, because the elevated child just re-opened the GUI instead of
    running the helper script.
    """
    # _is_pyinstaller_bundle is defined later; inline the same check here.
    if hasattr(sys, "_MEIPASS") or getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


def _windows_elevate_admin_helper(op: str, timeout: int = ELEVATION_TIMEOUT_SECONDS) -> int:
    """Re-launch ourselves with `--admin-helper <op>` via UAC.

    Uses ShellExecuteExW("runas") directly rather than spawning a PowerShell
    child with a bypassed execution policy to do it: that spawn pattern is a
    textbook malware heuristic and contributed to Defender flagging the exe
    (the literal command string is deliberately not written anywhere here).
    Returns the elevated child's exit code, or a nonzero value if the
    UAC prompt was declined / the launch failed. Raises TimeoutExpired if
    the child doesn't finish within `timeout`. Callers still verify the side
    effect (hosts_configured) rather than trusting the return code.
    """
    import ctypes.wintypes as wt

    invocation = _self_invocation_args() + ["--admin-helper", op]
    file_path = invocation[0]
    params = " ".join(f'"{a}"' for a in invocation[1:])

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("fMask", wt.ULONG),
            ("hwnd", wt.HWND),
            ("lpVerb", wt.LPCWSTR),
            ("lpFile", wt.LPCWSTR),
            ("lpParameters", wt.LPCWSTR),
            ("lpDirectory", wt.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wt.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wt.LPCWSTR),
            ("hkeyClass", wt.HKEY),
            ("dwHotKey", wt.DWORD),
            ("hIconOrMonitor", wt.HANDLE),
            ("hProcess", wt.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x40
    SEE_MASK_NOASYNC = 0x100
    SW_HIDE = 0
    WAIT_TIMEOUT = 0x102
    INFINITE = 0xFFFFFFFF

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wt.BOOL
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel32.GetExitCodeProcess.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = file_path
    info.lpParameters = params
    info.lpDirectory = str(Path(file_path).parent)
    info.nShow = SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        # Typically ERROR_CANCELLED (1223): user declined the UAC prompt.
        return ctypes.get_last_error() or 1
    if not info.hProcess:
        return 1
    try:
        wait_ms = INFINITE if timeout is None else int(timeout * 1000)
        if kernel32.WaitForSingleObject(info.hProcess, wait_ms) == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired([file_path, *invocation[1:]], timeout)
        code = wt.DWORD(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


# Exit codes of the elevated `--admin-helper` child. The parent reads these
# via _windows_elevate_admin_helper to explain partial failures to the user.
ADMIN_HELPER_OK = 0
ADMIN_HELPER_BAD_OP = 1
ADMIN_HELPER_EXCEPTION = 2
ADMIN_HELPER_CERT_FAILED = 3   # hosts written, but cert trust install failed


def _run_admin_helper(op: str) -> int:
    """Entry point for the elevated subprocess. Performs the privileged
    work (cert trust + hosts edits) and returns an exit code.
    """
    try:
        if op == "start":
            # Hosts first: it's the part the game can't work without. A cert
            # failure is reported separately so the parent can say so.
            add_hosts()
            if IS_WIN and cert_exists() and not install_cert_trust():
                return ADMIN_HELPER_CERT_FAILED
            return ADMIN_HELPER_OK
        if op == "stop":
            remove_hosts()
            return ADMIN_HELPER_OK
    except Exception:
        return ADMIN_HELPER_EXCEPTION
    return ADMIN_HELPER_BAD_OP


# ---------------------------------------------------------------------------
# TLS certificate
# ---------------------------------------------------------------------------

def generate_cert() -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    hosts = REDIRECT_DOMAINS + ["localhost"]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DR3"),
        x509.NameAttribute(NameOID.COMMON_NAME, hosts[0]),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))


def cert_exists() -> bool:
    return CERT_PATH.exists() and KEY_PATH.exists()


def install_cert_trust() -> bool:
    """Install cert into the Windows machine Root store. Returns True on success.

    Uses crypt32 directly instead of shelling out to `certutil -addstore Root`,
    which is a common malware behavior that AV heuristics weigh against us.
    Must run elevated (the helper child is).
    """
    import ssl
    import ctypes.wintypes as wt

    X509_ASN_ENCODING = 0x1
    CERT_STORE_ADD_REPLACE_EXISTING = 3
    CERT_STORE_PROV_SYSTEM_W = 10
    # Same store `certutil -addstore Root` targets: HKLM, trusted for every
    # user on the machine (CertOpenSystemStore would pick the elevating
    # admin's per-user store, which isn't necessarily the player's).
    CERT_SYSTEM_STORE_LOCAL_MACHINE = 0x00020000

    try:
        der = ssl.PEM_cert_to_DER_cert(CERT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CertOpenStore.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.LPCWSTR,
    ]
    crypt32.CertOpenStore.restype = ctypes.c_void_p
    crypt32.CertAddEncodedCertificateToStore.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.c_char_p, wt.DWORD, wt.DWORD, ctypes.c_void_p,
    ]
    crypt32.CertAddEncodedCertificateToStore.restype = wt.BOOL
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wt.DWORD]
    crypt32.CertCloseStore.restype = wt.BOOL

    store = crypt32.CertOpenStore(
        ctypes.c_void_p(CERT_STORE_PROV_SYSTEM_W), 0, None,
        CERT_SYSTEM_STORE_LOCAL_MACHINE, "ROOT",
    )
    if not store:
        return False
    try:
        return bool(crypt32.CertAddEncodedCertificateToStore(
            store, X509_ASN_ENCODING, der, len(der),
            CERT_STORE_ADD_REPLACE_EXISTING, None,
        ))
    finally:
        crypt32.CertCloseStore(store, 0)


# ---------------------------------------------------------------------------
# Linux: cert trust into DR2's Proton prefix
# ---------------------------------------------------------------------------

def _find_dr2_proton_prefix() -> Optional[Path]:
    home = Path.home()
    candidates = [
        home / ".steam" / "steam" / "steamapps" / "compatdata" / str(DR2_STEAM_APPID) / "pfx",
        home / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / str(DR2_STEAM_APPID) / "pfx",
        home / ".steam" / "root" / "steamapps" / "compatdata" / str(DR2_STEAM_APPID) / "pfx",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _find_proton_wine() -> Optional[Path]:
    """Locate the newest Proton-bundled wine binary."""
    home = Path.home()
    roots = [
        home / ".steam" / "steam" / "steamapps" / "common",
        home / ".local" / "share" / "Steam" / "steamapps" / "common",
    ]
    matches: list[Path] = []
    for r in roots:
        if not r.is_dir():
            continue
        for entry in sorted(r.iterdir()):
            if not entry.name.startswith("Proton"):
                continue
            wine = entry / "files" / "bin" / "wine"
            if wine.is_file():
                matches.append(wine)
    if not matches:
        return None
    # Sort by parent dir name (e.g. "Proton 10.0") so newer Proton wins.
    matches.sort(key=lambda p: p.parent.parent.parent.name)
    return matches[-1]


def install_cert_into_dr2_prefix(cert_path: Path) -> tuple[bool, str]:
    """Trust the cert inside DR2's Proton/Wine prefix. Returns (ok, message).

    The message is either a success summary or a copy-pasteable manual command.
    """
    # Prefer protontricks-launch when available — it handles prefix discovery
    # and picks the correct Proton wine for this AppID automatically.
    if shutil.which("protontricks-launch"):
        try:
            result = subprocess.run(
                [
                    "protontricks-launch", "--appid", str(DR2_STEAM_APPID),
                    "certutil", "-addstore", "Root", str(cert_path),
                ],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return True, "Cert trusted via protontricks-launch."
        except subprocess.TimeoutExpired:
            pass  # Fall through to the wine-direct path below.

    pfx = _find_dr2_proton_prefix()
    if pfx is None:
        manual = (
            "Could not locate DR2's Proton prefix at "
            "~/.steam/steam/steamapps/compatdata/690790/pfx. "
            "Launch DiRT Rally 2.0 once via Steam (Proton) to create it, then click START again. "
            f"Manual command:\n  WINEPREFIX=<dr2-pfx> wine certutil -addstore Root {cert_path}"
        )
        return False, manual

    wine = _find_proton_wine()
    if wine is None:
        wine_path = shutil.which("wine")
        if wine_path:
            wine = Path(wine_path)
    if wine is None:
        manual = (
            "No Proton wine binary found under ~/.steam/.../common/Proton*/files/bin/wine "
            "and no system 'wine' on PATH. "
            f"Manual command:\n  WINEPREFIX={pfx} wine certutil -addstore Root {cert_path}"
        )
        return False, manual

    env = os.environ.copy()
    env["WINEPREFIX"] = str(pfx)
    env["WINEDLLOVERRIDES"] = "mscoree=,mshtml="
    env.setdefault("WINEDEBUG", "-all")
    result = subprocess.run(
        [str(wine), "certutil", "-addstore", "Root", str(cert_path)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if result.returncode == 0:
        return True, f"Cert trusted in {pfx} via {wine}."
    err = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
    manual = (
        f"wine certutil failed ({err[0]}). Try manually:\n"
        f"  WINEPREFIX={pfx} '{wine}' certutil -addstore Root {cert_path}"
    )
    return False, manual


# ---------------------------------------------------------------------------
# Hosts file
# ---------------------------------------------------------------------------

def _read_hosts() -> str:
    try:
        return HOSTS_FILE.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return HOSTS_FILE.read_text(encoding="latin-1")


def _strip_block(content: str) -> str:
    lines = content.splitlines(keepends=True)
    out, inside = [], False
    for line in lines:
        s = line.strip()
        if s == HOSTS_BEGIN:
            inside = True; continue
        if s == HOSTS_END:
            inside = False; continue
        if not inside:
            out.append(line)
    return "".join(out)


def hosts_configured() -> bool:
    try:
        content = _read_hosts()
    except OSError:
        return False
    return HOSTS_BEGIN in content and REDIRECT_DOMAINS[0] in content


def add_hosts() -> None:
    existing = _read_hosts()
    cleaned = _strip_block(existing).rstrip("\r\n")
    nl = HOSTS_NEWLINE
    block = [HOSTS_BEGIN] + [f"{SERVER_IP}\t{d}" for d in REDIRECT_DOMAINS] + [HOSTS_END]
    new = (cleaned + nl + nl + nl.join(block) + nl) if cleaned else (nl.join(block) + nl)
    HOSTS_FILE.write_bytes(new.encode("utf-8"))


def remove_hosts() -> None:
    existing = _read_hosts()
    cleaned = _strip_block(existing)
    HOSTS_FILE.write_bytes(cleaned.encode("utf-8"))


# ---------------------------------------------------------------------------
# Linux: port-443 capability via setcap
# ---------------------------------------------------------------------------

def _is_pyinstaller_bundle() -> bool:
    return hasattr(sys, "_MEIPASS") or getattr(sys, "frozen", False)


def _install_dir() -> Optional[Path]:
    """The folder holding DR3.exe in an installed (bundled) build.

    None for source runs (`python dr3_app.py`) or any unexpected layout, so
    uninstall can never point at a repo checkout or a foreign directory."""
    if not _is_pyinstaller_bundle():
        return None
    exe = Path(sys.executable)
    if exe.name.lower() != "dr3.exe":
        return None
    return exe.parent


def _elevation_target_binary() -> Path:
    """The binary that needs cap_net_bind_service.

    For PyInstaller --onefile this is the bootloader ELF (sys.executable).
    For source runs (`python dr3_app.py`) this is the python interpreter.
    Either way: setcap on `sys.executable` is correct and scoped.
    """
    return Path(sys.executable).resolve()


def _system_python_for_helper() -> Optional[Path]:
    """A real Python interpreter to run the elevated helper script.

    sys.executable is unsuitable in PyInstaller mode because it's the bundled
    bootloader, not a python that can run an arbitrary .py file. Find the
    system python3 instead. In source mode sys.executable is fine.
    """
    if not _is_pyinstaller_bundle():
        return Path(sys.executable)
    for cand in ("python3", "python"):
        path = shutil.which(cand)
        if path:
            return Path(path)
    for cand in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if Path(cand).is_file():
            return Path(cand)
    return None


def _relaunch_with_auto_start() -> None:
    """Re-exec ourselves so a freshly-granted cap_net_bind_service applies.

    File capabilities only attach to a process at execve() time, so a setcap
    we just performed via pkexec doesn't help the currently-running GUI. We
    pass DR3_AUTO_START so the new instance auto-clicks START.
    """
    env = {**os.environ, "DR3_AUTO_START": "1"}
    # Frozen: [exe]. Source: [python, dr3_app.py]. Passing sys.argv[1:]
    # alone would drop the script path and re-exec a bare interpreter.
    argv = [*_self_invocation_args(), *sys.argv[1:]]
    os.execve(argv[0], argv, env)


def has_port_capability(binary: Path) -> bool:
    getcap = shutil.which("getcap")
    if not getcap:
        return False
    result = subprocess.run([getcap, str(binary)], capture_output=True, text=True)
    return "cap_net_bind_service" in (result.stdout or "")


# ---------------------------------------------------------------------------
# Auto-updater (backlog #51)
#
# The Windows build is a PyInstaller --onedir bundle: DR3.exe plus _internal/
# are locked while the process runs, so files can only be swapped AFTER exit.
# Flow: the GUI checks VERSION_CHECK_URL at startup and every 30 minutes,
# auto-downloads a newer zip into UPDATES_DIR, and on window close spawns a
# detached child of itself (`DR3.exe --apply-update <zip> <parent_pid>`) that
# waits for the parent to die, then swaps the install folder (the previous one
# is kept as DR3.bak-<oldver>, like the VPS keeps .bak zips). No relaunch —
# the next manual start runs the new version.
# ---------------------------------------------------------------------------

def _version_tuple(tag: str) -> Optional[tuple]:
    """Digit segments of a version tag; None when it carries no digits."""
    parts = tuple(int(x) for x in str(tag).lstrip("v").split(".") if x.isdigit())
    return parts or None


def _staged_update() -> Optional[tuple]:
    """Best staged download: ``(path, version_tuple)`` of the newest
    ``DR3-windows-<ver>.zip`` in UPDATES_DIR whose version is NEWER than the
    running one; None when nothing installable is staged."""
    local = _version_tuple(VERSION)
    best: Optional[tuple] = None
    try:
        entries = list(UPDATES_DIR.glob("DR3-windows-*.zip"))
    except OSError:
        return None
    for p in entries:
        ver = _version_tuple(p.name[len("DR3-windows-"):-len(".zip")])
        if not ver or (local is not None and not ver > local):
            continue
        if best is None or ver > best[1]:
            best = (p, ver)
    return best


def _validate_update_zip(path: Path) -> bool:
    """A client zip must be a real archive carrying the onedir bundle —
    top-level ``DR3/`` with ``DR3.exe`` inside (build_exe.py's layout)."""
    import zipfile
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
        return "DR3/DR3.exe" in names
    except Exception:
        return False


def _download_update(url: str, version: str, progress_cb) -> Optional[Path]:
    """Download ``url`` into UPDATES_DIR as ``DR3-windows-<version>.zip``.

    ``progress_cb(done_bytes, total_bytes_or_None)`` is called per chunk from
    the calling thread (the GUI hops to Tk with root.after). Returns the final
    path, or None — a partial/invalid file is removed."""
    import urllib.request
    safe = "".join(c for c in str(version) if c.isalnum() or c in "._-")
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    final = UPDATES_DIR / f"DR3-windows-{safe}.zip"
    part = final.with_name(final.name + ".part")
    try:
        from dr2server.tls_doctor import robust_urlopen
        req = urllib.request.Request(url, headers={"User-Agent": "DR3"})
        with robust_urlopen(req, timeout=15) as resp:
            total = int(resp.headers.get("Content-Length") or 0) or None
            done = 0
            with open(part, "wb") as out:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    progress_cb(done, total)
        if not _validate_update_zip(part):
            return None
        part.replace(final)
        return final
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _wait_for_pid_exit(pid: int, timeout_s: float = 600.0) -> bool:
    """True once the given process has exited (or is already gone)."""
    if pid <= 0:
        return True
    if IS_WIN:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        WAIT_TIMEOUT = 0x102
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return True  # already gone (or inaccessible — treat as gone)
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout_s * 1000)) != WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.5)
    return False


def _apply_update(zip_path: Path, install_dir: Path, parent_pid: int,
                  log=None) -> int:
    """Swap ``install_dir`` for the staged zip (runs as a detached child).

    Returns 0 on success; any nonzero code leaves the install in place so the
    next exit can retry. Takes explicit paths so tests can drive it with temp
    dirs instead of the real bundle. ``log(msg)`` (optional) receives the same
    failure lines that are printed, so a detached child can persist them to
    update.log — its stdout goes to DEVNULL."""
    import zipfile

    def fail(code: int, msg: str) -> int:
        line = (f"[UPDATE] {datetime.now().isoformat(timespec='seconds')} "
                f"failed ({code}): {msg}")
        try:
            print(line)
        except Exception:
            pass
        if log is not None:
            try:
                log(f"failed ({code}): {msg}")
            except Exception:
                pass
        return code

    if not install_dir.exists():
        return fail(4, f"install dir gone ({install_dir}) — uninstalled?")
    ver_str = zip_path.name[len("DR3-windows-"):-len(".zip")]
    if not _wait_for_pid_exit(parent_pid):
        return fail(5, "parent process still running after timeout — aborting swap")
    if not _validate_update_zip(zip_path):
        return fail(6, f"staged zip invalid: {zip_path.name}")

    staging = install_dir.parent / f"DR3.update-{ver_str}"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
        new_root = staging / "DR3"
        if not (new_root / "DR3.exe").is_file():
            return fail(7, "extracted zip is missing DR3/DR3.exe")

        # Keep exactly one backup of the previous install (the VPS keeps
        # .bak-<oldver> zips for the same reason).
        for old_bak in install_dir.parent.glob("DR3.bak-*"):
            if old_bak.is_dir():
                shutil.rmtree(old_bak, ignore_errors=True)
        bak = install_dir.parent / f"DR3.bak-{VERSION}"
        install_dir.rename(bak)
        try:
            new_root.rename(install_dir)
        except OSError:
            bak.rename(install_dir)  # roll back, then report
            raise
    except Exception as exc:
        return fail(8, f"swap failed: {exc}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    try:
        zip_path.unlink()
    except OSError:
        pass
    print(f"[UPDATE] installed v{ver_str} (previous kept as DR3.bak-{VERSION})")
    return 0


def _run_apply_update(zip_arg: str, pid_arg: str) -> int:
    """Entry point for the detached `--apply-update` child.

    Runs BEFORE _setup_file_logging() on purpose: the parent GUI is still
    alive holding debug.log open, and rotating it here would scatter the tail
    of the parent session into debug.prev.log. Logs to update.log instead."""
    log_path = DR3_DIR / "update.log"

    def note(msg: str) -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
        except OSError:
            pass

    note(f"apply-update start zip={zip_arg} parent_pid={pid_arg}")
    install_dir = _install_dir()
    if install_dir is None:
        note("not an installed DR3.exe bundle — nothing to update")
        return 0
    # Our CWD may be inside install_dir (inherited from the GUI, which was
    # launched by double-clicking DR3.exe) — Windows refuses to rename a
    # directory that is any process's current working directory.
    try:
        os.chdir(install_dir.parent)
    except OSError:
        pass
    try:
        rc = _apply_update(Path(zip_arg), install_dir, int(pid_arg), log=note)
    except Exception as exc:
        note(f"unexpected error: {exc}")
        return 9
    note(f"apply-update done rc={rc}")
    if rc == 0 and os.name == "nt":
        # The app just closed itself to install the update — open the install
        # folder so the user can double-click DR3.exe and relaunch.
        try:
            subprocess.Popen(["explorer", str(install_dir)])
            note("install folder opened in Explorer")
        except OSError as exc:
            note(f"could not open install folder: {exc}")
    return rc


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Every key the app reads or writes; anything else in config.json is a
# leftover from a removed feature and gets pruned on load.
KNOWN_CONFIG_KEYS = frozenset({
    "cert_path",
    "key_path",
    "game_token",
    "api_url",
    "easy_setup",
    "streaming_enabled",
    "streaming_interval_seconds",
    "streaming_output_dir",
    "streaming_exit_behavior",
    "streaming_files",
    "verbose_logging",
    "telemetry_enabled",
    "telemetry_port",
    "upstream_ip",
    "auto_start_server",
})


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(config, dict):
                stale = [k for k in config if k not in KNOWN_CONFIG_KEYS]
                if stale:
                    for k in stale:
                        del config[k]
                    save_config(config)
                return config
        except Exception:
            pass
    return {}


def save_config(config: dict) -> None:
    DR3_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


# DR3's own UDP telemetry port. Always present in config.json after first run
# (see ensure_telemetry_defaults); users change it by editing config.json.
DEFAULT_TELEMETRY_PORT = 20999


def ensure_telemetry_defaults(config: dict) -> None:
    """Make sure ``telemetry_port`` is a valid port, defaulting to 20999, and
    persist on first run so the key is visible/editable in config.json."""
    try:
        if int(config.get("telemetry_port") or 0):
            return
    except (TypeError, ValueError):
        pass
    config["telemetry_port"] = DEFAULT_TELEMETRY_PORT
    save_config(config)


# ---------------------------------------------------------------------------
# Elevated helper-script content (Linux: invoked via pkexec).
# Windows uses the in-process `--admin-helper` re-launch path instead.
# ---------------------------------------------------------------------------


def _linux_admin_start_script(setcap_target: Path) -> str:
    """Helper Python script (run via pkexec) that:
       1. Grants cap_net_bind_service to setcap_target.
       2. Writes the /etc/hosts block.
    """
    return (
        "import subprocess\n"
        f"target = {str(setcap_target)!r}\n"
        "r = subprocess.run(['setcap', 'cap_net_bind_service=+ep', target], capture_output=True, text=True)\n"
        "print('setcap:', 'ok' if r.returncode == 0 else 'fail ' + (r.stderr or '').strip())\n"
        f"hosts = {str(HOSTS_FILE)!r}\n"
        "try:\n"
        "    content = open(hosts, encoding='utf-8').read()\n"
        "except UnicodeDecodeError:\n"
        "    content = open(hosts, encoding='latin-1').read()\n"
        "lines, out, inside = content.splitlines(True), [], False\n"
        "for l in lines:\n"
        "    s = l.strip()\n"
        f"    if s == {HOSTS_BEGIN!r}: inside = True; continue\n"
        f"    if s == {HOSTS_END!r}: inside = False; continue\n"
        "    if not inside: out.append(l)\n"
        "cleaned = ''.join(out).rstrip('\\n')\n"
        f"block = '\\n'.join([{HOSTS_BEGIN!r}] + "
        f"[{SERVER_IP!r} + '\\t' + d for d in {REDIRECT_DOMAINS!r}] + "
        f"[{HOSTS_END!r}])\n"
        "new = (cleaned + '\\n\\n' + block + '\\n') if cleaned else (block + '\\n')\n"
        "open(hosts, 'wb').write(new.encode('utf-8'))\n"
        "print('hosts: ok')\n"
    )


def _linux_admin_stop_script() -> str:
    return (
        f"hosts = {str(HOSTS_FILE)!r}\n"
        "try:\n"
        "    content = open(hosts, encoding='utf-8').read()\n"
        "except UnicodeDecodeError:\n"
        "    content = open(hosts, encoding='latin-1').read()\n"
        "lines, out, inside = content.splitlines(True), [], False\n"
        "for l in lines:\n"
        "    s = l.strip()\n"
        f"    if s == {HOSTS_BEGIN!r}: inside = True; continue\n"
        f"    if s == {HOSTS_END!r}: inside = False; continue\n"
        "    if not inside: out.append(l)\n"
        "open(hosts, 'wb').write(''.join(out).encode('utf-8'))\n"
        "print('ok')\n"
    )


def tel_status_text(server_up: bool, enabled: bool, holder: dict, listener,
                    game=None) -> Optional[tuple]:
    """(text, color-key) for the Main-tab telemetry row; None hides it.

    States: server stopped (None), disabled in config.json, bind failed
    (holder carries the busy port), connected (fresh packets within 20 s), or —
    with no recent data — a game-aware hint from ``game`` (the parsed
    hardware_settings_config.xml ``<udp>`` element as read_game_udp() returns):
    file missing/unreadable, UDP disabled, game already sending to our port, or
    game on a different port (forward in SimHub). ``listener`` may be any object
    exposing .port / .packets_seen / .last_packet_wall (the TelemetryListener
    does); ``game`` may be None.
    """
    if not server_up:
        return None
    if not enabled:
        return ("Telemetry disabled in config.json", "muted")
    if listener is None:
        port = holder.get("port") or "?"
        return (f"\u2717 Telemetry unavailable \u2014 UDP :{port} in use", "bad")
    port = listener.port
    fresh = (time.time() - listener.last_packet_wall) <= 20.0
    if fresh:
        return (f"\u2713 Telemetry connected on :{port}", "good")
    if listener.packets_seen > 0:
        return (f"Telemetry listening on :{port} \u2014 no recent data", "muted")
    # No data yet and never seen any — give a game-aware hint.
    if game is None or not game.get("present"):
        return (f"Telemetry waiting for data on :{port}", "muted")
    if not game.get("enabled"):
        return ("Game UDP disabled \u2014 DR3 enabled it; restart the game", "warn")
    gport = game.get("port")
    if gport is None or gport == port:
        return (f"Listening on the game's port :{port} \u2014 start a stage to see data",
                "muted")
    return (f"Game sends UDP to :{gport}, DR3 listens on :{port} \u2014 forward in "
            f"SimHub or set telemetry_port to {gport}", "warn")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    config = load_config()
    ensure_telemetry_defaults(config)
    server_thread: Optional[threading.Thread] = None
    server_running = threading.Event()
    shutdown_flag = threading.Event()
    # Mutable holder so closures defined before the writer exists can still
    # reach it after server_worker() instantiates one.
    streaming_writer: dict = {"instance": None}
    # Same trick for the dispatcher — we need to flip verbose_logging on
    # the live instance when the user toggles the checkbox in the Logs tab.
    dispatcher_holder: dict = {"instance": None}
    # And for the App, so the same toggle can gate request-capture saving
    # (app.capturing) on the live server instance.
    app_holder: dict = {"instance": None}
    # Telemetry listener state for the status indicator — which port we tried
    # and whether the bind failed, so "disabled in config" can be told apart
    # from "port busy".
    telemetry_holder: dict = {"port": None, "failed": False}

    # Colors (dark tool window; the website uses the retro rally poster theme)
    BG = "#181C14"
    BG_CARD = "#3C3D37"
    BG_ELEVATED = "#3C3D37"
    ACCENT = "#ECDFCC"
    ACCENT_BRIGHT = "#697565"
    GREEN = "#ECDFCC"
    RED = "#697565"
    TEXT = "#ECDFCC"
    MUTED = "#697565"
    BORDER = "#697565"
    WARN = "#afff95"
    _TEL_COLORS = {"good": GREEN, "bad": RED, "muted": MUTED, "warn": WARN}

    # --- Window setup ---
    root = tk.Tk()
    root.title(f"DR3 v{VERSION}")
    root.resizable(False, False)
    root.configure(bg=BG)

    runtime_icon_file = _runtime_icon_path()
    if runtime_icon_file is not None:
        try:
            root._app_icon = tk.PhotoImage(file=str(runtime_icon_file))
            root.iconphoto(True, root._app_icon)
        except tk.TclError:
            pass

    # Center on screen (tall enough that the update banner never forces a
    # scrollbar on the Main tab)
    w, h = 440, 610
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    style = ttk.Style()
    try:
        style.theme_use("default")
    except tk.TclError:
        pass
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=BG_ELEVATED,
        foreground=MUTED,
        padding=(14, 6),
        font=(UI_FONT, 9, "bold"),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", BG)],
        foreground=[("selected", ACCENT)],
    )

    # --- Auto-updater (backlog #51) ---
    # Gold bar above the tabs. Installed Windows builds: a newer version is
    # downloaded automatically and installed by a detached child on exit (see
    # _apply_update / _run_apply_update). Source runs keep the old
    # click-to-download banner. The bar is only clickable in the manual
    # fallback state — while downloading or ready there is nothing to fetch,
    # so clicking does nothing.
    GOLD = ACCENT
    update_bar = tk.Frame(root, bg=GOLD)
    update_label = tk.Label(update_bar, text="", font=(UI_FONT, 9, "bold"),
                            fg=BG, bg=GOLD, cursor="hand2")
    update_label.pack(padx=10, pady=4)
    update_state = {"mode": "idle"}  # idle | downloading | ready | manual

    def _set_update_bar(text: str, url: Optional[str] = None):
        def show():
            if not text:
                return
            update_label.configure(text=text)
            if url:
                update_label.bind("<Button-1>", lambda e: webbrowser.open(url))
                update_label.configure(cursor="hand2")
            else:
                update_label.unbind("<Button-1>")
                update_label.configure(cursor="arrow")
            if not update_bar.winfo_ismapped():
                update_bar.pack(fill="x", padx=20, pady=(5, 0), before=notebook)
        try:
            root.after(0, show)
        except RuntimeError:
            pass  # Tk event loop already gone (shutdown race) — skip the UI update

    def _check_for_updates():
        import urllib.request
        try:
            if not VERSION_CHECK_URL:
                return
            from dr2server.tls_doctor import robust_urlopen
            req = urllib.request.Request(
                VERSION_CHECK_URL, headers={"User-Agent": "DR3"})
            with robust_urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            remote_tag = str(data.get("version", "")).lstrip("v")
            dl_url = data.get("download_url", "")
            if not remote_tag or not dl_url:
                return
            remote = _version_tuple(remote_tag)
            local = _version_tuple(VERSION)
            if not (remote and local and remote > local):
                return
            manual_text = f"Update available: v{remote_tag}  —  click to download"
            if _install_dir() is None:
                # Source run / foreign layout — nothing to auto-install into.
                update_state["mode"] = "manual"
                _set_update_bar(manual_text, dl_url)
                return
            staged = _staged_update()
            if staged and staged[1] == remote:
                # Nothing to fetch — the install happens on exit, so the bar
                # is informational only (no click action).
                update_state["mode"] = "ready"
                _set_update_bar(
                    f"Update v{remote_tag} ready — installs when DR3 closes")
                return

            def on_progress(done, total):
                pct = f"{int(done * 100 / total)}%" if total else ""
                _set_update_bar(f"Downloading update v{remote_tag} — {pct}")

            def download():
                path = _download_update(dl_url, remote_tag, on_progress)
                if path is None:
                    update_state["mode"] = "manual"
                    _set_update_bar(manual_text, dl_url)
                    return
                # Only the newest staged zip can install — drop older ones.
                for old in UPDATES_DIR.glob("DR3-windows-*.zip"):
                    if old != path:
                        try:
                            old.unlink()
                        except OSError:
                            pass
                update_state["mode"] = "ready"
                _set_update_bar(
                    f"Update v{remote_tag} ready — installs when DR3 closes")

            update_state["mode"] = "downloading"
            threading.Thread(target=download, daemon=True).start()
        except Exception:
            pass  # Silent fail — update check is best-effort

    update_tick = {"id": None}

    def _update_check_tick():
        if update_state["mode"] != "downloading":
            threading.Thread(target=_check_for_updates, daemon=True).start()
        update_tick["id"] = root.after(UPDATE_CHECK_INTERVAL_MS, _update_check_tick)

    threading.Thread(target=_check_for_updates, daemon=True).start()
    update_tick["id"] = root.after(UPDATE_CHECK_INTERVAL_MS, _update_check_tick)

    # --- Tabbed layout (each tab is scrollable so long content stays
    #     reachable at the fixed window size) ---
    def _scroll_tab(parent):
        """Return ``(outer, inner)``: ``inner`` is where a tab's widgets pack
        into; it sits on a Canvas with a vertical scrollbar (shown only when
        the content is taller than the window) so long tabs stay reachable at
        the fixed window size."""
        outer = tk.Frame(parent, bg=BG)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        inner = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.pack(side="left", fill="both", expand=True)

        def _sync_width(_e):
            # Keep the content frame as wide as the visible canvas so
            # fill="x" children span the whole tab (not just their natural width).
            canvas.itemconfigure(win_id, width=canvas.winfo_width())

        def _update_scrollbar():
            # Show the scrollbar only while the content overflows the visible
            # area. Toggling changes the canvas width and re-triggers this
            # check; the outcome is stable because content height never shrinks
            # as the frame narrows (wrapped labels only grow).
            bbox = canvas.bbox("all")
            needs = bool(bbox) and bbox[3] > canvas.winfo_height() + 2
            if needs and not sb.winfo_ismapped():
                sb.pack(side="right", fill="y")
            elif not needs and sb.winfo_ismapped():
                sb.pack_forget()

        def _on_inner_cfg(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            _sync_width(_e)
            _update_scrollbar()

        inner.bind("<Configure>", _on_inner_cfg)
        canvas.bind("<Configure>", _sync_width)

        def _wheel(event):
            # Only scroll when the tab actually overflows (scrollbar shown);
            # a fitting tab has nothing to scroll and must not move.
            if not sb.winfo_ismapped():
                return
            delta = getattr(event, "delta", 0) or \
                (120 if getattr(event, "num", 0) == 4 else
                 -120 if getattr(event, "num", 0) == 5 else 0)
            if delta:
                canvas.yview_scroll(-1 if delta > 0 else 1, "units")

        for w in (canvas, inner):
            w.bind("<MouseWheel>", _wheel)
            w.bind("<Button-4>", _wheel)
            w.bind("<Button-5>", _wheel)

        def _bind_wheel_everywhere():
            # Tk only looks up bindings on the event widget itself, its class,
            # the toplevel and "all" — never intermediate frames — so wheeling
            # over a card/row (children of `inner`) never reached the binding
            # above. Bind every descendant once all tab content exists.
            def _rec(w):
                w.bind("<MouseWheel>", _wheel)
                w.bind("<Button-4>", _wheel)
                w.bind("<Button-5>", _wheel)
                for c in w.winfo_children():
                    _rec(c)
            _rec(inner)
        return outer, inner, _bind_wheel_everywhere

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    main_tab_outer, main_tab, _main_wheel = _scroll_tab(notebook)
    streaming_tab_outer, streaming_tab, _streaming_wheel = _scroll_tab(notebook)
    advanced_tab_outer, advanced_tab, _advanced_wheel = _scroll_tab(notebook)
    notebook.add(main_tab_outer, text="Main")
    notebook.add(streaming_tab_outer, text="Overlay")
    notebook.add(advanced_tab_outer, text="Logs")

    # --- Header ---
    header = tk.Frame(main_tab, bg=BG)
    header.pack(fill="x", padx=20, pady=(20, 5))
    tk.Label(header, text="DR3", font=(UI_FONT, 18, "bold"),
              fg=ACCENT, bg=BG).pack(side="left")
    tk.Label(header, text="Community rally server — byagersi.com", font=(UI_FONT, 9),
             fg=MUTED, bg=BG).pack(side="left", padx=(10, 0), pady=(6, 0))

    # --- Token config ---
    token_frame = tk.Frame(main_tab, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
    token_frame.pack(fill="x", padx=20, pady=(10, 5))

    tk.Label(token_frame, text="GAME TOKEN", font=(UI_FONT, 8, "bold"),
             fg=MUTED, bg=BG_CARD).pack(anchor="w", padx=12, pady=(8, 2))

    token_input_frame = tk.Frame(token_frame, bg=BG_CARD)
    token_input_frame.pack(fill="x", padx=12, pady=(0, 8))

    token_var = tk.StringVar(value=config.get("game_token", ""))
    token_entry = tk.Entry(
        token_input_frame, textvariable=token_var, font=(MONO_FONT, 9),
        bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat",
        highlightbackground=BORDER, highlightthickness=1,
        show="•",
    )
    token_entry.pack(side="left", fill="x", expand=True, ipady=4)

    def toggle_token_visibility():
        if token_entry.cget("show") == "":
            token_entry.configure(show="•")
            show_btn.configure(text="Show")
        else:
            token_entry.configure(show="")
            show_btn.configure(text="Hide")

    def save_token():
        t = token_var.get().strip()
        config["game_token"] = t
        config.setdefault("api_url", API_URL)
        if not t:
            # No token -> auto-start can't be enabled; force it off.
            auto_start_var.set(False)
            config["auto_start_server"] = False
        save_config(config)
        if not t:
            token_status_label.configure(text="No token", fg=ACCENT)
            auto_cb.configure(state="disabled")
            return
        auto_cb.configure(state="normal")
        token_status_label.configure(text="Testing token...", fg=MUTED)
        save_btn.configure(state="disabled")

        def verify():
            from dr2server.api_client import UpstreamClient
            client = UpstreamClient(base_url=config.get("api_url", API_URL), api_token=t)
            username = client.test_token()
            if username:
                root.after(0, lambda u=username: token_status_label.configure(
                    text=f"\u2713 Token working \u2014 linked to {u}", fg=GREEN))
            else:
                root.after(0, lambda: token_status_label.configure(
                    text="\u2717 Token invalid or server unreachable", fg=RED))
            root.after(0, lambda: save_btn.configure(state="normal"))

        threading.Thread(target=verify, daemon=True).start()

    save_btn = tk.Button(
        token_input_frame, text="Save", font=(UI_FONT, 8, "bold"),
        bg=ACCENT, fg=BG, activebackground=ACCENT_BRIGHT, activeforeground=BG,
        relief="flat", cursor="hand2", padx=10, pady=2,
        command=save_token,
    )
    save_btn.pack(side="right", padx=(5, 0))

    show_btn = tk.Button(
        token_input_frame, text="Show", font=(UI_FONT, 8, "bold"),
        bg=BG_ELEVATED, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
        relief="flat", cursor="hand2", padx=10, pady=2,
        command=toggle_token_visibility,
    )
    show_btn.pack(side="right", padx=(5, 0))

    token_status_label = tk.Label(token_frame, text="", font=(UI_FONT, 8), fg=MUTED, bg=BG_CARD)
    token_status_label.pack(anchor="w", padx=12, pady=(0, 6))

    if config.get("game_token"):
        token_status_label.configure(text="Token configured", fg=GREEN)
    else:
        token_status_label.configure(text="Get token at byagersi.com/rally/dashboard", fg=MUTED)

    # --- Status ---
    status_frame = tk.Frame(main_tab, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
    status_frame.pack(fill="x", padx=20, pady=5)

    status_row = tk.Frame(status_frame, bg=BG_CARD)
    status_row.pack(fill="x")

    status_dot = tk.Label(status_row, text="\u25cf", font=(UI_FONT, 14),
                          fg=MUTED, bg=BG_CARD)
    status_dot.pack(side="left", padx=(15, 5), pady=10)

    status_label = tk.Label(status_row, text="Stopped", font=(UI_FONT, 12, "bold"),
                            fg=TEXT, bg=BG_CARD)
    status_label.pack(side="left", pady=10)

    status_detail = tk.Label(status_row, text="", font=(UI_FONT, 9),
                             fg=MUTED, bg=BG_CARD)
    status_detail.pack(side="right", padx=15, pady=10)

    # Telemetry row — visible while the server runs; surfaces the silent
    # failure modes (port busy / no data arriving) that used to only show up
    # as missing splits.
    tel_label = tk.Label(status_frame, text="", font=(UI_FONT, 9),
                         fg=MUTED, bg=BG_CARD, anchor="w")

    tel_tick = {"id": None}
    _game_udp_cache = {"ts": 0.0, "val": None}

    def _game_udp():
        # Re-read the game's <udp> element at most once every 10 s — the file is
        # stable while the game runs and we don't want to touch disk each tick.
        now = time.time()
        if now - _game_udp_cache["ts"] > 10.0:
            from dr2server.telemetry import read_game_udp
            _game_udp_cache["val"] = read_game_udp()
            _game_udp_cache["ts"] = now
        return _game_udp_cache["val"]

    def _update_tel_indicator():
        app = app_holder.get("instance")
        listener = (getattr(app.dispatcher, "telemetry", None)
                    if app is not None else None)
        state = tel_status_text(
            server_up=server_running.is_set(),
            enabled=bool(config.get("telemetry_enabled", True)),
            holder=telemetry_holder,
            listener=listener,
            game=_game_udp() if server_running.is_set() else None,
        )
        if state is None:
            tel_label.pack_forget()
        else:
            text, color = state
            tel_label.configure(text=text, fg=_TEL_COLORS[color])
            tel_label.pack(fill="x", padx=15, pady=(0, 8))
        tel_tick["id"] = root.after(2000, _update_tel_indicator)

    tel_tick["id"] = root.after(2000, _update_tel_indicator)

    # --- Log area ---
    log_frame = tk.Frame(main_tab, bg=BG)
    log_frame.pack(fill="both", expand=True, padx=20, pady=(5, 5))

    log_text = tk.Text(log_frame, height=5, bg=BG, fg=MUTED,
                       font=(MONO_FONT, 8), relief="flat", wrap="word",
                       state="disabled", borderwidth=0)
    log_text.pack(fill="both", expand=True)

    def log(msg: str):
        log_text.configure(state="normal")
        log_text.insert("end", msg + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")
        if _LOG_FILE is not None:
            try:
                from datetime import datetime as _dt
                _LOG_FILE.write(f"[GUI {_dt.now().strftime('%H:%M:%S')}] {msg}\n")
                _LOG_FILE.flush()
            except Exception:
                pass

    def _normalize_game_udp():
        """Silently enable/point the game's <udp> telemetry at DR3's port so
        telemetry works out of the box (writes a .bak backup + a log line only
        when something actually changes)."""
        if not IS_WIN or not bool(config.get("telemetry_enabled", True)):
            return
        from dr2server.telemetry import ensure_game_udp_for_dr3
        try:
            tp = int(config.get("telemetry_port") or 0)
        except (TypeError, ValueError):
            tp = 0
        if not tp:
            tp = DEFAULT_TELEMETRY_PORT
        ensure_game_udp_for_dr3(tp, log=log)

    # Run once shortly after boot so the game config is already correct by the
    # time the user launches the game (no restart needed for a fresh fix).
    root.after(600, _normalize_game_udp)

    def _cert_store_scan():
        from dr2server.tls_doctor import (expired_let_encrypt_certs,
                                          removal_commands,
                                          scan_windows_cert_store)
        try:
            bad = expired_let_encrypt_certs(scan_windows_cert_store())
        except Exception:
            return
        if not bad:
            return
        lines = [
            "WARNING: your Windows certificate store contains EXPIRED Let's Encrypt",
            f"certificates ({len(bad)} found) that break Python's TLS. DR3 falls back to its",
            "built-in Let's Encrypt roots so clubs keep working, but clean up the store:",
        ]
        for e in bad:
            lines.append(f"  {e['subject']} (expired {e['not_after']:%Y-%m-%d})"
                         f" in {e['store']}")
        lines.extend("  " + c for c in removal_commands(bad))
        lines.append("Details: https://byagersi.com/rally/install#cert-troubleshooting")

        def show():
            for line in lines:
                log(line)
        try:
            root.after(0, show)
        except Exception:
            pass  # window already gone (shutdown race) - debug.log still gets it
        for line in lines:
            print(f"[TLS] {line}")

    threading.Thread(target=_cert_store_scan, daemon=True).start()

    # --- Buttons ---
    btn_frame = tk.Frame(main_tab, bg=BG)
    btn_frame.pack(fill="x", padx=20, pady=(5, 8))

    easy_setup_var = tk.BooleanVar(value=config.get("easy_setup", True))
    easy_cb = tk.Checkbutton(
        btn_frame, text="Easy Setup Mode (Requires Admin)",
        variable=easy_setup_var,
        bg=BG, fg=TEXT, activebackground=BG, activeforeground=TEXT,
        selectcolor=BG, font=(UI_FONT, 9),
        highlightthickness=0, bd=0, anchor="w",
    )
    easy_cb.pack(fill="x", pady=(0, 2))

    manual_link = tk.Label(
        btn_frame, text="Manual setup instructions \u2192",
        font=(UI_FONT, 8, "underline"),
        fg=ACCENT, bg=BG, cursor="hand2", anchor="w",
    )
    manual_link.bind(
        "<Button-1>",
        lambda e: webbrowser.open("https://byagersi.com/rally/install#manual"),
    )

    def _update_easy_setup():
        if easy_setup_var.get():
            manual_link.pack_forget()
        else:
            manual_link.pack(fill="x", pady=(0, 5), before=start_btn)
        config["easy_setup"] = easy_setup_var.get()
        save_config(config)

    easy_cb.configure(command=_update_easy_setup)

    start_btn = tk.Button(
        btn_frame, text="START", font=(UI_FONT, 11, "bold"),
        bg=GREEN, fg=BG, activebackground=ACCENT_BRIGHT, activeforeground=BG,
        relief="flat", cursor="hand2", padx=20, pady=10,
    )
    start_btn.pack(fill="x", pady=(0, 5))

    # Show link below checkbox if starting unchecked
    if not easy_setup_var.get():
        manual_link.pack(fill="x", pady=(0, 5), before=start_btn)

    stop_btn = tk.Button(
        btn_frame, text="STOP", font=(UI_FONT, 10, "bold"),
        bg=BG_ELEVATED, fg=MUTED, activebackground=BORDER, activeforeground=TEXT,
        relief="flat", cursor="hand2", padx=20, pady=8,
        state="disabled",
    )
    stop_btn.pack(side="left")

    # Shares STOP's row (side packing) so the Main tab gains no height.
    # Auto-start is opt-in (off by default on a fresh install) and can only be
    # enabled once a game token is configured — without one the server has
    # nothing to connect to, so auto-starting would just open the dashboard.
    _has_token = bool(config.get("game_token"))
    auto_start_var = tk.BooleanVar(
        value=bool(config.get("auto_start_server", False)) and _has_token)
    auto_cb = tk.Checkbutton(
        btn_frame, text="Start automatically when DR3 opens",
        variable=auto_start_var, bg=BG, fg=TEXT,
        activebackground=BG, activeforeground=TEXT, selectcolor=BG,
        font=(UI_FONT, 9), highlightthickness=0, bd=0, anchor="w",
    )
    auto_cb.pack(side="right")
    if not _has_token:
        auto_cb.configure(state="disabled")

    def _update_auto_start():
        config["auto_start_server"] = auto_start_var.get()
        save_config(config)

    auto_cb.configure(command=_update_auto_start)

    # --- Footer ---
    footer = tk.Frame(main_tab, bg=BG)
    footer.pack(fill="x", padx=20, pady=(0, 12))

    dash_link = tk.Label(footer, text="byagersi.com/rally/dashboard", font=(UI_FONT, 8, "underline"),
                          fg=ACCENT, bg=BG, cursor="hand2")
    dash_link.pack(side="right")
    dash_link.bind("<Button-1>", lambda e: webbrowser.open(DASHBOARD_URL))

    # --- Overlay tab ---
    # Overlay output folder. Defaults to ~/dr3, but the user can
    # repoint it on this tab; the choice is persisted in config.json under
    # "streaming_output_dir" so it survives restarts.
    DEFAULT_OVERLAY_DIR = Path.home() / "dr3"
    _saved_overlay_dir = str(config.get("streaming_output_dir") or "").strip()
    streaming_dir_var = tk.StringVar(
        value=_saved_overlay_dir or str(DEFAULT_OVERLAY_DIR))

    def _overlay_dir() -> Path:
        val = (streaming_dir_var.get() or "").strip()
        return Path(val).expanduser() if val else DEFAULT_OVERLAY_DIR

    stream_header = tk.Frame(streaming_tab, bg=BG)
    stream_header.pack(fill="x", padx=20, pady=(20, 5))
    tk.Label(stream_header, text="Text Files", font=(UI_FONT, 16, "bold"),
             fg=ACCENT, bg=BG).pack(side="left")
    tk.Label(stream_header, text="For use in OBS or Simhub", font=(UI_FONT, 9),
             fg=MUTED, bg=BG).pack(side="left", padx=(10, 0), pady=(5, 0))

    stream_card = tk.Frame(streaming_tab, bg=BG_CARD,
                           highlightbackground=BORDER, highlightthickness=1)
    stream_card.pack(fill="x", padx=20, pady=(10, 5))

    streaming_enabled_var = tk.BooleanVar(
        value=bool(config.get("streaming_enabled", False)))
    enable_cb = tk.Checkbutton(
        stream_card, text="Generate overlay files while server is running",
        variable=streaming_enabled_var,
        bg=BG_CARD, fg=TEXT, activebackground=BG_CARD, activeforeground=TEXT,
        selectcolor=BG, font=(UI_FONT, 10),
        highlightthickness=0, bd=0, anchor="w",
    )
    enable_cb.pack(fill="x", padx=12, pady=(10, 4))

    interval_row = tk.Frame(stream_card, bg=BG_CARD)
    interval_row.pack(fill="x", padx=12, pady=(2, 8))
    tk.Label(interval_row, text="Update interval (seconds, min 2):",
             font=(UI_FONT, 9), fg=TEXT, bg=BG_CARD).pack(side="left")
    streaming_interval_var = tk.IntVar(
        value=max(2, int(config.get("streaming_interval_seconds", 5) or 5)))
    interval_spin = tk.Spinbox(
        interval_row, from_=2, to=60, increment=1, width=5,
        textvariable=streaming_interval_var,
        font=(MONO_FONT, 10), bg=BG, fg=TEXT, insertbackground=TEXT,
        relief="flat", highlightbackground=BORDER, highlightthickness=1,
        buttonbackground=BG_ELEVATED,
    )
    interval_spin.pack(side="left", padx=(8, 0), ipady=2)

    # Exit behavior: what happens to the overlay files when the server stops.
    # "clear" blanks them in place (OBS flags missing files as an error on
    # startup, see issue #36), "delete" removes them. Persisted in config.json
    # under "streaming_exit_behavior"; default is "clear".
    from dr2server.streaming import DEFAULT_EXIT_BEHAVIOR, EXIT_BEHAVIOR_DELETE

    _EXIT_BEHAVIOR_LABELS = {
        DEFAULT_EXIT_BEHAVIOR: "Clear files (leave them blank)",
        EXIT_BEHAVIOR_DELETE: "Delete files",
    }
    _EXIT_LABEL_TO_VALUE = {v: k for k, v in _EXIT_BEHAVIOR_LABELS.items()}
    _saved_exit = str(config.get("streaming_exit_behavior") or "").strip()
    if _saved_exit not in _EXIT_BEHAVIOR_LABELS:
        _saved_exit = DEFAULT_EXIT_BEHAVIOR
    streaming_exit_var = tk.StringVar(value=_EXIT_BEHAVIOR_LABELS[_saved_exit])

    def _exit_behavior() -> str:
        return _EXIT_LABEL_TO_VALUE.get(
            streaming_exit_var.get(), DEFAULT_EXIT_BEHAVIOR)

    exit_row = tk.Frame(stream_card, bg=BG_CARD)
    exit_row.pack(fill="x", padx=12, pady=(0, 10))
    tk.Label(exit_row, text="When the server stops:",
             font=(UI_FONT, 9), fg=TEXT, bg=BG_CARD).pack(side="left")
    exit_menu = tk.OptionMenu(
        exit_row, streaming_exit_var, *_EXIT_BEHAVIOR_LABELS.values())
    exit_menu.configure(
        font=(UI_FONT, 9), bg=BG_ELEVATED, fg=TEXT,
        activebackground=BORDER, activeforeground=TEXT,
        relief="flat", cursor="hand2",
        highlightthickness=1, highlightbackground=BORDER,
    )
    exit_menu["menu"].configure(
        font=(UI_FONT, 9), bg=BG_ELEVATED, fg=TEXT,
        activebackground=ACCENT, activeforeground=BG,
    )
    exit_menu.pack(side="left", padx=(8, 0))

    path_card = tk.Frame(streaming_tab, bg=BG_CARD,
                         highlightbackground=BORDER, highlightthickness=1)
    path_card.pack(fill="x", padx=20, pady=5)
    tk.Label(path_card, text="OUTPUT FOLDER", font=(UI_FONT, 8, "bold"),
             fg=MUTED, bg=BG_CARD).pack(anchor="w", padx=12, pady=(8, 2))
    tk.Label(path_card, textvariable=streaming_dir_var, font=(MONO_FONT, 9),
             fg=TEXT, bg=BG_CARD, anchor="w", justify="left",
             wraplength=345).pack(fill="x", padx=12, pady=(0, 4))
    path_row = tk.Frame(path_card, bg=BG_CARD)
    path_row.pack(fill="x", padx=12, pady=(0, 8))

    def _open_overlay_dir():
        out_dir = _overlay_dir()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            if IS_WIN:
                os.startfile(str(out_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(out_dir)])
        except Exception as exc:
            log(f"Could not open overlay folder: {exc}")

    def _choose_overlay_dir():
        from tkinter import filedialog
        chosen = filedialog.askdirectory(
            parent=streaming_tab,
            initialdir=str(_overlay_dir()),
            title="Choose overlay output folder",
            mustexist=False,
        )
        if chosen:
            # Setting the var fires the trace -> _apply_streaming_state, which
            # persists the new path and repoints a running writer.
            streaming_dir_var.set(str(Path(chosen)))

    tk.Button(
        path_row, text="Open folder", font=(UI_FONT, 8, "bold"),
        bg=ACCENT, fg=BG, activebackground=ACCENT_BRIGHT, activeforeground=BG,
        relief="flat", cursor="hand2", padx=10, pady=2,
        command=_open_overlay_dir,
    ).pack(side="right")
    tk.Button(
        path_row, text="Change...", font=(UI_FONT, 8, "bold"),
        bg=BG_ELEVATED, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
        relief="flat", cursor="hand2", padx=10, pady=2,
        command=_choose_overlay_dir,
    ).pack(side="right", padx=(0, 6))

    from dr2server.streaming import FILES as STREAMING_FILES

    files_card = tk.Frame(streaming_tab, bg=BG_CARD,
                          highlightbackground=BORDER, highlightthickness=1)
    files_card.pack(fill="x", padx=20, pady=5)
    tk.Label(files_card, text="FILES GENERATED", font=(UI_FONT, 8, "bold"),
             fg=MUTED, bg=BG_CARD).pack(anchor="w", padx=12, pady=(8, 4))
    _file_rows = [
        ("club",        "Club name you're racing in"),
        ("club_owner",  "Creator of the current club"),
        ("members",     "Member count, e.g. '123 members'"),
        ("stages",      "Stage progress, e.g. '5 of 12' (whole event)"),
        ("stage_name",  "Current stage name, e.g. 'SS3 Pirkkalaharju'"),
        ("location",    "Event location, e.g. 'Spain'"),
        ("car",         "Selected vehicle name"),
        ("car_class",   "Event class, e.g. 'Group B'"),
        ("leaderboard", "Top 10 leaderboard (polls API)"),
    ]
    saved_files_cfg = config.get("streaming_files") or {}
    streaming_file_vars: dict = {}
    for key, desc in _file_rows:
        var = tk.BooleanVar(value=bool(saved_files_cfg.get(key, True)))
        streaming_file_vars[key] = var
        row = tk.Frame(files_card, bg=BG_CARD)
        row.pack(fill="x", padx=12, pady=1)
        tk.Checkbutton(
            row, variable=var,
            bg=BG_CARD, fg=TEXT,
            activebackground=BG_CARD, activeforeground=TEXT,
            selectcolor=BG, highlightthickness=0, bd=0,
        ).pack(side="left")
        tk.Label(row, text=STREAMING_FILES[key], font=(MONO_FONT, 9),
                 fg=TEXT, bg=BG_CARD, width=22, anchor="w").pack(side="left")
        tk.Label(row, text=desc, font=(UI_FONT, 8),
                 fg=MUTED, bg=BG_CARD, anchor="w", justify="left",
                 wraplength=190).pack(side="left")

    streaming_status_label = tk.Label(
        streaming_tab,
        text=("Writing every {}s".format(
                   max(2, int(config.get("streaming_interval_seconds", 5) or 5)))
               if config.get("streaming_enabled", False) else "Idle"),
        font=(UI_FONT, 9), fg=MUTED, bg=BG, anchor="w",
    )
    streaming_status_label.pack(fill="x", padx=20, pady=(8, 12))

    def _enabled_files_set() -> set:
        return {k for k, v in streaming_file_vars.items() if v.get()}

    def _apply_streaming_state(*_):
        enabled = bool(streaming_enabled_var.get())
        try:
            interval = max(2, int(streaming_interval_var.get() or 5))
        except (tk.TclError, ValueError):
            interval = 5
        files_enabled = _enabled_files_set()
        out_dir = _overlay_dir()
        exit_behavior = _exit_behavior()
        config["streaming_enabled"] = enabled
        config["streaming_interval_seconds"] = interval
        config["streaming_output_dir"] = str(out_dir)
        config["streaming_exit_behavior"] = exit_behavior
        config["streaming_files"] = {k: (k in files_enabled) for k in streaming_file_vars}
        save_config(config)
        inst = streaming_writer["instance"]
        if inst is not None:
            inst.set_enabled(files_enabled)
            inst.set_exit_behavior(exit_behavior)
        if enabled:
            if inst is not None:
                if inst.is_running():
                    inst.set_interval(interval)
                    inst.set_output_dir(out_dir)
                else:
                    inst.start(interval=interval, output_dir=out_dir)
                streaming_status_label.configure(
                    text=f"Writing every {interval}s ({len(files_enabled)} files)",
                    fg=GREEN)
            else:
                streaming_status_label.configure(
                    text=f"Enabled, will start with server (every {interval}s)",
                    fg=MUTED)
        else:
            if inst is not None and inst.is_running():
                inst.stop()
            streaming_status_label.configure(text="Idle", fg=MUTED)

    streaming_enabled_var.trace_add("write", _apply_streaming_state)
    streaming_interval_var.trace_add("write", _apply_streaming_state)
    streaming_dir_var.trace_add("write", _apply_streaming_state)
    streaming_exit_var.trace_add("write", _apply_streaming_state)
    for _var in streaming_file_vars.values():
        _var.trace_add("write", _apply_streaming_state)

    # --- Advanced tab ---
    def _reveal_in_explorer(path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if IS_WIN and path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif IS_WIN:
                path.mkdir(parents=True, exist_ok=True)
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                target = str(path if path.is_dir() else path.parent)
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            log(f"Could not open {path}: {exc}")

    adv_header = tk.Frame(advanced_tab, bg=BG)
    adv_header.pack(fill="x", padx=20, pady=(20, 5))
    tk.Label(adv_header, text="Logs", font=(UI_FONT, 16, "bold"),
             fg=ACCENT, bg=BG).pack(side="left")
    tk.Label(adv_header, text="Diagnostic file locations", font=(UI_FONT, 9),
             fg=MUTED, bg=BG).pack(side="left", padx=(10, 0), pady=(5, 0))

    def _path_card(parent, label: str, path: Path, button_text: str) -> None:
        card = tk.Frame(parent, bg=BG_CARD,
                        highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=20, pady=5)
        tk.Label(card, text=label, font=(UI_FONT, 8, "bold"),
                 fg=MUTED, bg=BG_CARD).pack(anchor="w", padx=12, pady=(8, 2))
        tk.Label(card, text=str(path), font=(MONO_FONT, 9),
                 fg=TEXT, bg=BG_CARD, anchor="w", wraplength=350,
                 justify="left").pack(anchor="w", fill="x", padx=12)
        tk.Button(
            card, text=button_text, font=(UI_FONT, 8, "bold"),
            bg=ACCENT, fg=BG, activebackground=ACCENT_BRIGHT, activeforeground=BG,
            relief="flat", cursor="hand2", padx=10, pady=2,
            command=lambda p=path: _reveal_in_explorer(p),
        ).pack(anchor="w", padx=12, pady=(4, 8))

    _path_card(advanced_tab, "DEBUG LOG", LOG_PATH, "Show in folder")
    _path_card(advanced_tab, "CAPTURES DIR", CAPTURES_DIR, "Open folder")
    _path_card(advanced_tab, "CONFIG", CONFIG_PATH, "Show in folder")

    verbose_logging_var = tk.BooleanVar(
        value=bool(config.get("verbose_logging", False)))
    verbose_card = tk.Frame(advanced_tab, bg=BG_CARD,
                            highlightbackground=BORDER, highlightthickness=1)
    verbose_card.pack(fill="x", padx=20, pady=5)
    tk.Checkbutton(
        verbose_card,
        text="Verbose logging (for support)",
        variable=verbose_logging_var,
        bg=BG_CARD, fg=TEXT, activebackground=BG_CARD, activeforeground=TEXT,
        selectcolor=BG, font=(UI_FONT, 10),
        highlightthickness=0, bd=0, anchor="w",
    ).pack(fill="x", padx=12, pady=(8, 2))
    tk.Label(
        verbose_card,
        text=("ON: request captures are saved and extra diagnostics go to "
              "debug.log. OFF: no captures, only error and lifecycle lines."),
        font=(UI_FONT, 8), fg=MUTED, bg=BG_CARD, wraplength=350,
        justify="left", anchor="w",
    ).pack(fill="x", padx=12, pady=(0, 8))

    def _apply_verbose_logging(*_):
        val = bool(verbose_logging_var.get())
        config["verbose_logging"] = val
        save_config(config)
        disp = dispatcher_holder.get("instance")
        if disp is not None:
            disp.verbose_logging = val
        app = app_holder.get("instance")
        if app is not None:
            app.capturing = val
        log(f"Verbose logging {'ON' if val else 'OFF'} — "
            + ("request captures saved to the captures dir, extra "
               "diagnostics in debug.log"
               if val else
               "no request captures; debug.log keeps error and lifecycle "
               "lines only"))

    verbose_logging_var.trace_add("write", _apply_verbose_logging)

    uninstall_link = tk.Label(advanced_tab, text="Uninstall DR3", font=(UI_FONT, 8, "underline"),
                              fg=ACCENT, bg=BG, cursor="hand2")
    uninstall_link.pack(anchor="w", padx=20, pady=(14, 12))
    uninstall_link.bind("<Button-1>", lambda e: on_uninstall())

    # --- Server control ---
    def set_status(running: bool, detail: str = ""):
        if running:
            status_dot.configure(fg=GREEN)
            status_label.configure(text="Running")
            status_detail.configure(text=detail or "Launch DR2 to play")
            start_btn.configure(state="disabled", bg=BG_ELEVATED, fg=MUTED)
            stop_btn.configure(state="normal", bg=RED, fg=BG)
        else:
            status_dot.configure(fg=MUTED)
            status_label.configure(text="Stopped")
            status_detail.configure(text=detail)
            start_btn.configure(state="normal", bg=GREEN, fg=BG)
            stop_btn.configure(state="disabled", bg=BG_ELEVATED, fg=MUTED)

    def server_worker():
        """Run the game server in a background thread."""
        try:
            # Import and configure server
            cert = config.get("cert_path", str(CERT_PATH))
            key = config.get("key_path", str(KEY_PATH))
            api_url = config.get("api_url", API_URL)
            api_token = config.get("game_token")
            upstream_ip = str(config.get("upstream_ip", UPSTREAM_IP) or "")
            data_root = str(_data_dir())

            sys.argv = [sys.argv[0],
                        "--ssl-cert", cert,
                        "--ssl-key", key,
                        "--data-dir", data_root,
                        "--capture-dir", str(CAPTURES_DIR)]
            if api_url:
                sys.argv += ["--api-url", api_url]
            if api_token:
                sys.argv += ["--api-token", api_token]
            if upstream_ip:
                sys.argv += ["--upstream-ip", upstream_ip]

            from dr2server.httpd import build_arg_parser, App, create_server
            import ssl
            import time

            args = build_arg_parser().parse_args()
            from dr2server.api_client import UpstreamClient
            api_client = None
            if args.api_url:
                api_client = UpstreamClient(
                    base_url=args.api_url,
                    api_token=getattr(args, 'api_token', None),
                )

            app = App(
                data_root=Path(args.data_dir),
                capture_root=Path(args.capture_dir),
                upstream_ip=args.upstream_ip,
                api_url=args.api_url,
                api_token=getattr(args, "api_token", None),
                proxy_default_except_clubs=bool(args.upstream_ip),
                capturing=bool(verbose_logging_var.get()),
            )
            app_holder["instance"] = app
            if api_client:
                app.dispatcher.api_client = api_client
            app.dispatcher.verbose_logging = bool(verbose_logging_var.get())
            dispatcher_holder["instance"] = app.dispatcher

            # UDP telemetry listener (real per-segment stage splits). Enabled by
            # default; binds DR3's own telemetry port from config.json
            # ("telemetry_port", default 20999). The game's <udp> element is
            # normalized at startup (ensure_game_udp_for_dr3) so the game sends
            # to a port DR3 receives — when it stays on the SimHub default 20777,
            # SimHub forwards its UDP output here. A bind failure just leaves
            # telemetry off (with an actionable log line) — the server still runs
            # and splits fall back to the even profile.
            from dr2server.telemetry import TelemetryListener
            telemetry_listener = None
            if bool(config.get("telemetry_enabled", True)):
                try:
                    tel_port = int(config.get("telemetry_port") or 0)
                except (TypeError, ValueError):
                    tel_port = 0
                if not tel_port:
                    tel_port = DEFAULT_TELEMETRY_PORT
                candidate = TelemetryListener(
                    port=tel_port,
                    logger=lambda m: root.after(0, lambda msg=m: log(msg)),
                )
                if candidate.start():
                    app.dispatcher.telemetry = candidate
                    telemetry_listener = candidate
                    telemetry_holder["port"] = tel_port
                    telemetry_holder["failed"] = False
                    root.after(0, lambda p=tel_port: log(
                        f"Telemetry listener started on UDP :{p}"))
                else:
                    app.dispatcher.telemetry = None
                    telemetry_holder["port"] = tel_port
                    telemetry_holder["failed"] = True

            from dr2server.streaming import StreamingWriter
            streaming_writer["instance"] = StreamingWriter(
                app.dispatcher,
                logger=lambda m: root.after(0, lambda msg=m: log(msg)),
                verbose=lambda: bool(verbose_logging_var.get()),
            )
            streaming_writer["instance"].set_enabled(_enabled_files_set())
            streaming_writer["instance"].set_exit_behavior(
                str(config.get("streaming_exit_behavior") or "clear"))
            if config.get("streaming_enabled", False):
                _si = max(2, int(config.get("streaming_interval_seconds", 5) or 5))
                _odir = _overlay_dir()
                streaming_writer["instance"].start(
                    interval=_si, output_dir=_odir,
                )
                root.after(0, lambda iv=_si, d=_odir: log(
                    f"Streaming writer started, {d} every {iv}s"))

            servers = []
            http_server = create_server(args.host, args.port, app)
            servers.append(http_server)

            if args.ssl_cert and args.ssl_key:
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_context.load_cert_chain(certfile=args.ssl_cert, keyfile=args.ssl_key)
                https_server = create_server(args.host, args.https_port, app, ssl_context=ssl_context)
                servers.append(https_server)

            for s in servers:
                threading.Thread(target=s.serve_forever, daemon=True).start()

            server_running.set()
            root.after(0, lambda: set_status(True))
            root.after(0, lambda: log("Server listening on HTTPS :443 and HTTP :8080"))

            # Wait until shutdown is requested
            while not shutdown_flag.is_set():
                shutdown_flag.wait(timeout=1.0)

            if streaming_writer["instance"] is not None:
                _writer = streaming_writer["instance"]
                _writer.stop()
                # Clear overlay files on shutdown so streamers' OBS / SimHub
                # text sources go blank when DR3 isn't running, rather
                # than holding the last race's values. Blanks or deletes the
                # files per the Overlay tab's exit-behavior setting.
                _writer.clear_files()
                streaming_writer["instance"] = None

            if telemetry_listener is not None:
                try:
                    telemetry_listener.stop()
                except Exception:
                    pass
                if app.dispatcher is not None:
                    app.dispatcher.telemetry = None
                telemetry_listener = None
            telemetry_holder["port"] = None
            telemetry_holder["failed"] = False

            app_holder["instance"] = None

            for s in servers:
                s.shutdown()
                s.server_close()

        except Exception:
            import traceback
            err = traceback.format_exc()
            root.after(0, lambda e=err: log(f"Server error:\n{e}"))
        finally:
            server_running.clear()
            root.after(0, lambda: set_status(False))

    def on_start():
        nonlocal config, server_thread
        config = load_config()
        ensure_telemetry_defaults(config)
        _normalize_game_udp()
        start_btn.configure(state="disabled")
        log("Starting DR3...")

        def setup_and_start():
            nonlocal config
            try:
                easy_setup = easy_setup_var.get()

                # Generate cert if missing (needed in both modes — server loads it).
                if not cert_exists():
                    root.after(0, lambda: log("Generating TLS certificate..."))
                    generate_cert()
                    config["cert_path"] = str(CERT_PATH)
                    config["key_path"] = str(KEY_PATH)
                    save_config(config)

                if not easy_setup:
                    root.after(0, lambda: log(
                        "Easy Setup off - assuming hosts & cert trust are configured manually. "
                        "See byagersi.com/rally/install#manual"))
                elif IS_WIN:
                    if not hosts_configured():
                        root.after(0, lambda: log("Setting up hosts & cert trust (admin required)..."))
                        rc: Optional[int] = None
                        try:
                            rc = _windows_elevate_admin_helper("start")
                        except subprocess.TimeoutExpired:
                            root.after(0, lambda: log(
                                "WARNING: UAC prompt timed out after 5 minutes."))

                        if not hosts_configured():
                            root.after(0, lambda: log(
                                f"WARNING: Could not configure hosts (helper exit {rc}). "
                                "Did you decline the admin prompt?"))
                        elif rc == ADMIN_HELPER_CERT_FAILED:
                            root.after(0, lambda: log(
                                "WARNING: Hosts configured, but installing the cert into the "
                                "Windows Root store failed. The game will not trust the "
                                "server; see byagersi.com/rally/install#manual."))
                        elif rc != ADMIN_HELPER_OK:
                            root.after(0, lambda: log(
                                f"WARNING: Hosts configured, but the admin helper exited "
                                f"with code {rc}."))
                        else:
                            root.after(0, lambda: log("Hosts configured, cert trusted."))
                else:
                    # Linux: one pkexec runs setcap (port 443) + writes /etc/hosts.
                    target_bin = _elevation_target_binary()
                    needs_setcap = not has_port_capability(target_bin)
                    needs_hosts = not hosts_configured()
                    if needs_setcap or needs_hosts:
                        helper_python = _system_python_for_helper()
                        if helper_python is None:
                            root.after(0, lambda: log(
                                "WARNING: no python3 on PATH. Install python3 or run setcap "
                                "and the /etc/hosts edit manually - see install docs."))
                        else:
                            steps = []
                            if needs_setcap:
                                steps.append("port-443 capability")
                            if needs_hosts:
                                steps.append("/etc/hosts")
                            root.after(0, lambda s=", ".join(steps): log(
                                f"Authentication required to set up {s}..."))
                            helper = DR3_DIR / "_admin_start.py"
                            helper.write_text(_linux_admin_start_script(target_bin), encoding="utf-8")
                            try:
                                result = subprocess.run(
                                    ["pkexec", str(helper_python), str(helper)],
                                    capture_output=True, text=True,
                                    timeout=ELEVATION_TIMEOUT_SECONDS,
                                )
                            except subprocess.TimeoutExpired:
                                helper.unlink(missing_ok=True)
                                root.after(0, lambda: log(
                                    "WARNING: polkit prompt timed out after 5 minutes."))
                                result = None
                            if result is not None:
                                helper.unlink(missing_ok=True)
                                out = (result.stdout or "").strip()
                                if result.returncode != 0:
                                    err = (result.stderr or out or "no output").strip().splitlines()[-1:] or [""]
                                    root.after(0, lambda e=err[0]: log(
                                        f"WARNING: elevated setup failed ({e}). "
                                        "You can configure /etc/hosts and setcap manually - see install docs."))
                                else:
                                    if out:
                                        root.after(0, lambda o=out: log(o))
                                    root.after(0, lambda: log("Hosts and port-443 capability configured."))
                                    # Re-exec if we just acquired the cap; the
                                    # current process can't use it.
                                    if needs_setcap and has_port_capability(target_bin):
                                        root.after(0, lambda: log(
                                            "Relaunching DR3 to apply port-443 capability..."))
                                        root.after(1500, _relaunch_with_auto_start)
                                        return

                    # Cert into DR2 prefix (no elevation; user owns the prefix).
                    root.after(0, lambda: log("Installing TLS cert into DR2 Proton prefix..."))
                    ok, msg = install_cert_into_dr2_prefix(CERT_PATH)
                    if ok:
                        root.after(0, lambda m=msg: log(m))
                    else:
                        root.after(0, lambda m=msg: log(f"Cert install: {m}"))

                # Check for token
                if not config.get("game_token"):
                    root.after(0, lambda: log("No game token. Get one at byagersi.com/rally/dashboard"))
                    webbrowser.open(DASHBOARD_URL)
                    # We'll still start the server - it works without token (local mode)

                # Start server
                shutdown_flag.clear()
                server_thread = threading.Thread(target=server_worker, daemon=True)
                server_thread.start()

            except Exception as exc:
                root.after(0, lambda: log(f"Setup error: {exc}"))
                root.after(0, lambda: set_status(False))

        threading.Thread(target=setup_and_start, daemon=True).start()

    def _stop_cleanup(after):
        """The STOP flow: wait for the server thread to exit, remove the hosts
        entries (elevation), update the status, then call after()."""
        def cleanup():
            # Wait for server to stop
            if server_thread:
                server_thread.join(timeout=5)

            if not easy_setup_var.get():
                root.after(0, lambda: log(
                    "Easy Setup off - leaving hosts unchanged. Remove them manually if desired."))
                root.after(0, lambda: set_status(False, "Stopped"))
                root.after(0, after)
                return

            # Remove hosts (needs elevation)
            root.after(0, lambda: log("Removing hosts entries (admin required)..."))
            try:
                if IS_WIN:
                    rc = _windows_elevate_admin_helper("stop")
                    if rc != ADMIN_HELPER_OK:
                        root.after(0, lambda: log(
                            f"WARNING: admin helper exited with code {rc}; "
                            "hosts entries may still be present."))
                else:
                    helper_python = _system_python_for_helper()
                    if helper_python is None:
                        root.after(0, lambda: log(
                            "WARNING: no python3 on PATH; cannot remove hosts entries."))
                    else:
                        helper = DR3_DIR / "_admin_stop.py"
                        helper.write_text(_linux_admin_stop_script(), encoding="utf-8")
                        try:
                            subprocess.run(
                                ["pkexec", str(helper_python), str(helper)],
                                capture_output=True, text=True,
                                timeout=ELEVATION_TIMEOUT_SECONDS,
                            )
                        finally:
                            helper.unlink(missing_ok=True)
            except subprocess.TimeoutExpired:
                root.after(0, lambda: log(
                    "WARNING: elevation prompt timed out after 5 minutes."))

            if hosts_configured():
                root.after(0, lambda: log(
                    "WARNING: Stopped, but hosts entries are still present; the game "
                    "will not reach RaceNet until they are removed."))
                root.after(0, lambda: set_status(False, "Hosts NOT restored"))
            else:
                root.after(0, lambda: log("Stopped. Game will use RaceNet servers now."))
                root.after(0, lambda: set_status(False, "Hosts restored"))
            root.after(0, after)

        threading.Thread(target=cleanup, daemon=True).start()

    def on_stop():
        stop_btn.configure(state="disabled")
        log("Stopping server...")
        shutdown_flag.set()
        _stop_cleanup(lambda: None)

    def on_uninstall():
        install_dir = _install_dir()
        if install_dir is None:
            log("Uninstall is only available in the installed DR3.exe build.")
            return
        if server_running.is_set():
            log("Press STOP first, then uninstall.")
            return
        hosts_note = ""
        if easy_setup_var.get() and hosts_configured():
            hosts_note = (
                "\n\nNote: your hosts file still points the game at DR3. "
                "Press STOP to restore it (or run scripts\\remove_windows_redirect.ps1 "
                "as administrator)."
            )
        if not messagebox.askyesno(
                "DR3",
                f"This removes:\n\n"
                f"  {install_dir}\n"
                f"  {DR3_DIR}   (settings, logs, captures)\n"
                f"{hosts_note}\n\nUninstall DR3?",
                icon="warning",
                default="no",
        ):
            return
        if not IS_WIN:
            log("Automatic uninstall is only available on Windows — delete the DR3 folder "
                f"and {DR3_DIR} manually.")
            return
        # Drop staged auto-updates so no installer child can race the removal.
        shutil.rmtree(UPDATES_DIR, ignore_errors=True)
        # The exe is still running from install_dir, so a detached cmd removes
        # both folders shortly after this process exits.
        script = (f'timeout /t 2 >nul & rmdir /s /q "{install_dir}" '
                  f'& rmdir /s /q "{DR3_DIR}"')
        try:
            subprocess.Popen(
                ["cmd", "/c", script],
                creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log(f"Uninstall failed to start: {exc}")
            return
        log("Uninstalling — the DR3 folders will be removed in a few seconds.")
        root.destroy()

    start_btn.configure(command=on_start)
    stop_btn.configure(command=on_stop)

    # Auto-click START when we were just relaunched after a successful setcap,
    # or when the "start automatically" option is on AND a token is set (it's
    # off by default on a fresh install). on_start disables the START button
    # first, so a manual click can't double-start.
    if os.environ.pop("DR3_AUTO_START", "") == "1" \
            or (config.get("auto_start_server", False) and config.get("game_token")):
        root.after(800, on_start)

    def _spawn_update_on_exit():
        """Hand a staged update to a detached child that swaps the install
        folder once this process has fully exited (backlog #51)."""
        staged = _staged_update()
        install_dir = _install_dir()
        if not staged or install_dir is None:
            return
        zip_path, ver = staged
        try:
            subprocess.Popen(
                [*_self_invocation_args(), "--apply-update", str(zip_path),
                 str(os.getpid())],
                creationflags=(0x00000008 | 0x00000200) if IS_WIN else 0,
                # Windows refuses to rename a directory that is any process's
                # current working directory — the GUI's CWD is usually the
                # install dir itself (double-clicked exe), so start the child
                # one level up.
                cwd=str(install_dir.parent),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"[UPDATE] failed to start installer child: {exc}")
            return
        log(f"Update v{'.'.join(map(str, ver))} will be installed in the "
            "background after exit.")

    def _finish_close():
        # Cancel the pending status-tick so teardown has no queued callback
        # left (Tkinter prints a Tcl error for events that outlive their
        # Python wrapper).
        for tick in (tel_tick, update_tick):
            if tick["id"] is not None:
                try:
                    root.after_cancel(tick["id"])
                except tk.TclError:
                    pass
        # Clean exit → archive this session's log (verbose mode only, #52).
        archived = _archive_debug_log(bool(verbose_logging_var.get()))
        if archived is not None:
            print(f"[LOG] session archived to {archived}")
        _spawn_update_on_exit()
        root.destroy()

    def _ask_close(question: str, proceed_label: str) -> bool:
        """Modal close-confirmation dialog with a labeled proceed button and
        Cancel. Returns True when the user chose to proceed."""
        dlg = tk.Toplevel(root)
        dlg.title("DR3")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        answer = {"proceed": False}

        def _finish(val: bool):
            if not dlg.winfo_exists():
                return
            answer["proceed"] = val
            dlg.destroy()

        tk.Label(dlg, text=question, font=(UI_FONT, 9), fg=TEXT, bg=BG,
                 wraplength=320, justify="left", anchor="w").pack(
            fill="x", padx=16, pady=(14, 10))

        btns = tk.Frame(dlg, bg=BG)
        btns.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(btns, text="Cancel", font=(UI_FONT, 9, "bold"),
                  bg=BG_ELEVATED, fg=TEXT, activebackground=BORDER,
                  activeforeground=TEXT, relief="flat", cursor="hand2",
                  padx=14, pady=6, command=lambda: _finish(False)).pack(side="left")
        tk.Button(btns, text=proceed_label, font=(UI_FONT, 9, "bold"),
                  bg=ACCENT, fg=BG, activebackground=ACCENT_BRIGHT,
                  activeforeground=BG, relief="flat", cursor="hand2",
                  padx=14, pady=6, command=lambda: _finish(True)).pack(side="right")

        x = root.winfo_rootx() + max((root.winfo_width() - 360) // 2, 0)
        y = root.winfo_rooty() + max((root.winfo_height() - 200) // 2, 0)
        dlg.geometry(f"360x170+{x}+{y}")
        dlg.transient(root)
        dlg.grab_set()
        dlg.bind("<Escape>", lambda e: _finish(False))
        dlg.bind("<Return>", lambda e: _finish(True))
        root.wait_window(dlg)
        return answer["proceed"]

    closing = {"in_progress": False}

    def on_close():
        # Quitting with the server up leaves the hosts redirect in place, so
        # the game keeps trying to reach the DR3 proxy and can't talk
        # to RaceNet. Offer to run the full STOP flow (which restores the
        # hosts file) before exiting.
        if closing["in_progress"]:
            return
        leftover_hosts = easy_setup_var.get() and hosts_configured()
        server_up = server_running.is_set()
        if not (server_up or leftover_hosts):
            _finish_close()
            return
        if server_up:
            question = ("The server is still running, and your hosts file still points the "
                        "game at the DR3 proxy.\n\n"
                        "\"Stop and Close\" stops the server, restores your hosts file (an "
                        "admin prompt may appear), and quits.")
            proceed_label = "Stop and Close"
        else:
            question = ("Your hosts file still points the game at the DR3 proxy.\n\n"
                        "If you exit now, the game won't connect to RaceNet until you reopen "
                        "DR3 and press Stop (or edit your hosts file by hand).")
            proceed_label = "Close anyway"
        if not _ask_close(question, proceed_label):
            return
        if server_up:
            # Same stop flow as the STOP button — restores hosts, then closes.
            closing["in_progress"] = True
            stop_btn.configure(state="disabled")
            log("Stopping server...")
            shutdown_flag.set()
            _stop_cleanup(_finish_close)
        else:
            _finish_close()

    root.protocol("WM_DELETE_WINDOW", on_close)
    for _bind_wheel in (_main_wheel, _streaming_wheel, _advanced_wheel):
        _bind_wheel()  # all tab content exists now — cover every child widget
    root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Auto-updater child (backlog #51): must run BEFORE _setup_file_logging —
    # the parent GUI is still alive holding debug.log open, and rotating it
    # here would scatter the tail of the parent session into debug.prev.log.
    if len(sys.argv) >= 4 and sys.argv[1] == "--apply-update":
        sys.exit(_run_apply_update(sys.argv[2], sys.argv[3]))
    _setup_file_logging()
    # Elevated-helper entry: when re-launched via UAC/pkexec with
    # `--admin-helper <op>`, do the privileged work and exit before
    # importing/initializing any Tk machinery. The elevated child must
    # never show a GUI — otherwise users see a phantom second window
    # and the privileged work silently never happens (the original Win11
    # symptom).
    if len(sys.argv) >= 3 and sys.argv[1] == "--admin-helper":
        sys.exit(_run_admin_helper(sys.argv[2]))

    run_gui()
