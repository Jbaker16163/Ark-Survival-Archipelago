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

# Known world-unit transforms. Each value is either a 2-tuple (divisor, shift) used for BOTH axes,
# or a 4-tuple (lon_div, lon_shift, lat_div, lat_shift) for a map whose latitude and longitude do
# not share one transform. "island" is verified against the three obelisks to within 0.1 deg.
# Only add a map here once it has been derived from real samples - see --calib.
KNOWN = {"island": (8000.0, 50.0),
         # from /dumppos at both true map corners, 2026-08-06
         "ragnarok": (13009.4, 49.99),
         # fitted from 9 collected notes as 7998.2/50.013, then snapped: the round
         # pair is equally accurate (0.076 vs 0.077 deg mean error, both at the
         # 0.1-deg rounding floor of the published coordinates) and matches the
         # Island, which is the same physical size.
         "scorched": (8000.0, 50.0),
         # PER-AXIS: The Center has different lat/lon origins (it is not a square-cornered map).
         # From APrimalWorldSettings lat_origin=-290800 scale=9584, lon_origin=-529000 scale=9600
         # (the plugin's ArkAP_map_geo.json reported the scales 10x low - 958.4/960 - so they were
         # x10'd here), verified against 4 /dumppos+compass samples to within 0.1 deg.
         #        (lon_div, lon_shift,        lat_div, lat_shift)
         "center": (9600.0, 529000 / 9600, 9584.0, 290800 / 9584),
         # Valguero is symmetric (lat==lon): origin -408050, scale 8161 both axes (geo file reported
         # 816.1, again 10x low), shift = 408050/8161 = 50.0 exactly. Verified against 4
         # /dumppos+compass samples to within ~0.1 deg.
         "valguero": (8161.0, 50.0),
         # Fjordur is ONE world with four ALTITUDE-STACKED realms (Midgard/Asgard/Jotunheim/
         # Vanaheim) sharing one global X/Y transform - the compass is X/Y only, so it reads the
         # same lat/lon in a realm as in the Midgard spot directly above. Derived from Midgard
         # /dumppos+compass corners (the geo file was 10x low, the Valguero bug again); the map's
         # own toprightfjordur corner read ~2 deg off - a GPS edge clamp - so it was dropped, and
         # the fit is coarser than the others (~1.4 deg worst residual). Fine for biome polygons.
         # Realms are told apart by their Z band, applied automatically per-realm (FJORDUR_REALMS).
         "fjordur": (7265.7, 50.592)}

# Fjordur realms: each region's realm decides its own image->world transform, its Z band (world
# units) and how its key/name are namespaced. The realms are NOT one GPS system - each realm's
# in-game map is edge-to-edge and has its OWN scale (Midgard ~7300 world units/deg, Asgard ~3630),
# so a single global transform does not fit (a 16-point global fit missed by ~6 deg). Each realm
# therefore carries a 4-tuple `xform` = (lon_div, lon_shift, lat_div, lat_shift) mapping the region
# drawer's image fraction (0-100) straight to world, derived from that realm's four map corners
# (imgLat 0=top..100=bottom, imgLon 0=left..100=right) /dumppos'd in-game (residual 0-5 world units).
# Z band (world units): realms share X/Y so the band keeps a Midgard polygon from firing for a
# player at the same X/Y down in a realm; bands are kept apart for realms that overlap in X/Y
# (Asgard vs Jotunheim), reused where they are X/Y-separated (Vanaheim reuses Jotunheim's safely).
# A None edge = open on that side. Measured Z: Midgard +10k..+45k, Jotunheim -171k..-103k,
# Asgard -298k..-275k, Vanaheim -176k..-126k (verified 2026-09-07).
# xform None = not calibrated yet: import refuses until its four map corners are /dumppos'd.
FJORDUR_REALMS = {
    "midgard":   {"name": "Midgard",   "z": (-90000, None),     "xform": (7305.3, 50.984, 7233.8, 50.469)},
    "jotunheim": {"name": "Jotunheim", "z": (-220000, -92000),  "xform": (2404.6, 72.230, 2822.7, -11.890)},
    "asgard":    {"name": "Asgard",    "z": (-360000, -240000), "xform": (3632.1, 87.887, 3647.8, 63.357)},
    "vanaheim":  {"name": "Vanaheim",  "z": (-220000, -92000),  "xform": (2706.0, -45.352, 2917.4, 141.657)},
}


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


