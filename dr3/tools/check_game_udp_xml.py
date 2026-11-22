"""Verify the game hardware_settings_config.xml telemetry helpers in
dr2server/telemetry.py (read_game_udp / ensure_game_udp_for_dr3):

- ensure_game_udp_for_dr3 is ADD-ONLY: it never modifies or removes any
  existing <udp> element, only adds a dedicated stream on DR3's port when
  none points there;
- an enabled <udp> already on DR3's port is left alone (extradata="3"
  normalized on that element only when missing/wrong);
- pre-existing streams (SimHub default 20777, any other port, disabled ones)
  are byte-for-byte untouched; a new element is inserted after the last
  existing <udp>, or right after <motion_platform> when there is none;
- edits write a .bak backup, preserve the rest of the file, and are
  idempotent;
- read_game_udp reports every enabled port in document order;
- _xml_elem_attrs("udp") / _xml_udp_elems never match a differently-named
  element such as custom_udp.

Runs entirely against throwaway fixtures in dr3/build (gitignored). Exits
non-zero on any failure.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path


def _load_telemetry():
    path = Path(__file__).resolve().parent.parent / "dr2server" / "telemetry.py"
    spec = importlib.util.spec_from_file_location("dr3_tel_mod", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dr3_tel_mod"] = mod  # so @dataclass can resolve cls.__module__
    spec.loader.exec_module(mod)
    return mod


BASE = (
    '<?xml version="1.0"?>\n'
    '<hardware_settings_config version="25" deviceId="0x2684">\n'
    '  <motion_platform>\n'
    '    <dbox enabled="true" />\n'
    '    {udps}\n'
    '    <fanatec enabled="false" pollrate="64" deadzone="0" />\n'
    '  </motion_platform>\n'
    '  <gameplay>\n'
    '    <assist_braking value="1" />\n'
    '  </gameplay>\n'
    '</hardware_settings_config>\n'
)

UDP_20777 = '<udp enabled="true" extradata="3" ip="127.0.0.1" port="20777" delay="1" />'
UDP_20777_EX0 = '<udp enabled="true" extradata="0" ip="127.0.0.1" port="20777" delay="1" />'
UDP_30000 = '<udp enabled="true" extradata="0" ip="127.0.0.1" port="30000" delay="1" />'
UDP_DISABLED = '<udp enabled="false" extradata="0" ip="127.0.0.1" port="20777" delay="1" />'
UDP_TARGET_EX0 = '<udp enabled="true" extradata="0" ip="127.0.0.1" port="20999" delay="1" />'
UDP_TARGET_EX3 = '<udp enabled="true" extradata="3" ip="127.0.0.1" port="20999" delay="1" />'


def main() -> int:
    mod = _load_telemetry()
    failures = 0

    def check(name, cond, detail=""):
        nonlocal failures
        if cond:
            print(f"OK   {name}")
        else:
            failures += 1
            print(f"FAIL {name} {detail}")

    scratch = Path(__file__).resolve().parent.parent / "build"
    scratch.mkdir(parents=True, exist_ok=True)
    target = 20999

    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        def write(name, *udp_lines):
            p = Path(tmp) / name
            if not udp_lines:
                # motion_platform present but no <udp> line at all.
                text = BASE.replace("    {udps}\n", "").replace("{udps}", "")
            else:
                text = BASE.format(udps="\n".join(udp_lines))
            p.write_text(text, encoding="utf-8")
            return p

        def added_stream(new):
            """The exact element ensure_game_udp_for_dr3 inserts."""
            return (f'<udp enabled="true" extradata="3" ip="127.0.0.1" '
                    f'port="{target}" delay="1" />')

        # --- A: single enabled stream on SimHub default 20777 ---------------
        # Old behavior left it alone (DR3 then received nothing without a
        # SimHub forward). New: the 20777 element stays byte-identical and a
        # dedicated :20999 stream is added after it.
        p = write("simhub.xml", UDP_20777)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("20777 (SimHub default) stream added, not rewritten",
              action is not None and "added" in action, repr(action))
        check("20777 original element byte-identical", UDP_20777 in new)
        check("20777 dedicated :20999 stream present", added_stream(new) in new)
        check("20777 new stream inserted after the existing one",
              new.index(UDP_20777) < new.index(added_stream(new)))
        check("20777 rest of file preserved",
              '<dbox enabled="true" />' in new and "<assist_braking value=\"1\" />" in new)
        bak = p.parent / (p.name + ".bak")
        check("20777 .bak written with original",
              bak.exists() and bak.read_text(encoding="utf-8") == orig)
        check("20777 idempotent second run",
              mod.ensure_game_udp_for_dr3(target, path=str(p)) is None)

        # --- A2: SimHub default with extradata=0 stays untouched ------------
        p = write("simhub_ex0.xml", UDP_20777_EX0)
        orig = p.read_text(encoding="utf-8")
        mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("20777 extradata=0 original element byte-identical",
              UDP_20777_EX0 in new)
        check("20777 extradata=0 dedicated :20999 stream added",
              added_stream(new) in new)
        bak = p.parent / (p.name + ".bak")
        check("20777 extradata=0 .bak written with original",
              bak.exists() and bak.read_text(encoding="utf-8") == orig)

        # --- B: enabled on another port -> untouched + stream added ---------
        p = write("other.xml", UDP_30000)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("other port left untouched (action says added)",
              action is not None and "added" in action, repr(action))
        check("other port original element byte-identical", UDP_30000 in new)
        check("other port dedicated :20999 stream present",
              added_stream(new) in new)
        check("other port rest of file preserved",
              '<dbox enabled="true" />' in new and "<assist_braking value=\"1\" />" in new)
        bak = p.parent / (p.name + ".bak")
        check("other port .bak written with original",
              bak.exists() and bak.read_text(encoding="utf-8") == orig)
        check("other port idempotent second run",
              mod.ensure_game_udp_for_dr3(target, path=str(p)) is None)

        # --- C: disabled stream -> untouched + enabled stream added ---------
        p = write("disabled.xml", UDP_DISABLED)
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("disabled element left untouched (action says added)",
              action is not None and "added" in action, repr(action))
        check("disabled original element byte-identical", UDP_DISABLED in new)
        check("disabled dedicated :20999 stream enabled + present",
              added_stream(new) in new)

        # --- D: missing <udp> -> inserted at target -------------------------
        p = write("missing.xml")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("missing -> inserted (action mentions no <udp>)",
              action is not None and "<udp>" in action, repr(action))
        check("missing -> enabled udp at target present",
              f'port="{target}"' in new and 'enabled="true"' in new)
        check("missing -> inserted with extradata=3", 'extradata="3"' in new)
        check("missing -> inserted inside motion_platform",
              new.index("<motion_platform>") < new.index("<udp ")
              < new.index("</motion_platform>"))
        check("missing -> idempotent second run",
              mod.ensure_game_udp_for_dr3(target, path=str(p)) is None)

        # --- G: enabled on DR3 target port -> extradata normalized ----------
        p = write("target_ex0.xml", UDP_TARGET_EX0)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("target port extradata=0 normalized (action mentions extradata)",
              action is not None and "extradata=3" in action, repr(action))
        check("target port now extradata=3",
              'extradata="3"' in new and 'extradata="0"' not in new)
        check("target port single element only (no duplicate added)",
              new.count("<udp ") == 1)
        bak = p.parent / (p.name + ".bak")
        check("target port .bak written with original",
              bak.exists() and bak.read_text(encoding="utf-8") == orig)
        check("target port idempotent second run",
              mod.ensure_game_udp_for_dr3(target, path=str(p)) is None)

        # --- H: enabled on DR3 target port with extradata=3 -> untouched ----
        p = write("target_ex3.xml", UDP_TARGET_EX3)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        check("target port extradata=3 left untouched", action is None, repr(action))
        check("target port extradata=3 file unchanged",
              p.read_text(encoding="utf-8") == orig)
        check("target port extradata=3 no .bak written",
              not (p.parent / (p.name + ".bak")).exists())

        # --- I: live shape — DR3 stream first, SimHub second -> untouched ---
        p = write("live.xml", UDP_TARGET_EX3, UDP_20777)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        check("live multi-stream file left untouched", action is None, repr(action))
        check("live multi-stream file unchanged",
              p.read_text(encoding="utf-8") == orig)

        # --- J: two foreign streams -> both untouched + one added -----------
        p = write("two_foreign.xml", UDP_20777, UDP_30000)
        orig = p.read_text(encoding="utf-8")
        mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("two foreign: both original elements byte-identical",
              UDP_20777 in new and UDP_30000 in new)
        check("two foreign: exactly one :20999 stream added",
              new.count(f'port="{target}"') == 1)
        check("two foreign: new stream after the LAST existing element",
              new.index(UDP_30000) < new.index(added_stream(new)))
        bak = p.parent / (p.name + ".bak")
        check("two foreign: .bak written with original",
              bak.exists() and bak.read_text(encoding="utf-8") == orig)
        check("two foreign: idempotent second run",
              mod.ensure_game_udp_for_dr3(target, path=str(p)) is None)

        # --- E: no file at all -> no crash ----------------------------------
        missing_path = Path(tmp) / "does_not_exist.xml"
        check("no file -> returns None",
              mod.ensure_game_udp_for_dr3(target, path=str(missing_path)) is None)
        check("no file -> read_game_udp present=False",
              mod.read_game_udp(path=str(missing_path))["present"] is False)

        # --- F: custom_udp is never mistaken for udp ------------------------
        p = Path(tmp) / "custom.xml"
        p.write_text(
            '<hardware_settings_config>\n'
            '  <motion_platform>\n'
            '    <custom_udp enabled="true" ip="127.0.0.1" port="9999" />\n'
            '  </motion_platform>\n'
            '</hardware_settings_config>\n', encoding="utf-8")
        check("attrs: udp not matched by custom_udp",
              mod._xml_elem_attrs("udp", p.read_text(encoding="utf-8")) is None)
        check("elems: udp not matched by custom_udp",
              mod._xml_udp_elems(p.read_text(encoding="utf-8")) == [])
        check("read_game_udp: only custom_udp -> present=False",
              mod.read_game_udp(path=str(p))["present"] is False)
        orig = p.read_text(encoding="utf-8")
        mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("custom_udp left byte-identical when adding a stream",
              '<custom_udp enabled="true" ip="127.0.0.1" port="9999" />' in new)
        check("standard <udp> added next to custom_udp",
              f'port="{target}"' in new and new.count("<udp ") == 1)

        # --- read_game_udp parse --------------------------------------------
        p = write("parse.xml", UDP_20777)
        got = mod.read_game_udp(path=str(p))
        check("read_game_udp parses single 20777 enabled",
              got == {"present": True, "enabled": True, "port": 20777,
                      "ports": [20777]}, repr(got))

        p = write("parse_multi.xml", UDP_30000, UDP_20777)
        got = mod.read_game_udp(path=str(p))
        check("read_game_udp reports every enabled port in document order",
              got == {"present": True, "enabled": True, "port": 30000,
                      "ports": [30000, 20777]}, repr(got))

        p = write("parse_disabled.xml", UDP_DISABLED)
        got = mod.read_game_udp(path=str(p))
        check("read_game_udp: present but all disabled -> no ports",
              got == {"present": True, "enabled": False, "port": None,
                      "ports": []}, repr(got))

    if failures:
        print(f"\n{failures} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
