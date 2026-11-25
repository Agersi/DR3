"""Verify dr3/web/static/cars/cars.json against the game vehicle catalog.

Maps every cars.json entry to its catalog Vehicle id (dr2server.game_data)
using per-class optimal name matching, then cross-checks both directions:
vehicles missing from cars.json, duplicate image paths, image files on disk
that no entry references, and entries whose image file does not exist on disk.

Usage:  python tools/verify_cars_json.py [path/to/cars.json]
"""
import difflib
import itertools
import json
import os
import re
import sys
import unicodedata
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dr3"))

from dr2server.game_data import Vehicle, _VEHICLE_META  # noqa: E402


def norm_tokens(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return [t for t in s.split() if t]


def entry_score(entry, vid):
    name = f"{entry.get('brand', '')} {entry.get('model', '')}"
    etoks = set(norm_tokens(name))
    dtoks = set(norm_tokens(_VEHICLE_META[vid]["display_name"]))
    if etoks == dtoks:
        return 1.0, "exact"
    if (dtoks < etoks and len(dtoks) >= 2) or (etoks < dtoks and len(etoks) >= 2):
        return 0.9, "subset"
    ename = " ".join(sorted(etoks))
    dname = " ".join(sorted(dtoks))
    return difflib.SequenceMatcher(None, ename, dname).ratio() * 0.85, "fuzzy"


def main():
    cars_json = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "dr3", "web", "static", "cars", "cars.json")
    base_dir = os.path.dirname(os.path.abspath(cars_json))

    with open(cars_json, encoding="utf-8") as f:
        entries = json.load(f)

    by_class = {}
    for vid in Vehicle:
        meta = _VEHICLE_META[vid]
        by_class.setdefault(meta["vehicle_class"].label, []).append(vid)

    class_entries = {}
    for i, e in enumerate(entries):
        class_entries.setdefault(e.get("class", ""), []).append(i)

    assignment = {}
    problems = []
    notes = []

    for cls, idxs in class_entries.items():
        cands = list(by_class.get(cls, []))
        if len(idxs) != len(cands):
            problems.append(
                f"class '{cls}': {len(idxs)} cars.json entries vs "
                f"{len(cands)} catalog vehicles")
        n = min(len(idxs), len(cands))
        if n == 0:
            continue
        if max(len(idxs), len(cands)) > 8:
            order = sorted(
                ((entry_score(entries[i], v)[0], i, v) for i in idxs for v in cands),
                reverse=True)
            used_i, used_v = set(), set()
            for score, i, v in order:
                if i in used_i or v in used_v:
                    continue
                assignment[i] = v
                used_i.add(i)
                used_v.add(v)
        else:
            best_perm, best_total = None, -1.0
            for perm in itertools.permutations(range(len(cands)), n):
                total = 0.0
                for k in range(n):
                    total += entry_score(entries[idxs[k]], cands[perm[k]])[0]
                if total > best_total:
                    best_total, best_perm = total, perm
            for k in range(n):
                assignment[idxs[k]] = cands[best_perm[k]]

    mapping = []
    for i, e in enumerate(entries):
        vid = assignment.get(i)
        if vid is None:
            problems.append(
                f"line {i + 1}: NO MATCH for class '{e.get('class', '')}': "
                f"'{e.get('brand', '')} {e.get('model', '')}'")
            continue
        score, how = entry_score(e, vid)
        if how != "exact":
            notes.append(
                f"line {i + 1}: {how} match for '{e.get('brand', '')} "
                f"{e.get('model', '')}' -> {_VEHICLE_META[vid]['display_name']} "
                f"({vid}) [score {score:.2f}]")
        mapping.append((int(vid), e.get("image", ""), e.get("class", ""),
                        f"{e.get('brand', '')} {e.get('model', '')}"))

    img_counter = Counter(e.get("image", "") for e in entries)
    for img, n in img_counter.items():
        if n > 1:
            problems.append(f"duplicate image path x{n}: {img}")

    used_vids = {vid for vid in assignment.values()}
    for vid in Vehicle:
        if vid not in used_vids:
            problems.append(
                f"vehicle NOT in cars.json: {vid} {_VEHICLE_META[vid]['display_name']}")

    disk_imgs = set()
    for dirpath, _dirnames, filenames in os.walk(base_dir):
        for fn in filenames:
            if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                rel = os.path.relpath(os.path.join(dirpath, fn), base_dir)
                disk_imgs.add(rel.replace("\\", "/"))
    referenced = {e.get("image", "") for e in entries}
    for img in sorted(disk_imgs - referenced):
        problems.append(f"image on disk not referenced by cars.json: {img}")
    for img in sorted(referenced - disk_imgs):
        problems.append(f"cars.json image MISSING on disk: {img}")

    order = sorted(mapping, key=lambda m: (m[2], m[0]))
    cur = None
    for vid, img, cls, name in order:
        if cls != cur:
            print(f"\n== {cls} ==")
            cur = cls
        print(f"  {vid:>4}  {img:<38} {name}")

    print(f"\nentries: {len(entries)}  vehicles in catalog: {len(list(Vehicle))}  "
          f"mapped: {len(mapping)}")
    if notes:
        print("\nNOTES (non-exact name matches, review):")
        for n in notes:
            print("  -", n)
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  -", p)
    else:
        print("\nOK: one-to-one mapping, no orphan images, no missing vehicles")


if __name__ == "__main__":
    main()
