#!/usr/bin/env python3
"""Turn /dumppos samples into the exploration-check areas the apworld and plugin both read.

    python tools/gen_explore_map.py <ArkAP_positions.jsonl> [--map island] [--calib LAT LON X Y]

Input is the jsonl the plugin's /dumppos writes: {"key","x","y","z"} per sample.

THE SAMPLES ARE POLYGON VERTICES, IN FLIGHT ORDER. You circle a region and everything inside the
loop counts as that region. This replaced an earlier bounding-box approach, which fattened every
curved region out to its extremes and swallowed whole neighbours (a single box around the Redwoods
loop contained all of Red Peak). Two consequences:
  * ORDER MATTERS - never sort or reshuffle the file.
  * RE-FLYING a region means deleting its old lines first, or the two laps weave together.

Overlap between regions is fine and expected: caves sit under biomes, the Volcanic Maw sits inside
the Volcano. A player inside several polygons completes all of them.

Altitude is recorded but not used for membership - you fly through the volcano to reach its maw, so
a height band would only cause false negatives. `z` is kept in the output anyway so an altitude
floor can be added later (the underwater caves will want one) without re-flying anything.

Outputs:
  data/explore_areas.json        + the apworld's copy - polygons the apworld and plugin both load
  docs/exploration_overlay.html  the loops drawn over YOUR OWN copy of the Island map

MAP IMAGE: not shipped - it is Studio Wildcard's asset. Save your own copy next to the html as
docs/island_map.jpg. Without it the overlay still renders, just on a plain grid.
"""
import argparse
import json
import os
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DATA_DIRS = [os.path.join(ROOT, "data"), os.path.join(ROOT, "apworld", "ark_ase", "data")]

# region key -> (display name, gear gate). Keys are what you type after /dumppos.
#
# GATES are the survival gear the place physically demands, nothing else. Gate only what would
# genuinely kill an unequipped player, because every gate is a logic requirement the fill has to
# respect - an over-gated sightseeing check just strands items.
#
# THE ISLAND ONLY. Other maps get their own block with their own keys when they are mapped; the
# "map" field in the output is what the apworld filters on, so keys never need to be unique across
# maps - only within one.
ISLAND_REGIONS = OrderedDict([
    # ---- mountains + volcano ----
    ("volcano",             ("Volcano", "")),
    ("volcanicmaw",         ("Volcanic Maw", "")),
    ("farspeak",            ("Far's Peak", "")),
    ("grandhills",          ("The Grand Hills", "")),
    ("redpeak",             ("The Red Peak", "")),
    ("weathertop",          ("Weathertop", "")),
    ("themaw",              ("The Maw", "")),
    # ---- snow / ice (cold: needs Fur) ----
    ("whitesky",            ("Whitesky Peak", "Fur")),
    ("whiteskymountain",    ("Whitesky Mountain", "Fur")),
    ("wintersmouth",        ("Winter's Mouth", "Fur")),
    ("frozentooth",         ("The Frozen Tooth", "Fur")),
    ("frozenfang",          ("The Frozen Fang", "Fur")),
    ("frigidplains",        ("The Frigid Plains", "Fur")),
    ("southerniceberg",     ("Southern Iceberg", "Fur")),
    ("icespikes",           ("The Ice Spikes", "Fur")),
    ("icebergwitharch",     ("Iceberg Arch", "Fur")),
    # ---- islands + coastline ----
    ("craggs",              ("Cragg's Island", "")),
    ("deadisland",          ("The Dead Island (Carno Island)", "")),
    ("southhaven",          ("South Haven (Herbivore Island)", "")),
    ("southernislets",      ("Southern Islets", "")),
    ("footpaw",             ("The Footpaw", "")),
    ("drayoscove",          ("Drayo's Cove", "")),
    ("westernpeninsula",    ("The Western Peninsula", "")),
    ("southernrockpillars", ("Southern Rock Pillars", "")),
    ("hiddenlake",          ("The Hidden Lake", "")),
    # ---- forest + swamp ----
    ("redwoods",            ("The Redwood Forests", "")),
    ("southernjungle",      ("Southern Jungle", "")),
    ("westernswamp",        ("The Western Swamp", "")),
    ("centralswamp",        ("The Central Swamp", "")),
    ("easternswamp",        ("The Eastern Swamp", "")),
    # ---- obelisks ----
    ("blueobelisk",         ("Blue Obelisk", "")),
    ("greenobelisk",        ("Green Obelisk", "")),
    ("redobelisk",          ("Red Obelisk", "")),
    # ---- caves ----
    ("centralcave",         ("Central Cave", "")),
    ("lowersouthcave",      ("Lower South Cave", "")),
    ("uppersouthcave",      ("Upper South Cave", "")),
    ("northeastcave",       ("North East Cave", "")),
    ("northwestcave",       ("North West Cave", "")),
    ("lavacave",            ("Lava Cave", "")),
    ("swampcave",           ("Swamp Cave", "")),
    ("snowcave",            ("Snow Cave", "Fur")),
    ("tekcave",             ("Tek Cave Entrance", "")),
    # deep-sea caverns - you cannot reach these without diving gear
    ("cavernsoflosthope",   ("Caverns of Lost Hope", "Scuba")),
    ("cavernsoflostfaith",  ("Caverns of Lost Faith", "Scuba")),
])
REGIONS = {"island": ISLAND_REGIONS}

