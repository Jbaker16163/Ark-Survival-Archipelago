"""Put the /aptrail cave maps into the PopTracker pack.

For every cave recorded under trails/<map>/ (the same caves the gallery in tools/cave_map_edit.py
shows) whose cave holds an artifact in tools/reference/poptracker_logic.json `artifacts._caves`:

  * render it if it is out of date (build/caves/<cave>.png, edits applied);
  * copy the picture to tools/reference/poptracker_art/maps/<Pop>_<Cave>.png, which replaces that
    cave tab's "Coming Soon" card on the next pack build;
  * write each artifact's pixel on the picture into `artifacts._cave_pins`, so the tab gets one
    pin per artifact right on its icon.

Every artifact the table puts in that cave must be marked on the map ("/aptrail arrived", or
"artifact <Name>" placed in the editor) - otherwise the cave is skipped and says why, rather than
guessing a pin. A marked artifact the table puts in another cave is reported too.

Then rebuild the pack:  python tools/poptracker_add_map.py --all

Usage:  python tools/cave_maps_to_poptracker.py [--dry-run]
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cave_map_edit as E  # noqa: E402
import cave_trail_map as ctm  # noqa: E402
from poptracker_logic_apply import cave_map_name  # noqa: E402

LOGIC = os.path.join(ROOT, "tools", "reference", "poptracker_logic.json")
MAPS_CFG = os.path.join(ROOT, "tools", "reference", "poptracker_maps.json")
ART_MAPS = os.path.join(ROOT, "tools", "reference", "poptracker_art", "maps")
PIN_SOURCE = ("Pixel positions on the cave's own map image (poptracker_art/maps/<Map>_<Cave>.png). "
              "Life's Labyrinth: read off the artifact icons on the map Justin supplied 2026-11-10 (2000x1985 "
              "high-res version, ARK Patch 276; original in _source/maps/Ragnarok_Life_s_Labyrinth.jpg). The "
              "others: written by tools/cave_maps_to_poptracker.py from the /aptrail cave renders "
              "(build/caves/<cave>.json pins). Devourer: Pit of Dreen is no cave, its tab is the user's "
              "in-game GPS screenshot (_source/maps/Ragnarok_Pit_of_Dreen_gps.webp) with a title box; the "
              "pin sits in that box. A cave listed here gets one pin per artifact on its tab "
              "instead of one shared pin.")


def write_pins(text, map_key, pins):
    """Rewrite one map's artifacts._cave_pins block in place, keeping the file's hand layout."""
    # the block sits inside "<map_key>": { ... "artifacts": { ... "_cave_pins": { ... } ... } }
    start = text.find('"%s": {' % map_key)
    if start < 0:
        sys.exit("poptracker_logic.json has no %s section" % map_key)
    m = re.compile(r'( *)"_cave_pins": \{\n(.*?)\n\1\}', re.S).search(text, start)
    nxt = re.compile(r'\n  "[^"]+": \{').search(text, start + 1)
    if not m or (nxt and m.start() > nxt.start()):
        sys.exit("no artifacts._cave_pins block in the %s section of poptracker_logic.json - add "
                 "one by hand first ({\"_size\": 24})" % map_key)
    ind = m.group(1) + "  "
    # keep a map's own hand-written _source (Valguero's lists maps the user drew); the generic
    # text only goes into a block that has none
    src = pins.get("_source") or PIN_SOURCE
    lines = ['%s"_source": %s,' % (ind, json.dumps(src, ensure_ascii=False)),
             '%s"_size": %d' % (ind, pins.get("_size", 24))]
    for k, v in pins.items():
        if not k.startswith("_"):
            lines[-1] += ","
            lines.append('%s"%s": [%d, %d]' % (ind, k, v[0], v[1]))
    block = '%s"_cave_pins": {\n%s\n%s}' % (m.group(1), "\n".join(lines), m.group(1))
    return text[:m.start()] + block + text[m.end():]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    args = ap.parse_args(argv)

    raw = open(LOGIC, encoding="utf-8").read()
    logic = json.loads(raw)
    cfg = json.load(open(MAPS_CFG, encoding="utf-8"))
    caves = E.scan()
    if not caves:
        sys.exit("no recordings under trails/")
    done, skipped = 0, 0
    new_pins = {}
    for key, c in sorted(caves.items()):
        pop = (cfg.get(c.map) or {}).get("pop")
        arts_tbl = (logic.get(c.map) or {}).get("artifacts") or {}
        # the hand table wins; a map without one (Valguero) uses its config's artifact_caves,
        # which is what the generated pack builds its cave tabs from
        table = dict(arts_tbl.get("_caves") or (cfg.get(c.map) or {}).get("artifact_caves") or {})
        if not pop or not table:
            print("%s: map %s has no PopTracker artifact table - skipped" % (c.name, c.map))
            skipped += 1
            continue
        # the table's spelling of the cave (recordings may differ in case / punctuation)
        tname = next((cv for cv in set(table.values()) if ctm.slug(cv) == c.slug), None)
        if not tname:
            print("%s: not an artifact cave in poptracker_logic.json %s._caves - no cave tab to "
                  "fill, skipped" % (c.name, c.map))
            skipped += 1
            continue
        if tname in (arts_tbl.get("_no_cave_tab") or []):
            print("%s: listed in _no_cave_tab (not a cave) - skipped" % tname)
            skipped += 1
            continue
        ok, log = c.ensure([])
        if not ok or not os.path.isfile(c.side):
            print("%s: render failed - open it in tools/cave_map_edit.py to see why\n%s" % (c.name, log))
            skipped += 1
            continue
        side = json.load(open(c.side, encoding="utf-8"))
        pins = side.get("pins") or {}
        want = sorted(a for a, cv in table.items() if cv == tname)
        missing = [a for a in want if a not in pins]
        stray = sorted(a for a in pins if a not in want)
        if stray:
            print("%s: WARNING artifact mark(s) %s are not in this cave per _caves (%s) - check the "
                  "label against its GPS" % (tname, ", ".join(stray), ", ".join(want)))
        if missing:
            print("%s: artifact(s) %s not marked on the map - skipped (mark them, then run again)"
                  % (tname, ", ".join(missing)))
            skipped += 1
            continue
        dest = os.path.join(ART_MAPS, cave_map_name(pop, tname) + ".png")
        for a in want:
            new_pins[(c.map, a)] = pins[a]
        print("%s: %s -> %s, pins %s" % (tname, os.path.relpath(c.out, ROOT), os.path.relpath(dest, ROOT),
                                         ", ".join("%s %s" % (a, pins[a]) for a in want)))
        if not args.dry_run:
            os.makedirs(ART_MAPS, exist_ok=True)
            shutil.copyfile(c.out, dest)
        done += 1

    by_map = {}
    for (mk, a), v in new_pins.items():
        by_map.setdefault(mk, {})[a] = v
    for mk, add in by_map.items():
        pins = dict((logic[mk]["artifacts"].get("_cave_pins") or {"_size": 24}))
        pins.update(add)
        raw = write_pins(raw, mk, pins)
    if by_map and not args.dry_run:
        json.loads(raw)                     # never write a file that does not parse
        with open(LOGIC, "w", encoding="utf-8") as fh:
            fh.write(raw)
    print("\n%d cave map(s) %s, %d skipped.%s" % (
        done, "would be copied" if args.dry_run else "copied", skipped,
        "" if args.dry_run or not done else
        " Now rebuild the pack: python tools/poptracker_add_map.py --all"))


if __name__ == "__main__":
    main()
