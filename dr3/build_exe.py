"""Build script - produces the distributable binary via PyInstaller.

Run:
    python build_exe.py

Output:
    Windows  -> dist/DR3/DR3.exe (onedir), zipped to
                dist/DR3-windows.zip for release
    Linux    -> dist/DR3-linux-x86_64 (onefile)

Windows is built --onedir on purpose: a --onefile exe is a self-extracting
stub that unpacks a runtime to %TEMP% and runs it, which is structurally a
dropper and scores badly with Defender's ML classifier. A plain exe next to
its DLLs scores far better.

Requirements:
    pip install pyinstaller cryptography
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"

# Shared libs that ship with glibc / the dynamic loader. Never bundle these:
# the binary must use the host's glibc, and bundling our build-host's copy
# breaks on older targets ("version `GLIBC_2.XX' not found").
_GLIBC_LIB_PREFIXES = (
    "linux-vdso", "ld-linux", "libc.so", "libm.so", "libpthread.so",
    "libdl.so", "librt.so", "libresolv.so", "libnsl.so", "libutil.so",
)


def _linux_tk_runtime_libs() -> list[str]:
    """Resolve the X11/font shared libs that bundled libtk pulls in at runtime.

    PyInstaller bundles libtk/libtcl/_tkinter automatically, but it does not
    follow libtk's dynamic deps (libX11, libXft, libfontconfig, libfreetype,
    libXss, libxcb, ...). On hosts without the distro `tk` package installed
    those are missing and the GUI fails to start. Walk ldd against _tkinter.so
    and bundle everything except glibc itself.
    """
    if not IS_LINUX:
        return []
    import _tkinter
    target = _tkinter.__file__
    proc = subprocess.run(
        ["ldd", target], capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        print(f"[build] ldd {target} failed; skipping X11 dep bundling.")
        return []
    libs: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "=>" not in line:
            continue
        name, _, rest = line.partition("=>")
        name = name.strip()
        if any(name.startswith(p) for p in _GLIBC_LIB_PREFIXES):
            continue
        path = rest.strip().split(" ", 1)[0]
        if not path or not Path(path).exists():
            continue
        libs.append(path)
    return libs

if IS_WIN:
    APP_NAME = "DR3"                   # PyInstaller appends .exe
    OUTPUT_NAME = "DR3/DR3.exe"                  # onedir
    ZIP_BASENAME = "DR3-windows"                    # -> dist/DR3-windows.zip
elif IS_LINUX:
    # Honest arch tag — CI publishes x86_64 only, but a local build on
    # aarch64 would otherwise produce a misnamed binary.
    APP_NAME = f"DR3-linux-{platform.machine()}"
    OUTPUT_NAME = APP_NAME
else:
    APP_NAME = "DR3"
    OUTPUT_NAME = "DR3"


def _windows_version_file() -> Path | None:
    """Write a VSVersionInfo file for PyInstaller's --version-file (Windows).

    Unsigned exes with no version resource (CompanyName, ProductName, ...)
    score worse with AV machine-learning classifiers; embedding real metadata
    reduces false-positive detections.
    """
    if not IS_WIN:
        return None
    version = (ROOT / "VERSION").read_text().strip()
    numeric = [int(p) for p in version.split(".") if p.isdigit()]
    filevers = tuple((numeric + [0, 0, 0, 0])[:4])
    content = f"""\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={filevers},
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'byagersi.com'),
        StringStruct('FileDescription', 'DR3 - DiRT Rally 2.0 Community Server'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'DR3'),
        StringStruct('LegalCopyright', 'byagersi.com'),
        StringStruct('OriginalFilename', 'DR3.exe'),
        StringStruct('ProductName', 'DR3'),
        StringStruct('ProductVersion', '{version}'),
      ]),
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""
    # Not under build/, since PyInstaller's --clean purges that dir at start.
    path = ROOT / "version_info.txt"
    path.write_text(content)
    return path


