"""Append another map's engrams to data/engrams.json, without renumbering the existing ones.

Why not widen gen_engrams.py's exclusion lists and re-run it? Because that tool assigns ids as
ID_BASE + len(engrams) - sequential, no gaps - so un-excluding anything shifts every id after it.
Item ids are stored BY VALUE in data/maps.json membership, so a shift silently re-points map
membership at the wrong engrams. This appends after the current maximum; nothing existing moves.

Everything comes from the ArkAP.DumpEngrams dump, which is the only authority for two things:

  * `item_class` - the UClass GetFullName the plugin matches on. Tables.cpp does an exact string
    lookup (engram_class_to_item), so a shortened or hand-written class simply never fires.
  * which DLC an engram belongs to - read off the ASSET PATH (/Game/ScorchedEarth/...), which the
    game itself assigns.

The reference workbook is deliberately NOT used to classify. Its "Item Class" column is a
reconstruction and disagrees with the game on real entries: the game ships
PrimalItemStructure_AdobeLader (its own typo), AdobeStaircase and AdobeFrameGate where the sheet
says AdobeLadder, AdobeStairs and AdobeGate. Matching on it would silently drop those engrams.

    python tools/add_map_engrams.py <dump.json> --roots ScorchedEarth --also-name Adobe \\
        --maps scorched,ragnarok
    ... same again with --write
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checklist_schema as S                                            # noqa: E402
from gen_engrams import pretty, EXCLUDE                                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIRS = [os.path.join(ROOT, "data"), os.path.join(ROOT, "apworld", "ark_ase", "data")]


def load(name):
    with open(os.path.join(DATA_DIRS[0], name), encoding="utf-8") as fh:
        return json.load(fh)


def save(name, obj):
    for d in DATA_DIRS:
        if not os.path.isdir(d):
            continue
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
            fh.write("\n")
        print(f"   wrote {p}")


def asset_root(item_class):
    """'BlueprintGeneratedClass /Game/ScorchedEarth/Structures/...' -> 'ScorchedEarth'."""
    m = re.search(r"/Game/([^/]+)/", item_class)
    return m.group(1) if m else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", help="ArkAP.DumpEngrams output")
    ap.add_argument("--roots", required=True,
                    help="comma-separated asset roots to take, e.g. ScorchedEarth")
    ap.add_argument("--also-name", default="",
                    help="comma-separated name substrings to take from ANY root - for content whose "
                         "assets were filed under /Game/PrimalEarth/ despite belonging to the DLC "
                         "(the Adobe tri-panels and doorframes are)")
    ap.add_argument("--maps", required=True,
                    help="comma-separated map keys these engrams exist on, e.g. scorched,ragnarok")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    roots = {r.strip() for r in a.roots.split(",") if r.strip()}
    names = [n.strip().lower() for n in a.also_name.split(",") if n.strip()]
    want = S.parse_map_list(a.maps)

    maps_json = load("maps.json")
    known = {m["key"] for m in maps_json["maps"]}
    for k in want:
        if k not in known:
            sys.exit(f"unknown map key '{k}' - not on the Maps sheet")

    with open(a.dump, encoding="utf-8") as fh:
        dump = json.load(fh)
    engrams_json = load("engrams.json")
    engrams = engrams_json["engrams"]
    have = {e["engram_class"] for e in engrams}
    have_names = {e["ap_name"] for e in engrams}

    lo, hi = S.ID_BLOCKS["engram"]
    next_id = max(e["id"] for e in engrams) + 1

    added, dup_name = [], []
    for e in dump:
        cls = e["item_class"]
        if cls in have:
            continue                                     # already ours
        name = pretty(e["entry_name"])
        if name.replace(" ", "").lower() in EXCLUDE:     # auto-granted defaults
            continue
        root = asset_root(cls)
        by_root = root in roots
        by_name = any(n in name.lower() for n in names)
        if not (by_root or by_name):
            continue
        ap_name = "Engram: " + name
        if ap_name in have_names:
            dup_name.append(ap_name)                     # same display name, different class
            continue
        if next_id > hi:
            sys.exit(f"engram id block exhausted ({lo}-{hi})")
        added.append({"id": next_id, "ap_name": ap_name, "engram_class": cls, "tier": "auto",
                      "_root": root, "_why": "root" if by_root else "name"})
        have_names.add(ap_name)
        next_id += 1

    print(f"dump entries            : {len(dump)}")
    print(f"already in engrams.json : {len(have)}")
    print(f"TO ADD                  : {len(added)}  -> maps: {', '.join(want)}")
    from collections import Counter
    print(f"   by asset root        : {dict(Counter(x['_root'] for x in added))}")
    print(f"   matched by name rule : {sum(1 for x in added if x['_why'] == 'name')}")
    for x in added:
        print(f"     {x['id']}  {x['ap_name']:38} /Game/{x['_root']}/")
    if dup_name:
        print(f"\nSKIPPED - display name already in use ({len(dup_name)}): {sorted(set(dup_name))}")

    if not a.write:
        print("\nDRY RUN. Re-run with --write to apply.")
        return
    if not added:
        print("\nnothing to add")
        return

    for x in added:
        x.pop("_root", None)
        x.pop("_why", None)
    engrams.extend(added)
    base = engrams_json.get("_comment", "").split(" Appended:")[0]
    engrams_json["_comment"] = (
        f"{base} Appended: {len(added)} engram(s) for {', '.join(want)} by add_map_engrams.py "
        f"(ids continue past the generated block - existing ids are never renumbered, because "
        f"maps.json membership stores item ids by value).")
    save("engrams.json", engrams_json)

    content = maps_json["content"]
    for x in added:
        for k in want:
            b = content.setdefault(k, {"items": [], "locations": []})
            if x["id"] not in b["items"]:
                b["items"].append(x["id"])
    for b in content.values():
        b["items"] = sorted(set(b["items"]))
        b["locations"] = sorted(set(b["locations"]))
    save("maps.json", maps_json)
    print(f"\n{len(added)} engram(s) added; engrams.json now has {len(engrams)}")


if __name__ == "__main__":
    main()
