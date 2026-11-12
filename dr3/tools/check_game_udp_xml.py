"""Verify the game hardware_settings_config.xml telemetry helpers in
dr2server/telemetry.py (read_game_udp / ensure_game_udp_for_dr3):

- a missing <udp> line is inserted (enabled, at DR3's port) inside
  <motion_platform>;
- a disabled <udp> is enabled (port kept);
- an enabled <udp> on the SimHub default 20777 is left untouched;
- an enabled <udp> on any other port is rewritten to DR3's port;
- edits write a .bak backup, preserve the rest of the file, and are idempotent;
- _xml_elem_attrs("udp") never matches a differently-named element such as
  custom_udp.

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
    '    {udp}\n'
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
        def write(name, udp_line):
            p = Path(tmp) / name
            if udp_line is None:
                # motion_platform present but no <udp> line at all.
                text = (BASE.replace("    {udp}\n", "")
                        .replace("{udp}", ""))
            else:
                text = BASE.format(udp=udp_line)
            p.write_text(text, encoding="utf-8")
            return p

        # --- A: enabled on SimHub default 20777 -> untouched ----------------
        p = write("simhub.xml", UDP_20777)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        check("20777 (SimHub default) left untouched", action is None, repr(action))
        check("20777 file unchanged", p.read_text(encoding="utf-8") == orig)
        check("20777 no .bak written", not (p.parent / (p.name + ".bak")).exists())

        # --- A2: SimHub default with extradata=0 is still untouched ---------
        p = write("simhub_ex0.xml", UDP_20777_EX0)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        check("20777 extradata=0 (SimHub default) left untouched",
              action is None, repr(action))
        check("20777 extradata=0 file unchanged", p.read_text(encoding="utf-8") == orig)
        check("20777 extradata=0 no .bak written",
              not (p.parent / (p.name + ".bak")).exists())

        # --- B: enabled on another port -> rewritten to target --------------
        p = write("other.xml", UDP_30000)
        orig = p.read_text(encoding="utf-8")
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("other port rewritten (action mentions 30000)",
              action is not None and "30000" in action, repr(action))
        check("other port now target", f'port="{target}"' in new)
        check("other port old value gone", 'port="30000"' not in new)
        check("other port extradata normalized to 3",
              'extradata="3"' in new and 'extradata="0"' not in new)
        check("other port rest of file preserved",
              '<dbox enabled="true" />' in new and "<assist_braking value=\"1\" />" in new)
        bak = p.parent / (p.name + ".bak")
        check("other port .bak written with original",
              bak.exists() and bak.read_text(encoding="utf-8") == orig)
        check("other port idempotent second run",
              mod.ensure_game_udp_for_dr3(target, path=str(p)) is None)

        # --- C: disabled -> enabled, port kept ------------------------------
        p = write("disabled.xml", UDP_DISABLED)
        action = mod.ensure_game_udp_for_dr3(target, path=str(p))
        new = p.read_text(encoding="utf-8")
        check("disabled -> enabled (action mentions disabled)",
              action is not None and "disabled" in action, repr(action))
        check("disabled now enabled", 'enabled="true"' in new)
        check("disabled keeps its port 20777", 'port="20777"' in new)
        check("disabled extradata normalized to 3",
              'extradata="3"' in new and 'extradata="0"' not in new)

        # --- D: missing <udp> -> inserted at target -------------------------
        p = write("missing.xml", None)
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
        check("read_game_udp: only custom_udp -> present=False",
              mod.read_game_udp(path=str(p))["present"] is False)

        # --- read_game_udp parse --------------------------------------------
        p = write("parse.xml", UDP_20777)
        got = mod.read_game_udp(path=str(p))
        check("read_game_udp parses 20777 enabled",
              got == {"present": True, "enabled": True, "port": 20777}, repr(got))

    if failures:
        print(f"\n{failures} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
