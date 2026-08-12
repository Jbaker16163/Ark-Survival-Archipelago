"""Turn regions drawn in tools/region_drawer.html into data/explore_areas.json entries.

The drawer works in ARK GPS (0-100 across any map), because that is what you can actually see and
click. The plugin tests membership in WORLD coordinates, so the conversion happens here:

    x = (lon - shift) * divisor        y = (lat - shift) * divisor

`divisor` (world units per degree) and `shift` differ per map, and getting them wrong silently
shifts every region - the checks still fire, just in the wrong places. So they are never guessed:
either pass a known pair with --transform, or derive them from real in-game samples with --calib,
which is what /dumppos is for.

Deriving from samples needs only TWO points, as far apart as possible:

    /dumppos corner_nw      (stand there, note the lat/lon your compass shows)
    /dumppos corner_se

then pass each as lat,lon,x,y:

    python tools/import_drawn_regions.py regions.json \\
        --calib 10.2,12.5,-310000,-318000 --calib 88.0,91.3,330000,304000

Ids are positional in registry order (8758000 + n), like the existing regions, so new maps append
after the Island's 45 and nothing already shipped moves.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checklist_schema as S                                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIRS = [os.path.join(ROOT, "data"), os.path.join(ROOT, "apworld", "ark_ase", "data")]

# Known world-unit transforms. "island" is verified against the three obelisks to within 0.1 deg.
# Only add a map here once it has been derived from real samples - see --calib.
KNOWN = {"island": (8000.0, 50.0),
         # from /dumppos at both true map corners, 2026-08-06
         "ragnarok": (13009.4, 49.99),
         # fitted from 9 collected notes as 7998.2/50.013, then snapped: the round
         # pair is equally accurate (0.076 vs 0.077 deg mean error, both at the
         # 0.1-deg rounding floor of the published coordinates) and matches the
         # Island, which is the same physical size.
         "scorched": (8000.0, 50.0)}


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


def _bbox_area(poly):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def derive(samples):
    """(divisor, shift) from >=2 (lat, lon, x, y) samples, least-squares over both axes.

    Both axes share one divisor and one shift in every ARK map we have seen, so lat/y and lon/x are
    pooled - which also means a bad sample shows up as a large residual rather than quietly
    skewing one axis."""
    deg, world = [], []
    for lat, lon, x, y in samples:
        deg += [lat, lon]
        world += [y, x]
    n = len(deg)
    mean_d, mean_w = sum(deg) / n, sum(world) / n
    num = sum((d - mean_d) * (w - mean_w) for d, w in zip(deg, world))
    den = sum((d - mean_d) ** 2 for d in deg)
    if den == 0:
        sys.exit("calibration samples are all at the same coordinate - spread them out")
    divisor = num / den                       # world units per degree
    shift = mean_d - mean_w / divisor
    resid = max(abs(w - (d - shift) * divisor) for d, w in zip(deg, world))
    return divisor, shift, resid


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drawn", help="regions.json exported by tools/region_drawer.html")
    ap.add_argument("--geo", help="ArkAP_map_geo.json written by /dumppos. This is the map's OWN "
                                  "lat/lon constants read out of APrimalWorldSettings, so it is "
                                  "exact - no corners, no compass readings, no derivation.")
    ap.add_argument("--transform", help="divisor,shift - skips derivation (e.g. 8000,50)")
    ap.add_argument("--calib", action="append", default=[],
                    help="lat,lon,x,y sample from /dumppos plus the compass reading. Repeatable; "
                         "two well-separated points are enough.")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    with open(a.drawn, encoding="utf-8") as fh:
        drawn = json.load(fh)
    map_key = drawn.get("map", "").strip()
    if not map_key:
        sys.exit("the drawn file has no 'map' key")

    maps_json = load("maps.json")
    if map_key not in {m["key"] for m in maps_json["maps"]}:
        sys.exit(f"unknown map key '{map_key}' - not on the Maps sheet")

    if a.geo:
        with open(a.geo, encoding="utf-8") as fh:
            g = json.load(fh)
        if g.get("map") and g["map"] != map_key:
            sys.exit(f"that geo file is for map '{g['map']}' but the drawing is for '{map_key}'")
        divisor, shift = float(g["divisor"]), float(g["shift"])
        # ARK keeps latitude and longitude constants separately. Every official map uses the same
        # pair for both, but check rather than assume - a map that did not would need the polygon
        # conversion split per axis.
        lat_d, lon_d = float(g["lat_scale"]), float(g["lon_scale"])
        if abs(lat_d - lon_d) > 1.0:
            sys.exit(f"this map scales latitude and longitude differently "
                     f"(lat {lat_d}, lon {lon_d}) - the converter assumes one divisor for both")
        print(f"transform      : divisor={divisor:.1f} shift={shift:.2f}  "
              f"(read from the game's own APrimalWorldSettings)")
    elif a.transform:
        divisor, shift = (float(v) for v in a.transform.split(","))
        print(f"transform      : divisor={divisor:.1f} shift={shift:.2f}  (given)")
    elif a.calib:
        samples = []
        for c in a.calib:
            parts = [float(v) for v in c.split(",")]
            if len(parts) != 4:
                sys.exit(f"--calib needs lat,lon,x,y - got {c!r}")
            samples.append(parts)
        if len(samples) < 2:
            sys.exit("need at least two --calib samples")
        divisor, shift, resid = derive(samples)
        print(f"transform      : divisor={divisor:.1f} shift={shift:.2f}  "
              f"(derived from {len(samples)} samples, worst residual {resid:,.0f} world units)")
        if resid > 20000:
            print("   WARNING: that residual is large (>20k units, ~2.5 degrees). Check the "
                  "compass readings you paired with each /dumppos sample.")
    elif map_key in KNOWN:
        divisor, shift = KNOWN[map_key]
        print(f"transform      : divisor={divisor:.1f} shift={shift:.2f}  (known for {map_key})")
    else:
        sys.exit(f"no transform for '{map_key}'. Pass --transform, or two --calib samples taken "
                 f"with /dumppos. Guessing would shift every region on the map.")

    def to_world(lat, lon):
        return [int(round((lon - shift) * divisor)), int(round((lat - shift) * divisor))]

    ex = load("explore_areas.json")
    regions = ex["regions"]
    base = ex.get("_id_base", S.ID_BLOCKS["explore"][0])
    lo, hi = S.ID_BLOCKS["explore"]
    next_id = max((r["id"] for r in regions.values()), default=base - 1) + 1

    # KEYS AND NAMES ARE BOTH GLOBAL, AND MAPS REUSE PLACE NAMES. Every map has a Green, a Red and
    # a Blue Obelisk. The key collision merely dropped them (`key in regions` -> skipped, silently,
    # with no error - Scorched's three obelisks nearly shipped missing). The NAME collision is far
    # worse: the apworld builds locations as `"Explore: " + name` straight into the class-level
    # location_name_to_id, so a duplicate name overwrites the Island's id in the shared datapackage
    # for every player in the multiworld. Namespace both, the same way the notes are suffixed.
    display = next((m.get("display", map_key) for m in maps_json["maps"]
                    if m["key"] == map_key), map_key)
    by_name = {r["name"]: k for k, r in regions.items()}

    added, skipped = [], []
    for key, r in drawn.get("regions", {}).items():
        raw_name = r.get("name", key)
        # island keeps the bare key/name it already ships; everything else is qualified, so this
        # stays idempotent - a re-run computes the same key and skips it as already present.
        out_key = key if map_key == "island" else f"{map_key}_{key}"
        out_name = raw_name if map_key == "island" else f"{raw_name} ({display})"
        if out_key in regions:
            skipped.append(f"{out_key} (already imported)")
            continue
        if out_name in by_name:
            sys.exit(f"region name {out_name!r} is already used by '{by_name[out_name]}'. Two "
                     f"locations cannot share a name - the datapackage is keyed on it.")
        # A region may be several disjoint shapes ("latlon_parts"); the drawer emits one loop
        # ("latlon"). Both end up as a list of parts.
        raw_parts = r.get("latlon_parts") or ([r["latlon"]] if r.get("latlon") else [])
        parts = [[to_world(lat, lon) for lat, lon in p] for p in raw_parts if len(p) >= 3]
        if not parts:
            skipped.append(f"{out_key} (no shape with 3+ points)")
            continue
        if next_id > hi:
            sys.exit(f"explore id block {lo}-{hi} exhausted")
        by_name[out_name] = out_key
        rec = {"id": next_id, "name": out_name, "gate": r.get("gate", ""), "map": map_key,
               # `polygon` stays the single-shape form so every existing reader keeps working; for
               # a multi-part region it holds the LARGEST part and `polygons` holds them all.
               "polygon": max(parts, key=lambda p: len(p)) if len(parts) == 1 else
                          max(parts, key=_bbox_area)}
        if len(parts) > 1:
            rec["polygons"] = parts
        added.append((out_key, key, rec))
        next_id += 1

    print(f"map            : {map_key}")
    print(f"TO ADD         : {len(added)}")
    for out_key, src_key, r in added:
        src = drawn["regions"][src_key]
        allpts = [q for p in (src.get("latlon_parts") or [src.get("latlon") or []]) for q in p]
        lats = [lat for lat, _ in allpts]
        lons = [lon for _, lon in allpts]
        nparts = len(r.get("polygons") or [1])
        print(f"     {r['id']}  {r['name']:34} {nparts:3} part(s)  "
              f"lat {min(lats):.1f}-{max(lats):.1f} lon {min(lons):.1f}-{max(lons):.1f}"
              f"{'  needs ' + r['gate'] if r['gate'] else ''}")
    if skipped:
        print(f"skipped        : {skipped}")

    if not a.write:
        print("\nDRY RUN. Re-run with --write to apply.")
        return
    if not added:
        print("\nnothing to add")
        return

    for out_key, _src_key, r in added:
        regions[out_key] = r
    save("explore_areas.json", ex)

    content = maps_json["content"]
    b = content.setdefault(map_key, {"items": [], "locations": []})
    b["locations"] = sorted(set(b["locations"]) | {r["id"] for _, _, r in added})
    save("maps.json", maps_json)
    print(f"\n{len(added)} region(s) added; explore_areas.json now has {len(regions)}")
    print("Re-run tools/build_release.py so the plugin ships them.")


if __name__ == "__main__":
    main()