# DEPTH regions have no polygon: they fire anywhere on the map below a given world Z. You cannot
# circle the deep ocean the way you circle a landmass - it is the whole seabed - and a surface
# polygon would fire while a bird flies over it, defeating the Scuba gate entirely. A depth floor
# is the honest test: you are only down there if you actually went down there.
#     key -> (display name, gate, map, z_below)
DEPTH_REGIONS = OrderedDict([
    ("deepocean", ("Deep Ocean", "Scuba", "island", -35253)),
])

EXPLORE_ID_BASE = 8758000        # own block, after inventory checks (8757xxx)
MIN_POINTS = 3                   # fewer than 3 samples is not a polygon


def polygon_area(pts):
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def self_crossings(pts):
    """Loops that close slightly past their start flip a sliver of area. Worth reporting, not
    worth refusing - the sliver is tiny next to the region."""
    def orient(a, b, c):
        v = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
        return 0 if v == 0 else (1 if v > 0 else 2)

    def crosses(a, b, c, d):
        return orient(a, b, c) != orient(a, b, d) and orient(c, d, a) != orient(c, d, b)

    n, hits = len(pts), 0
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if crosses(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n]):
                hits += 1
    return hits


def point_in(pt, poly):
    x, y = pt
    inside, n = False, len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--map", default="island", help="which map these samples belong to")
    ap.add_argument("--calib", nargs=4, type=float, metavar=("LAT", "LON", "X", "Y"),
                    help="one known point: in-game GPS lat/lon and the world X/Y at that spot")
    ap.add_argument("--scale", type=float, default=8000, help="world units per degree")
    a = ap.parse_args()

    known = REGIONS.get(a.map)
    if known is None:
        raise SystemExit(f"unknown map '{a.map}' - known: {', '.join(REGIONS)}")

    samples = OrderedDict()
    with open(a.jsonl, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                samples.setdefault(str(r["key"]).lower(), []).append(
                    (int(r["x"]), int(r["y"]), int(r.get("z", 0))))
            except Exception:
                print(f"  ! skipping unparseable line: {line[:70]}")

    unknown = [k for k in samples if k not in known]
    if unknown:
        print(f"! not registered for map '{a.map}' (typo?): {', '.join(unknown)}")

    regions, next_id = OrderedDict(), EXPLORE_ID_BASE
    for key, (name, gate, mp, z_below) in DEPTH_REGIONS.items():
        if mp != a.map:
            continue
        regions[key] = {"id": next_id, "name": name, "gate": gate, "map": mp,
                        "polygon": [], "z_below": z_below}
        next_id += 1
    for key, (name, gate) in known.items():          # registry order = stable id order
        pts = samples.get(key)
        if not pts:
            continue
        if len(pts) < MIN_POINTS:
            print(f"  ! {key}: only {len(pts)} sample(s) - need {MIN_POINTS} for a polygon, skipped")
            continue
        poly = [(p[0], p[1]) for p in pts]
        regions[key] = {"id": next_id, "name": name, "gate": gate, "map": a.map,
                        "polygon": [[x, y] for x, y in poly],
                        "z_min": min(p[2] for p in pts), "z_max": max(p[2] for p in pts)}
        next_id += 1

    out = {"_comment": "Exploration areas measured in-game with /dumppos. Each polygon's points are "
                       "vertices IN FLIGHT ORDER - a player inside the loop has explored that "
                       "region. Overlap is intended (caves sit under biomes); a player inside "
                       "several polygons completes all of them. z_min/z_max are recorded but NOT "
                       "used for membership. Never reorder a polygon's points.",
           "_id_base": EXPLORE_ID_BASE, "regions": regions}

    for d in DATA_DIRS:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "explore_areas.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
            fh.write("\n")

    print(f"\nmap '{a.map}': {len(regions)} region(s) -> ids {EXPLORE_ID_BASE}..{next_id - 1}")
    gated = [r for r in regions.values() if r["gate"]]
    print(f"  gated: {len(gated)}  ({', '.join(sorted({r['gate'] for r in gated})) or '-'})")
    depth = [k for k, r in regions.items() if not r["polygon"]]
    if depth:
        print(f"  depth-only (no polygon): {', '.join(depth)}")
    crossed = [k for k, r in regions.items()
               if r["polygon"] and self_crossings([tuple(p) for p in r["polygon"]])]
    if crossed:
        print(f"  loops that close past their start (harmless sliver): {', '.join(crossed)}")
    missing = [k for k in known if k not in regions]
    if missing:
        print(f"\nSTILL TO MAP ({len(missing)}): {', '.join(missing)}")

    # A gear-gated region reachable from inside an UNGATED one hands out the gated check for free,
    # which would let the fill place progression behind a gate that is not really there.
    leaks = []
    for gk, g in regions.items():
        if not g["gate"] or not g["polygon"]:
            continue
        for ok, o in regions.items():
            if ok == gk or o["gate"] or not o["polygon"]:
                continue
            gp = [tuple(p) for p in g["polygon"]]
            op = [tuple(p) for p in o["polygon"]]
            if all(point_in(p, op) for p in gp):
                leaks.append(f"{gk} ({g['gate']}) is fully inside ungated {ok}")
    if leaks:
        print("\n!! GATE LEAKS - the gate can be bypassed:")
        for l in leaks:
            print("   ", l)
    else:
        print("  gate check: no gated region sits inside an ungated one")

    print(f"\nwrote explore_areas.json to {len(DATA_DIRS)} data dir(s)")
    write_overlay(regions, a)


def write_overlay(regions, a):
    if a.calib:
        lat0, lon0, x0, y0 = a.calib
        to_lon = lambda x: lon0 + (x - x0) / a.scale       # noqa: E731
        to_lat = lambda y: lat0 + (y - y0) / a.scale       # noqa: E731
        note = (f"Calibrated from one measured point: GPS {lat0:.1f}/{lon0:.1f} at world "
                f"({x0:.0f}, {y0:.0f}), {a.scale:.0f} units per degree.")
    else:
        allp = [p for r in regions.values() for p in r["polygon"]]
        xs = [p[0] for p in allp] or [0]
        ys = [p[1] for p in allp] or [0]
        sx, sy = min(xs), min(ys)
        rx, ry = (max(xs) - sx) or 1, (max(ys) - sy) or 1
        to_lon = lambda x: (x - sx) / rx * 100            # noqa: E731
        to_lat = lambda y: (y - sy) / ry * 100            # noqa: E731
        note = ("NOT calibrated - stretched to fit the samples, so it will not line up with the "
                "map image. Re-run with --calib LAT LON X Y for true placement.")

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    shapes = []
    for key, r in regions.items():
        if not r["polygon"]:
            continue                                   # depth region - nothing to draw
        cls = "fur" if r["gate"] == "Fur" else "scuba" if r["gate"] == "Scuba" else "plain"
        pts = " ".join(f"{to_lon(x):.3f},{to_lat(y):.3f}" for x, y in r["polygon"])
        cx = sum(to_lon(x) for x, _ in r["polygon"]) / len(r["polygon"])
        cy = sum(to_lat(y) for _, y in r["polygon"]) / len(r["polygon"])
        shapes.append(f'<polygon class="{cls}" points="{pts}"><title>{esc(r["name"])}'
                      f'{" - needs " + r["gate"] if r["gate"] else ""}</title></polygon>')
        shapes.append(f'<text x="{cx:.2f}" y="{cy:.2f}">{esc(r["name"])}</text>')

    html = f"""<title>ARK:ipelago - Measured exploration areas</title>
<style>
 body{{margin:0;padding:20px;background:#12161c;color:#e8edf3;
      font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",sans-serif}}
 .wrap{{max-width:1100px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} p{{color:#8b98a8;margin:0 0 14px}}
 .note{{border-left:3px solid #5bc0de;background:rgba(91,192,222,.08);padding:9px 13px;
       border-radius:0 6px 6px 0;color:#e8edf3;margin:0 0 16px}}
 .map{{position:relative;width:100%;aspect-ratio:1/1;border:1px solid #2a323d;border-radius:10px;
      overflow:hidden;background:#0d1b2a}}
 .map img,.map svg{{position:absolute;inset:0;width:100%;height:100%}}
 .map img{{object-fit:fill}}
 polygon{{fill:rgba(201,212,224,.13);stroke:#c9d4e0;stroke-width:.25;vector-effect:non-scaling-stroke}}
 polygon.fur{{fill:rgba(127,179,255,.16);stroke:#7fb3ff}}
 polygon.scuba{{fill:rgba(79,214,196,.16);stroke:#4fd6c4}}
 text{{fill:#e8edf3;font:2.2px ui-sans-serif,sans-serif;text-anchor:middle;
       paint-order:stroke;stroke:#000;stroke-width:.6px}}
 code{{background:rgba(255,255,255,.1);padding:1px 5px;border-radius:4px;
       font:12px ui-monospace,Consolas,monospace}}
</style>
<div class="wrap">
<h1>Measured exploration areas</h1>
<p>{len(regions)} regions circled with <code>/dumppos</code>. Blue = needs Fur, teal = needs Scuba.</p>
<div class="note">{esc(note)}</div>
<div class="map">
  <img src="island_map.jpg" alt="" onerror="this.style.display='none'">
  <svg viewBox="0 0 100 100" preserveAspectRatio="none">
  {chr(10) + "  ".join(shapes)}
  </svg>
</div>
<p style="margin-top:14px">Drop your own copy of the Island map at <code>docs/island_map.jpg</code>
to see the loops over the real terrain. The image is not shipped with the repo.</p>
</div>
"""
    dst = os.path.join(ROOT, "docs", "exploration_overlay.html")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
