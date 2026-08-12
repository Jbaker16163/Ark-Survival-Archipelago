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

# WORLD COORDS -> IN-GAME GPS. ARK maps use lat = y/divisor + shift, lon = x/divisor + shift, and
# The Island uses 8000 / 50. This was checked against the three obelisks in our own sample data and
# lands within 0.1 degrees of their published positions - so the overlay no longer needs a
# calibration point, and --calib is only there to override a map whose constants differ.
#     key -> (divisor, shift)
MAP_TRANSFORM = {"island": (8000.0, 50.0), "ragnarok": (13009.4, 49.99),
                 "scorched": (8000.0, 50.0)}

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
    ap.add_argument("--no-embed", action="store_true",
                    help="do NOT inline the Island map image. The page is published, and the image "
                         "is a Wildcard asset - use this for a repo copy that ships only our own "
                         "measured data. Readers can drop their own docs/island_map.png beside it.")
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


def island_map_data_uri(embed=True):
    """Embed the local Island map as a data URI so the page is self-contained.

    It used to be a plain <img src="island_map.jpg">, which meant the background silently vanished
    if the file was named .png (it was), or if the html was opened from anywhere else. The image is
    a Wildcard asset so it is still never committed - docs/island_map.* is gitignored, and this html
    is too. Absent image = the page falls back to the plain grid.
    """
    import base64
    if not embed:
        print("  --no-embed: linking docs/island_map.png instead of inlining it")
        return "", "link"
    for name, mime in (("island_map.png", "image/png"), ("island_map.jpg", "image/jpeg"),
                       ("island_map.jpeg", "image/jpeg"), ("island_map.webp", "image/webp")):
        path = os.path.join(ROOT, "docs", name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            print(f"  embedded {name} ({os.path.getsize(path):,} bytes)")
            return f"data:{mime};base64,{b64}", name
    print("  no docs/island_map.* found - the overlay will render on a plain grid")
    return "", ""


def write_overlay(regions, a):
    """Draw the measured loops over the real Island map, positioned by in-game GPS.

    ARK's lat/lon run 0-100 across a map, so a GPS coordinate IS a percentage - the SVG viewBox is
    literally 0 0 100 100 and no extra scaling is needed. Earlier versions stretched the samples to
    fit their own bounding box, which meant the shapes never lined up with the map image."""
    div, shift = MAP_TRANSFORM.get(a.map, (a.scale, 50.0))
    if a.calib:                                     # explicit override for a map with odd constants
        lat0, lon0, x0, y0 = a.calib
        to_lat = lambda y: lat0 + (y - y0) / a.scale        # noqa: E731
        to_lon = lambda x: lon0 + (x - x0) / a.scale        # noqa: E731
        note = (f"Calibrated from a measured point: GPS {lat0:.1f}/{lon0:.1f} at world "
                f"({x0:.0f}, {y0:.0f}).")
    else:
        to_lat = lambda y: y / div + shift                  # noqa: E731
        to_lon = lambda x: x / div + shift                  # noqa: E731
        note = (f"Positioned by in-game GPS (lat = y/{div:.0f} + {shift:.0f}), verified against the "
                f"three obelisks to within 0.1 degrees.")

    def esc(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def cls_of(r):
        return "fur" if r["gate"] == "Fur" else "scuba" if r["gate"] == "Scuba" else "plain"

    shapes, rows = [], []
    for key, r in regions.items():
        poly = r["polygon"]
        if not poly:                                # depth region - no shape to draw
            rows.append((r["name"], key, r["gate"], "-", "-", "-",
                         f"below z {r['z_below']:,}", cls_of(r)))
            continue
        lats = [to_lat(y) for _, y in poly]
        lons = [to_lon(x) for x, _ in poly]
        pts = " ".join(f"{to_lon(x):.3f},{to_lat(y):.3f}" for x, y in poly)
        cx, cy = sum(lons) / len(lons), sum(lats) / len(lats)
        shapes.append(
            f'<polygon class="{cls_of(r)}" points="{pts}"><title>{esc(r["name"])}'
            f' - lat {min(lats):.1f} to {max(lats):.1f}, lon {min(lons):.1f} to {max(lons):.1f}'
            f'{" - needs " + r["gate"] if r["gate"] else ""}</title></polygon>')
        shapes.append(f'<text x="{cx:.2f}" y="{cy:.2f}">{esc(r["name"])}</text>')
        rows.append((r["name"], key, r["gate"],
                     f"{min(lats):.1f} - {max(lats):.1f}",
                     f"{min(lons):.1f} - {max(lons):.1f}",
                     f"{cy:.1f}, {cx:.1f}",
                     f"{len(poly)} pts", cls_of(r)))

    tbody = "\n".join(
        f'<tr class="{c}"><td>{esc(n)}</td><td><code>{esc(k)}</code></td>'
        f'<td>{esc(g) or "-"}</td><td>{la}</td><td>{lo}</td><td>{ctr}</td><td>{extra}</td></tr>'
        for n, k, g, la, lo, ctr, extra, c in rows)

    gated = sum(1 for r in regions.values() if r["gate"])
    map_uri, map_name = island_map_data_uri(embed=not a.no_embed)
    if map_uri:
        map_img = f'<img src="{map_uri}" alt="">'
        map_note = (f"Background: your local <code>docs/{map_name}</code>, embedded so this page "
                    f"works on its own.")
    elif map_name == "link":
        map_img = ('<img src="island_map.png" alt="" '
                   "onerror=\"this.style.display='none'\">")
        map_note = ("Put your own copy of the Island map at <code>docs/island_map.png</code> to see "
                    "the terrain behind the loops. It is a Wildcard asset, so it is not included "
                    "here - only our own measured data is.")
    else:
        map_img = '<!-- no docs/island_map.* found; grid only -->'
        map_note = ("No <code>docs/island_map.png</code> found, so this is the plain grid. Drop "
                    "your own copy there and re-run to see the terrain.")
    html = f"""<title>ARK:ipelago - exploration areas</title>
<style>
 body{{margin:0;padding:20px;background:#12161c;color:#e8edf3;
      font:14px/1.55 ui-sans-serif,system-ui,"Segoe UI",sans-serif}}
 .wrap{{max-width:1180px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:26px 0 8px}}
 p{{color:#8b98a8;margin:0 0 14px}}
 .note{{border-left:3px solid #5bc0de;background:rgba(91,192,222,.08);padding:9px 13px;
       border-radius:0 6px 6px 0;color:#e8edf3;margin:0 0 16px}}
 .map{{position:relative;width:100%;aspect-ratio:1/1;border:1px solid #2a323d;border-radius:10px;
      overflow:hidden;background:#0d1b2a}}
 .map img,.map svg{{position:absolute;inset:0;width:100%;height:100%}}
 .map img{{object-fit:fill}}
 /* DARK fills, bright strokes. Light translucent fills washed out completely over the map
    image - the shapes were barely readable against sunlit terrain. Shading the region down
    instead makes every loop obvious and keeps the labels legible. */
 polygon{{fill:rgba(8,11,16,.52);stroke:#e4ebf2;stroke-width:.3;vector-effect:non-scaling-stroke}}
 polygon:hover{{fill:rgba(8,11,16,.2)}}
 polygon.fur{{fill:rgba(6,20,44,.55);stroke:#8dbcff}}
 polygon.scuba{{fill:rgba(3,30,29,.55);stroke:#5fe0ce}}
 text{{fill:#e8edf3;font:2.1px ui-sans-serif,sans-serif;text-anchor:middle;
       paint-order:stroke;stroke:#000;stroke-width:.55px;pointer-events:none}}
 .grid{{position:absolute;inset:0;pointer-events:none;
       background-image:linear-gradient(rgba(255,255,255,.07) 1px,transparent 1px),
                        linear-gradient(90deg,rgba(255,255,255,.07) 1px,transparent 1px);
       background-size:10% 10%}}
 .key span{{display:inline-block;margin-right:16px}}
 .sw{{display:inline-block;width:11px;height:11px;border:1px solid #e4ebf2;
      background:rgba(8,11,16,.6);vertical-align:-1px;margin-right:5px}}
 .sw.fur{{border-color:#8dbcff;background:rgba(6,20,44,.75)}}
 .sw.scuba{{border-color:#5fe0ce;background:rgba(3,30,29,.75)}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{text-align:left;padding:5px 9px;border-bottom:1px solid #232a33}}
 th{{color:#8b98a8;font-weight:600;position:sticky;top:0;background:#12161c}}
 tr.fur td:first-child{{border-left:3px solid #8dbcff}}
 tr.scuba td:first-child{{border-left:3px solid #5fe0ce}}
 tr.plain td:first-child{{border-left:3px solid #3a444f}}
 code{{background:rgba(255,255,255,.08);padding:1px 5px;border-radius:4px;
       font:12px ui-monospace,Consolas,monospace}}
 td:nth-child(4),td:nth-child(5),td:nth-child(6){{font:12px ui-monospace,Consolas,monospace}}
</style>
<div class="wrap">
<h1>Exploration areas - The Island</h1>
<p>{len(regions)} regions, {gated} of them gated. Coordinates are in-game GPS, so they match what
your compass shows. Hover a shape for its range.</p>
<div class="note">{esc(note)}</div>
<p class="key">
  <span><i class="sw"></i>no gear needed</span>
  <span><i class="sw fur"></i>needs Fur</span>
  <span><i class="sw scuba"></i>needs Scuba</span>
</p>
<div class="map">
  {map_img}
  <svg viewBox="0 0 100 100" preserveAspectRatio="none">
  {chr(10) + "  ".join(shapes)}
  </svg>
  <div class="grid"></div>
</div>
<p style="margin-top:10px">Grid lines are every 10 degrees. {map_note} The image is a Wildcard
asset, so neither it nor this page is committed.</p>

<h2>Ranges</h2>
<table>
<thead><tr><th>Region</th><th>/dumppos key</th><th>Gate</th><th>Lat</th><th>Lon</th>
<th>Centre (lat, lon)</th><th>Shape</th></tr></thead>
<tbody>
{tbody}
</tbody></table>
</div>
"""
    dst = os.path.join(ROOT, "docs", "exploration_overlay.html")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
