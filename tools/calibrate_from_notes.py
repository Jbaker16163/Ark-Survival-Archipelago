"""Derive a map's world-unit transform from explorer notes the player already collected.

ARK does not expose the transform: APrimalWorldSettings leaves LatitudeScale/Origin at zero unless
a map explicitly overrides them, and no SDK helper converts world coordinates to the compass
reading. So it has to be measured - but not by flying to corners and squinting at a HUD.

Explorer notes are ideal reference points. The wiki publishes each note's lat/lon
(tools/reference/note_coords.json), and the plugin records the player's world coordinate whenever a
note fires (ArkAP_note_positions.jsonl). Joining the two gives one calibration sample per note
collected, for free, with no extra work from anyone.

Many samples also beat two corners: this fits all of them at once and reports the spread, so a
single bad point shows up as a large residual instead of quietly skewing the answer.

    python tools/calibrate_from_notes.py ArkAP_note_positions.jsonl --map scorched

Pass --write to record the result in import_drawn_regions.KNOWN.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COORDS = os.path.join(ROOT, "tools", "reference", "note_coords.json")
TARGET = os.path.join(ROOT, "tools", "import_drawn_regions.py")


def fit(samples):
    """Least-squares (divisor, shift) over every (degree, world) pair, latitude and longitude
    pooled - both axes share one transform on every official map."""
    deg = [d for d, _ in samples]
    wld = [w for _, w in samples]
    n = len(deg)
    md, mw = sum(deg) / n, sum(wld) / n
    den = sum((d - md) ** 2 for d in deg)
    if den == 0:
        sys.exit("all samples are at the same coordinate")
    divisor = sum((d - md) * (w - mw) for d, w in zip(deg, wld)) / den
    shift = md - mw / divisor
    return divisor, shift


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("positions", help="ArkAP_note_positions.jsonl from the plugin folder")
    ap.add_argument("--map", required=True, help="map key the samples were taken on")
    ap.add_argument("--write", action="store_true", help="record it in import_drawn_regions.KNOWN")
    a = ap.parse_args()

    with open(COORDS, encoding="utf-8") as fh:
        coords = json.load(fh)["coords"].get(a.map, {})
    if not coords:
        sys.exit(f"no published note coordinates for map '{a.map}'")

    seen, samples, unknown = {}, [], []
    with open(a.positions, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                idx, x, y = int(r["note_index"]), float(r["x"]), float(r["y"])
            except (ValueError, KeyError):
                continue
            ll = coords.get(str(idx))
            if ll is None:
                unknown.append(idx)
                continue
            seen[idx] = (ll[0], ll[1], x, y)

    for idx, (lat, lon, x, y) in sorted(seen.items()):
        samples.append((lat, y))
        samples.append((lon, x))

    print(f"map            : {a.map}")
    print(f"notes usable   : {len(seen)}  (of {len(seen) + len(set(unknown))} positions logged)")
    if unknown:
        print(f"   no published coordinate for: {sorted(set(unknown))}")
    if len(seen) < 2:
        sys.exit("need at least 2 notes with known coordinates - collect a couple more, "
                 "as far apart as possible")

    divisor, shift = fit(samples)
    resid = [(d, w, (d - shift) * divisor - w) for d, w in samples]
    worst = max(abs(r) for _, _, r in resid)
    mean = sum(abs(r) for _, _, r in resid) / len(resid)
    print(f"\ndivisor        : {divisor:,.1f} world units per degree")
    print(f"shift          : {shift:.3f}")
    print(f"residual       : mean {mean:,.0f}   worst {worst:,.0f} world units "
          f"({worst / divisor:.2f} degrees)")

    print("\nper-note check (published lat/lon vs what this transform predicts):")
    for idx, (lat, lon, x, y) in sorted(seen.items())[:12]:
        print(f"   note {idx:5}  published {lat:6.1f},{lon:6.1f}   "
              f"from world {y / divisor + shift:6.1f},{x / divisor + shift:6.1f}")
    if len(seen) > 12:
        print(f"   ... and {len(seen) - 12} more")

    if worst / divisor > 3.0:
        print("\nWARNING: worst residual is over 3 degrees. Either a note's published coordinate is "
              "wrong, or the samples are clustered too tightly to fit reliably.")

    if not a.write:
        print("\nDRY RUN. Re-run with --write to record it.")
        return
    with open(TARGET, encoding="utf-8") as fh:
        src = fh.read()
    entry = f'"{a.map}": ({divisor:.1f}, {shift:.2f})'
    if f'"{a.map}"' in src.split("KNOWN = {")[1].split("}")[0]:
        src = re.sub(rf'"{a.map}":\s*\([^)]*\)', entry, src, count=1)
    else:
        src = src.replace("KNOWN = {", "KNOWN = {\n         "
                          f"# from {len(seen)} collected notes, worst residual "
                          f"{worst / divisor:.2f} deg\n         {entry},", 1)
    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(src)
    print(f"\nrecorded in {TARGET}")


if __name__ == "__main__":
    main()
