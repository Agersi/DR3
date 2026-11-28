"""C7/C9 probe: DR3 hosts-file safety (dr3_app.py) + generated Linux pkexec script.

Usage: python tools/probe_hosts_safety.py [hosts_file]
(default hosts file: build/probe_hosts/hosts — created if missing)

Checks the in-process helpers (_read_hosts/_strip_block/_write_hosts_atomic/
add_hosts/remove_hosts/hosts_configured) and the embedded Linux admin script
(compile + exec against a scratch copy). Exits non-zero on any failure.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dr3"))

HOSTS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "probe_hosts" / "hosts"
HOSTS.parent.mkdir(parents=True, exist_ok=True)
os.environ["DR3_HOSTS_FILE"] = str(HOSTS)

import dr3_app  # noqa: E402  (env must be set before import)

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'OK  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def set_hosts_bytes(data: bytes) -> None:
    HOSTS.write_bytes(data)


# --- 1. add to empty -------------------------------------------------------
HOSTS.unlink(missing_ok=True)
dr3_app.add_hosts()
text = HOSTS.read_text(encoding="utf-8")
check("add: block written", dr3_app.HOSTS_BEGIN in text and dr3_app.HOSTS_END in text)
check("add: all domains present", all(d in text for d in dr3_app.REDIRECT_DOMAINS))
check("add: configured()", dr3_app.hosts_configured())

# --- 2. remove with both markers (normal case) ------------------------------
user_line = "192.168.1.50\tmyprinter.local\n"
set_hosts_bytes((text + "\n" + user_line).encode("utf-8"))
dr3_app.remove_hosts()
text = HOSTS.read_text(encoding="utf-8")
check("remove: block gone", dr3_app.HOSTS_BEGIN not in text and dr3_app.HOSTS_END not in text)
check("remove: no domain left", not any(d in text for d in dr3_app.REDIRECT_DOMAINS))
check("remove: user line kept", "myprinter.local" in text)
check("remove: not configured()", not dr3_app.hosts_configured())

# --- 3. REGRESSION: only BEGIN present (END hand-deleted) -------------------
# Old code ate everything from BEGIN to EOF; new code drops just our lines.
after = "10.0.0.2\tlate.example.com\n"
set_hosts_bytes(
    ("127.0.0.1 localhost\n"
     + dr3_app.HOSTS_BEGIN + "\n"
     + "".join(f"127.0.0.1\t{d}\n" for d in dr3_app.REDIRECT_DOMAINS)
     + after).encode("utf-8"))
dr3_app.remove_hosts()
text = HOSTS.read_text(encoding="utf-8")
check("begin-only: late content KEPT", "late.example.com" in text)
check("begin-only: domains gone", not any(d in text for d in dr3_app.REDIRECT_DOMAINS))
check("begin-only: orphan marker gone", dr3_app.HOSTS_BEGIN not in text)
check("begin-only: localhost kept", "localhost" in text)

# --- 4. only END present ----------------------------------------------------
set_hosts_bytes(
    ("127.0.0.1 localhost\n"
     + "".join(f"127.0.0.1\t{d}\n" for d in dr3_app.REDIRECT_DOMAINS)
     + dr3_app.HOSTS_END + "\n"
     + after).encode("utf-8"))
dr3_app.remove_hosts()
text = HOSTS.read_text(encoding="utf-8")
check("end-only: late content kept", "late.example.com" in text)
check("end-only: domains gone", not any(d in text for d in dr3_app.REDIRECT_DOMAINS))
check("end-only: orphan marker gone", dr3_app.HOSTS_END not in text)

# --- 5. foreign redirect of our domain OUTSIDE the block is NOT touched ----
set_hosts_bytes(
    ("9.9.9.9\t" + dr3_app.REDIRECT_DOMAINS[0] + "\n"   # user's own redirect
     + dr3_app.HOSTS_BEGIN + "\n"
     + "".join(f"127.0.0.1\t{d}\n" for d in dr3_app.REDIRECT_DOMAINS)
     + dr3_app.HOSTS_END + "\n").encode("utf-8"))
dr3_app.remove_hosts()
text = HOSTS.read_text(encoding="utf-8")
check("foreign redirect outside block kept", "9.9.9.9\t" + dr3_app.REDIRECT_DOMAINS[0] in text)

# --- 6. latin-1 file stays latin-1 ------------------------------------------
latin = ("# caf\u00e9 comment\n127.0.0.1 localhost\n".encode("latin-1")
         + os.linesep.encode("latin-1"))
set_hosts_bytes(latin)
dr3_app.add_hosts()
raw = HOSTS.read_bytes()
check("latin-1: café byte preserved", b"caf\xe9" in raw)
content, enc = dr3_app._read_hosts()
check("latin-1: encoding detected", enc == "latin-1")
dr3_app.remove_hosts()
check("latin-1: still decodes as latin-1 after remove", b"caf\xe9" in HOSTS.read_bytes())

# --- 7. no temp file left behind --------------------------------------------
check("atomic: no .dr3tmp left", not (HOSTS.parent / (HOSTS.name + ".dr3tmp")).exists())

# --- 8. generated Linux script: compiles + runs with the same semantics -----
for add in (True, False):
    script = dr3_app._linux_admin_script(add=add, setcap_target=None)
    try:
        compile(script, "<linux-admin-script>", "exec")
        ok_compile = True
    except SyntaxError as e:
        ok_compile = False
        print(f"  syntax error: {e}")
    check(f"linux script (add={add}): compiles", ok_compile)

scratch = Path(tempfile.mkdtemp(dir=ROOT / "build")) / "hosts"
scratch.write_bytes(("127.0.0.1 localhost\n" + dr3_app.HOSTS_BEGIN + "\n"
                     + "".join(f"127.0.0.1\t{d}\n" for d in dr3_app.REDIRECT_DOMAINS)
                     + "10.0.0.2\tlate.example.com\n").encode("utf-8"))
# Point the generated script at the scratch file (it embeds HOSTS_FILE).
script = dr3_app._linux_admin_script(add=False, setcap_target=None).replace(
    repr(str(dr3_app.HOSTS_FILE)), repr(str(scratch)))
ns = {}
exec(compile(script, "<s>", "exec"), ns)
t = scratch.read_text(encoding="utf-8")
check("linux script: remove keeps late content", "late.example.com" in t)
check("linux script: remove drops block", dr3_app.HOSTS_BEGIN not in t and not any(d in t for d in dr3_app.REDIRECT_DOMAINS))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("ALL HOSTS SAFETY CHECKS PASSED")
