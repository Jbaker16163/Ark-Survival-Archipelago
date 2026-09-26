"""Emit paste-ready PopTracker chunks for one map, from data/ - so a new map is a script run.

PopTracker packs (Lurch's Arkipelago-Poptracker, and ours in poptracker/) pin every check to a
PIXEL on a map image. Our data stores GPS lat/lon (in note names) and world units (explore-region
polygons), so the only thing a human has to supply per map is the image and its calibration:
two points whose lat/lon AND pixel are both known.

    python tools/gen_poptracker_map.py fjordur --image images/maps/Fjordur.png
        --size 1024x900 --calib 0,0,192,-64 --calib 100,100,1088,832

--calib takes lat,lon,x,y. Two gridline crossings on the printed map are the easy pair: open the
image, read the pixel where 0/0 and 100/100 cross. lon->x and lat->y are fitted independently, so
a map whose axes have different scales (The Center, Scorched) is fine.

Writes nothing into the pack. Prints four blocks to paste in, and dumps the long ones to build/:
    1. maps/maps.json          - the map entry
    2. locations/<map>.json    - the location tree (runes/notes + explore regions)
    3. location_mapping.lua    - [id] = {{"@..."}} lines
    4. archipelago.lua         - EXPLORER_NOTE_IDS additions, for the _enabled visibility rules
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_drawn_regions import FJORDUR_REALMS, KNOWN                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# lat/lon is embedded in the display name, because that is where the harvest put it:
#   "Fjordur Rune #1 (Jotunheim 60.4/26.1)"   ->  qualifier Jotunheim, 60.4 lat, 26.1 lon
NAME_COORD = re.compile(r"\(([A-Za-z ]*?)\s*(-?\d+\.?\d*)[/-](-?\d+\.?\d*)\)\s*$")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def fit(samples):
    """(slope, intercept) for one axis from >=2 (degrees, pixel) pairs, least squares."""
    n = len(samples)
    md = sum(d for d, _ in samples) / n
    mp = sum(p for _, p in samples) / n
    den = sum((d - md) ** 2 for d, _ in samples)
    if den == 0:
        sys.exit("calibration points must differ on both axes")
    sl = sum((d - md) * (p - mp) for d, p in samples) / den
    return sl, mp - sl * md


def world_to_gps(map_key, realm, x, y):
    """World units -> (lat, lon). Fjordur realms each carry their own transform."""
    if map_key == "fjordur" and realm in FJORDUR_REALMS:
        lond, lons, latd, lats = FJORDUR_REALMS[realm]["xform"]
        return y / latd + lats, x / lond + lons
    t = KNOWN.get(map_key)
    if not t:
        sys.exit("no world transform known for %r - derive it with tools/import_drawn_regions.py "
                 "--calib and add it to KNOWN there first" % map_key)
    if len(t) == 2:
        div, shift = t
        return y / div + shift, x / div + shift
    lond, lons, latd, lats = t
    return y / latd + lats, x / lond + lons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map", help="map key as it appears in data/maps.json, e.g. fjordur")
    ap.add_argument("--pop-name", help="map name inside the pack (default: Title_Case of map key)")
    ap.add_argument("--image", required=True, help="image path as the pack will reference it")
    ap.add_argument("--size", required=True, help="image size WxH, e.g. 1024x900")
    ap.add_argument("--calib", action="append", required=True, metavar="lat,lon,x,y",
                    help="a point whose lat/lon and pixel are both known; pass at least twice")
    ap.add_argument("--group", default=None, help="location tree root (default: the pack map name)")
    ap.add_argument("--dot", type=int, default=10, help="location_size")
    args = ap.parse_args()

    pop = args.pop_name or "_".join(w.capitalize() for w in args.map.split("_"))
    group = args.group or pop.replace("_", " ")
    w, h = (int(v) for v in args.size.lower().split("x"))

    pts = []
    for c in args.calib:
        f = [float(v) for v in c.split(",")]
        if len(f) != 4:
            sys.exit("--calib wants lat,lon,x,y - got %r" % c)
        pts.append(f)
    if len(pts) < 2:
        sys.exit("pass --calib at least twice")
    xs, xi = fit([(p[1], p[2]) for p in pts])
    ys, yi = fit([(p[0], p[3]) for p in pts])
    print("-- calibration: x = %.1f + %.3f*lon    y = %.1f + %.3f*lat" % (xi, xs, yi, ys))

    def px(lat, lon):
        return round(xi + xs * lon), round(yi + ys * lat)

    children, mapping, note_ids, off_image = [], [], [], 0

    # ---- notes and runes: coordinates come out of the display name -------------------------
    notes = load("locations.json")["location_categories"]["dossiers"]["entries"]
    keep = set(load("maps.json").get("content", {}).get(args.map, {}).get("locations") or [])
    for e in notes:
        if keep and e["id"] not in keep:
            continue
        m = NAME_COORD.search(e["name"])
        if not m:
            continue
        qual, lat, lon = m.group(1).strip(), float(m.group(2)), float(m.group(3))
        x, y = px(lat, lon)
        if not (0 <= x <= w and 0 <= y <= h):
            off_image += 1
        base = e["name"].split(" (")[0]
        slug = re.sub(r"[^a-z0-9]+", "_", e["name"].lower()).strip("_")
        sec = "%s: %s-%s" % (qual or group, lat, lon)
        children.append({
            "name": base,
            "chest_unopened_img": "images/items/chest_gold_x.png",
            "chest_opened_img": "images/items/chest_o.png",
            "map_locations": [{"map": pop, "x": x, "y": y}],
            "sections": [{"name": sec,
                          "visibility_rules": ["$explorer_note_%s_enabled" % slug],
                          "item_count": 1}],
        })
        mapping.append('[%d] = {{"@%s/%s/%s"}},' % (e["id"], group, base, sec))
        note_ids.append("    %-52s = %d," % (slug, e["id"]))

    # ---- explore regions: polygon centroid, world -> gps -> pixel ---------------------------
    for r in load("explore_areas.json")["regions"].values():
        if r.get("map") != args.map or not r.get("polygon"):
            continue
        poly = r["polygon"]
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        lat, lon = world_to_gps(args.map, r.get("realm"), cx, cy)
        x, y = px(lat, lon)
        if not (0 <= x <= w and 0 <= y <= h):
            off_image += 1
        sec = "Explore: %.1f-%.1f" % (lat, lon)
        children.append({
            "name": "Explore - %s" % r["name"],
            "chest_unopened_img": "images/items/chest_gold_x.png",
            "chest_opened_img": "images/items/chest_o.png",
            "map_locations": [{"map": pop, "x": x, "y": y}],
            "sections": [{"name": sec, "item_count": 1}],
        })
        mapping.append('[%d] = {{"@%s/Explore - %s/%s"}},' % (r["id"], group, r["name"], sec))

    out = os.path.join(ROOT, "build")
    os.makedirs(out, exist_ok=True)
    tree = [{"name": group, "color": "#FFFFFF", "children": children}]
    files = {"poptracker_%s.json" % args.map: json.dumps(tree, indent=2),
             "poptracker_%s_mapping.lua" % args.map: "\n".join(mapping),
             "poptracker_%s_noteids.lua" % args.map: "\n".join(note_ids)}
    for n, body in files.items():
        with open(os.path.join(out, n), "w", encoding="utf-8") as fh:
            fh.write(body + "\n")

    if off_image:
        print("-- WARNING: %d pins land outside the %dx%d image. Recheck --calib."
              % (off_image, w, h))

    print("\n===== 1. maps/maps.json - append this entry =====")
    print(json.dumps({"name": pop, "location_size": args.dot,
                      "location_border_thickness": max(2, args.dot // 4),
                      "img": args.image}, indent=2))
    print("\n===== 2. locations/%s.json - new file, %d locations. Add a matching"
          " Tracker:AddLocations line to scripts/init.lua =====" % (args.map, len(children)))
    print("   build/poptracker_%s.json" % args.map)
    print("\n===== 3. scripts/autotracking/location_mapping.lua - %d lines =====" % len(mapping))
    print("   build/poptracker_%s_mapping.lua" % args.map)
    for line in mapping[:4]:
        print("   " + line)
    print("\n===== 4. archipelago.lua EXPLORER_NOTE_IDS - %d lines =====" % len(note_ids))
    print("   build/poptracker_%s_noteids.lua" % args.map)
    for line in note_ids[:4]:
        print("   " + line.strip())


if __name__ == "__main__":
    main()