def _fit(pairs):
    """(divisor, shift, worst residual) for one axis: world = (deg - shift) * divisor."""
    n = len(pairs)
    md = sum(d for d, _ in pairs) / n
    mw = sum(w for _, w in pairs) / n
    den = sum((d - md) ** 2 for d, _ in pairs)
    if den == 0:
        sys.exit("calibration samples are all at the same coordinate - spread them out")
    div = sum((d - md) * (w - mw) for d, w in pairs) / den
    shift = md - mw / div
    resid = max(abs(w - (d - shift) * div) for d, w in pairs)
    return div, shift, resid


def derive_axes(samples):
    """Per-axis (lon_div, lon_shift, lat_div, lat_shift) + worst residual, fitting lon~x and lat~y
    SEPARATELY. Needed for maps like The Center whose lat and lon do not share a transform."""
    lon_div, lon_shift, r_lon = _fit([(lon, x) for _lat, lon, x, _y in samples])
    lat_div, lat_shift, r_lat = _fit([(lat, y) for lat, _lon, _x, y in samples])
    return (lon_div, lon_shift, lat_div, lat_shift), max(r_lon, r_lat)


def as_axes(t):
    """Normalise a KNOWN entry to (lon_div, lon_shift, lat_div, lat_shift). A 2-tuple means both
    axes share one transform."""
    return tuple(t) if len(t) == 4 else (t[0], t[1], t[0], t[1])


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
    ap.add_argument("--zband", help="zmin,zmax world-Z band for an altitude-stacked realm (Fjordur "
                                    "Midgard/Asgard/Jotunheim/Vanaheim share X/Y, differ in Z). "
                                    "Leave a side blank for open, e.g. '-90000,' = at or above. "
                                    "Stamped onto every region in this import so a Midgard polygon "
                                    "never fires for a player at the same X/Y down in a realm.")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    with open(a.drawn, encoding="utf-8") as fh:
        drawn = json.load(fh)
    map_key = drawn.get("map", "").strip()
    if not map_key:
        sys.exit("the drawn file has no 'map' key")

    zband = None
    if a.zband:
        parts = a.zband.split(",")
        if len(parts) != 2:
            sys.exit("--zband needs 'zmin,zmax' (either side may be blank for open)")
        zband = (float(parts[0]) if parts[0].strip() else None,
                 float(parts[1]) if parts[1].strip() else None)

    # A realm (Fjordur only) namespaces keys/names and auto-selects the Z band, so the drawer's
    # per-realm export just works with no flags. An explicit --zband still overrides.
    realm = drawn.get("realm", "").strip().lower()
    if realm:
        if map_key != "fjordur" or realm not in FJORDUR_REALMS:
            sys.exit(f"unknown realm {realm!r} for map {map_key!r} (Fjordur realms: "
                     f"{', '.join(FJORDUR_REALMS)})")
        if zband is None:
            zband = FJORDUR_REALMS[realm]["z"]

    maps_json = load("maps.json")
    if map_key not in {m["key"] for m in maps_json["maps"]}:
        sys.exit(f"unknown map key '{map_key}' - not on the Maps sheet")

    # transform is per-axis internally: (lon_div, lon_shift, lat_div, lat_shift)
    if realm and not (a.geo or a.transform or a.calib):
        xf = FJORDUR_REALMS[realm]["xform"]
        if xf is None:
            sys.exit(f"realm {realm!r} is not calibrated yet. Stand at its map's four visual "
                     f"corners (top-left, top-right, bottom-left, bottom-right) and /dumppos each, "
                     f"then pass them as --calib imgLat,imgLon,x,y (imgLat 0=top/100=bottom, "
                     f"imgLon 0=left/100=right), e.g. --calib 0,0,<x>,<y> --calib 100,100,<x>,<y>.")
        transform = as_axes(xf)
        print(f"transform      : lon div={transform[0]:.1f} shift={transform[1]:.2f} | "
              f"lat div={transform[2]:.1f} shift={transform[3]:.2f}  "
              f"(known for Fjordur/{FJORDUR_REALMS[realm]['name']})")
    elif a.geo:
        with open(a.geo, encoding="utf-8") as fh:
            g = json.load(fh)
        if g.get("map") and g["map"] != map_key:
            sys.exit(f"that geo file is for map '{g['map']}' but the drawing is for '{map_key}'")
        # ARK keeps latitude and longitude constants separately (origin + scale each). Convert both
        # axes - they may differ (The Center does). x = (lon - shift)*div where div = scale and
        # shift = -origin/scale, per axis.
        lat_s, lat_o = float(g["lat_scale"]), float(g["lat_origin"])
        lon_s, lon_o = float(g["lon_scale"]), float(g["lon_origin"])
        transform = (lon_s, -lon_o / lon_s, lat_s, -lat_o / lat_s)
        print(f"transform      : lon div={lon_s:.1f} shift={-lon_o / lon_s:.2f} | "
              f"lat div={lat_s:.1f} shift={-lat_o / lat_s:.2f}  (from APrimalWorldSettings)")
    elif a.transform:
        vals = [float(v) for v in a.transform.split(",")]
        if len(vals) not in (2, 4):
            sys.exit("--transform needs 'div,shift' or 'lon_div,lon_shift,lat_div,lat_shift'")
        transform = as_axes(vals)
        print(f"transform      : {transform}  (given)")
    elif a.calib:
        samples = []
        for c in a.calib:
            parts = [float(v) for v in c.split(",")]
            if len(parts) != 4:
                sys.exit(f"--calib needs lat,lon,x,y - got {c!r}")
            samples.append(parts)
        if len(samples) < 2:
            sys.exit("need at least two --calib samples")
        transform, resid = derive_axes(samples)
        print(f"transform      : lon div={transform[0]:.1f} shift={transform[1]:.2f} | "
              f"lat div={transform[2]:.1f} shift={transform[3]:.2f}  "
              f"(derived from {len(samples)} samples, worst residual {resid:,.0f} world units)")
        if resid > 20000:
            print("   WARNING: that residual is large (>20k units, ~2.5 degrees). Check the "
                  "compass readings you paired with each /dumppos sample.")
    elif map_key in KNOWN:
        transform = as_axes(KNOWN[map_key])
        print(f"transform      : lon div={transform[0]:.1f} shift={transform[1]:.2f} | "
              f"lat div={transform[2]:.1f} shift={transform[3]:.2f}  (known for {map_key})")
    else:
        sys.exit(f"no transform for '{map_key}'. Pass --transform, or two --calib samples taken "
                 f"with /dumppos. Guessing would shift every region on the map.")

    lon_div, lon_shift, lat_div, lat_shift = transform

    def to_world(lat, lon):
        return [int(round((lon - lon_shift) * lon_div)), int(round((lat - lat_shift) * lat_div))]

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

    realm_name = FJORDUR_REALMS[realm]["name"] if realm else ""

    added, skipped = [], []
    for key, r in drawn.get("regions", {}).items():
        raw_name = r.get("name", key)
        # island keeps the bare key/name it already ships; everything else is qualified, so this
        # stays idempotent - a re-run computes the same key and skips it as already present. A realm
        # adds its own segment so the four Fjordur realms never collide on a shared place name.
        if realm:
            out_key = f"{map_key}_{realm}_{key}"
            out_name = f"{raw_name} ({realm_name})"
        else:
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
        if realm:
            rec["realm"] = realm            # lets the drawer show only this realm on re-edit
        if len(parts) > 1:
            rec["polygons"] = parts
        if zband is not None:
            if zband[0] is not None:
                rec["z_min"] = zband[0]
            if zband[1] is not None:
                rec["z_max"] = zband[1]
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