def check_pyinstaller() -> None:
    """Ensure PyInstaller is installed; offer to install it if missing."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] PyInstaller not found.")
        answer = input("Install it now? (pip install pyinstaller) [y/N] ").strip().lower()
        if answer == "y":
            subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
        else:
            print("[build] Aborted — PyInstaller is required.")
            sys.exit(1)


def build() -> None:
    check_pyinstaller()

    # ------------------------------------------------------------------
    # Data files bundled inside the exe.
    # PyInstaller --add-data syntax: <src_path><sep><dest_dir_in_bundle>
    # On Windows the separator is ";", on other platforms ":".
    # We pass the whole upstream_templates directory; PyInstaller copies all
    # its contents to data/upstream_templates inside the bundle, which is
    # exactly where dr3_app.py looks for them via _data_dir().
    # ------------------------------------------------------------------
    templates_src = ROOT / "data" / "upstream_templates"
    sep = ";" if sys.platform == "win32" else ":"
    add_data = [
        f"{templates_src}{sep}data/upstream_templates",
        f"{ROOT / 'VERSION'}{sep}.",
    ]

    # App icon (PNG), used by tkinter for the window/taskbar icon. Bundled at
    # the bundle root; dr3_app.py's _icon_path() looks there.
    icon_src = ROOT / "icon_32.png"
    if icon_src.is_file():
        add_data.append(f"{icon_src}{sep}.")
    else:
        logo_src = ROOT / "web" / "static" / "logo.png"
        if logo_src.is_file():
            add_data.append(f"{logo_src}{sep}.")
        else:
            print("[build] No app icon found; window will use Tk's default icon.")

    # Player-facing setup scripts (hosts redirect, dev cert) — the website's
    # install page "manual setup" section points players at these.
    scripts_src = ROOT / "scripts"
    if scripts_src.is_dir():
        add_data.append(f"{scripts_src}{sep}scripts")

    # ------------------------------------------------------------------
    # Hidden imports that PyInstaller's static analysis might miss.
    # ------------------------------------------------------------------
    hidden_imports = [
        # cryptography internals (used by cert generation)
        "cryptography.hazmat.primitives.asymmetric.rsa",
        "cryptography.hazmat.primitives.hashes",
        "cryptography.hazmat.primitives.serialization",
        "cryptography.x509",
        "cryptography.x509.oid",
        # dr2server package and sub-modules
        "dr2server",
        "dr2server.httpd",
        "dr2server.dispatcher",
        "dr2server.egonet",
        "dr2server.models",
        "dr2server.game_data",
        "dr2server.api_client",
        "dr2server.account_store",
        # streaming text-file writer (imported inside a function in dr3_app.py)
        "dr2server.streaming",
    ]

    # Bundle mode (--onedir / --onefile) is inserted per run below.
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        # No --uac-admin: the app elevates only when needed (hosts/cert),
        # not on every launch. This avoids the UAC prompt just to see the GUI.
        "--windowed",  # No console window (GUI app)
        # Clean build artefacts from previous runs.
        "--clean",
        # onedir asks "output directory will be REMOVED! Continue?" when
        # dist/DR3/ already exists; never prompt (rebuilds, CI).
        "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
    ]
    if IS_LINUX:
        # Smaller ELF; harmless on Windows but only meaningful on Linux/macOS.
        cmd.append("--strip")

    # Data files
    for entry in add_data:
        cmd += ["--add-data", entry]

    # Hidden imports
    for imp in hidden_imports:
        cmd += ["--hidden-import", imp]

    # Linux: bundle libtk's X11/font dep chain so the binary runs on hosts
    # that don't have the distro `tk` package installed (which would have
    # otherwise pulled in libX11/libXft/libfontconfig/etc. transitively).
    if IS_LINUX:
        runtime_libs = _linux_tk_runtime_libs()
        if runtime_libs:
            print(f"[build] Bundling {len(runtime_libs)} Tk runtime libs:")
            for lib in runtime_libs:
                print(f"          {lib}")
                cmd += ["--add-binary", f"{lib}{sep}."]
        else:
            print("[build] No Tk runtime libs detected to bundle.")

    # Windows version resource (AV heuristics penalize metadata-less exes).
    version_file = _windows_version_file()
    if version_file:
        cmd += ["--version-file", str(version_file)]

    # Optional icon
    icon_path = ROOT / "icon.ico"
    if icon_path.exists():
        cmd += ["--icon", str(icon_path)]
    else:
        print(f"[build] No icon found at {icon_path} — skipping --icon.")

    # Entry point
    cmd.append(str(ROOT / "dr3_app.py"))

    def run_pyinstaller(mode: str, expected: Path) -> None:
        full = cmd[:3] + [mode] + cmd[3:]
        print(f"[build] Running PyInstaller ({mode}) ...")
        print("[build] Command:", " ".join(full))
        print()
        result = subprocess.run(full, cwd=str(ROOT))
        if result.returncode != 0:
            print(f"\n[build] PyInstaller failed (exit {result.returncode}).")
            sys.exit(result.returncode)
        print()
        if not expected.exists():
            print(f"[build] Warning: expected output not found at {expected}")
            sys.exit(1)
        size_mb = expected.stat().st_size / (1024 * 1024)
        print(f"[build] Success!  {expected}  ({size_mb:.1f} MB)")

    if IS_WIN:
        run_pyinstaller("--onedir", ROOT / "dist" / OUTPUT_NAME)
    else:
        # Linux: onefile, since the AV problem is Windows-only and setcap
        # targets the single ELF.
        run_pyinstaller("--onefile", ROOT / "dist" / OUTPUT_NAME)

    if IS_WIN:
        import shutil
        # Player-facing scripts also at the bundle root (next to DR3.exe) so
        # the website install page can reference DR3\scripts\ without _internal.
        bundle_scripts = ROOT / "dist" / APP_NAME / "scripts"
        if (ROOT / "scripts").is_dir():
            if bundle_scripts.exists():
                shutil.rmtree(bundle_scripts)
            shutil.copytree(ROOT / "scripts", bundle_scripts)

        # Zip the onedir folder with DR3/ as the top-level entry so
        # extracting gives users one folder to run from.
        zip_path = shutil.make_archive(
            str(ROOT / "dist" / ZIP_BASENAME), "zip",
            root_dir=str(ROOT / "dist"), base_dir=APP_NAME,
        )
        zip_mb = Path(zip_path).stat().st_size / (1024 * 1024)
        print(f"[build] Zipped   {zip_path}  ({zip_mb:.1f} MB)")


if __name__ == "__main__":
    build()
