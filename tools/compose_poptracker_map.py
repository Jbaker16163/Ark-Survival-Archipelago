"""Build a PopTracker map image: sidebar artwork + the map + a boss strip.

The pack's map "images" are composites, not maps. `The_Island.png` is a 1024x900 canvas with a
column of creature and resource artwork down the left, the map itself on the right, and boss
portraits along the bottom. Tames, kills and milestones have no place on a real map, so their
pins sit on that artwork instead. Hand a bare map in and those pins land in the middle of the
terrain with nothing behind them, which is exactly what a new map looks like before this step.

    python tools/compose_poptracker_map.py ragnarok --source docs/ragnarok_map.jpg \
        --pack build/pack_ragnarok --out build/pack_ragnarok/images/maps/Ragnarok.png

Reads the slot manifest `patch_poptracker_pack.py` writes (build/poptracker_<map>_slots.json), so
every icon is painted at the exact pixel its pin was assigned - they cannot drift apart. Icons
come from the pack's own images/ directories; anything with no icon gets a readable text chip
rather than being silently left blank.

The geometry printed at the end is the --calib pair to re-run the patcher with, because moving the
map into the canvas changes where a lat/lon lands.
"""
import argparse
import json
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Taller than the pack's 1024x900: Ragnarok carries three more sidebar rows than the Island
# (the Scorched-only creatures, the extra resources, the artifacts). The map box is unchanged, so
# the calibration is too.
CANVAS = (1024, 1024)
MAP_BOX = (274, 0, 1024, 750)        # where the map itself goes: left, top, right, bottom
TILE = 32                            # icon size, matching the 33.2px pin grid
# Crop narrower than a full tile. The pack's rightmost sidebar column sits at x=257 and its map
# starts at x=265, so a full 32px crop centred there scoops 8px of ocean in with the artwork.
# The source art tile is NOT centred on its pin. Measured off The_Island's sidebar seams
# (x = 0, 30, 64, 98, 129, 163, 196, 230, 262; same 33px step down the rows), the tile runs from
# pin-27 to pin+6, so its centre sits 11px up and left of the pin. Cropping centred on the pin
# instead takes two-thirds of the right tile plus a slice of its neighbour, which looks like the
# artwork has been chopped up. The rightmost column lands exactly on the map edge (257+6 = 263),
# so a correctly placed crop also never scoops ocean.
CROP = 33
ART_LEAD = 27
BOSS_TILE = 110                      # boss portraits; a pair is two of these side by side

# Display names the pack's filenames spell differently. Every entry is a rename, never a guess at
# a different creature - an unmatched name gets a text chip instead.
ALIAS = {
    "carno": "carnotaurus", "spino": "spinosaurus", "stego": "stegosaurus",
    "mosasaur": "mosasaurus", "therizinosaur": "therizinosaurus", "raptor": "raptor",
    "trike": "triceratops", "rex": "rex", "bronto": "brontosaurus", "pachy": "pachy",
    "dire bear": "direbear", "dire wolf": "direwolf", "spirit direwolf": "direwolf",
    "spirit dire bear": "direbear", "iceworm queen": "deathworm",
    "lava elemental": "rockelemental", "rock elemental": "rockelemental",
    "thorny dragon": "thornydragon", "dragon": "dragon_trophy", "manticore": "manticore",
    "griffin": "griffin", "camelsaurus": "morellatops", "jerboa": "jerboa",
    "spineylizard": "thornydragon", "moth": "lymantria", "lymantria": "lymantria",
    "plant species x seed": "plant_species_x_seed", "angler gel": "angler_gel",
    "bio toxin": "bio_toxin",
    # verified against the pack's actual filenames
    "spino": "spinosaur", "spinosaurus": "spinosaur",
    "alpha fire wyvern": "wyvern_fire_alpha", "wyvern": "wyvern_fire",
    "alpha deathworm": "deathworm_alpha",
    "devious": "artifact_devious",
}


BORROW_ART = {"Explore Regions": "Exploration (The Island)"}

# Full-size boss portraits the pack paints under its own maps: (canvas, box to search). The box
# is loose on purpose - fit() trims it to the artwork - and was read off the 0.0.8 canvases. A boss
# with no portrait anywhere in the pack falls back to its creature icon, scaled up.
BOSS_PORTRAITS = {
    "Dragon": ("The_Island", (612, 771, 770, 857)),      # below the map edge
    # NOT the Scorched canvas: there the map edge clips its wing and the artifact labels overlap
    # it, so any crop is lopsided. The pack ships the whole creature as a transparent icon.
    "Manticore": ("file", "dinos/manticore.png"),
}


def portrait(pack, name, cache):
    spec = BOSS_PORTRAITS.get(name)
    if not spec:
        return None
    if spec[0] == "file":
        path = os.path.join(pack, "images", spec[1])
        return Image.open(path).convert("RGBA") if os.path.exists(path) else None
    path = os.path.join(pack, "images", "maps", "%s.png" % spec[0])
    if not os.path.exists(path):
        return None
    if path not in cache:
        cache[path] = Image.open(path).convert("RGB")
    return cache[path].crop(spec[1]).convert("RGBA")


def trim(im):
    """Make a crop's black canvas background transparent (art that already has transparency is
    left alone - its dark outlines are part of the drawing) and cut it to the artwork."""
    im = im.convert("RGBA")
    if im.getextrema()[3][0] == 255:
        px = im.load()
        for yy in range(im.height):
            for xx in range(im.width):
                r, g, b, a = px[xx, yy]
                if r + g + b < 40:
                    px[xx, yy] = (0, 0, 0, 0)
    box = im.getbbox()
    return im.crop(box) if box else im


def fit(im, size):
    """Trim, then scale to fit a size box."""
    im = trim(im)
    scale = min(size / im.width, size / im.height)
    return im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                     Image.LANCZOS)


def index_existing_art(pack, skip):
    """name -> (canvas path, x, y) for every object already pinned on another map.

    Most of this map's objects are shared with maps the pack already carries, and their artwork is
    baked into THOSE canvases at exactly their pin. Cropping it from there is exact - no filename
    guessing - and it keeps the new map looking like the rest of the pack. It is also the only
    source for the milestone drop icons (Argentavis Talon, Allosaurus Brain and the rest), which
    the pack ships nowhere as standalone files.
    """
    out = {}
    locdir = os.path.join(pack, "locations")
    for fn in sorted(os.listdir(locdir)):
        if not fn.endswith(".json"):
            continue
        def w(node):
            if isinstance(node, list):
                for c in node:
                    w(c)
                return
            if not isinstance(node, dict):
                return
            for ml in node.get("map_locations") or []:
                # never the map being built: a previous run may have left a canvas there, and
                # cropping from it would just recycle whatever it already had
                if ml.get("map") == skip:
                    continue
                canvas = os.path.join(pack, "images", "maps", "%s.png" % ml.get("map"))
                if node.get("name") and os.path.exists(canvas):
                    out.setdefault(node["name"], (canvas, ml["x"], ml["y"]))
            for c in node.get("children") or []:
                w(c)
        w(json.load(open(os.path.join(locdir, fn), encoding="utf-8")))
    return out


def index_images(pack):
    """Every icon the pack ships, keyed by its squashed filename."""
    pool = {}
    for sub in ("dinos", "engrams", "crates", "saddles", "settings"):
        d = os.path.join(pack, "images", sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".png"):
                pool.setdefault(re.sub(r"[^a-z0-9]", "", f[:-4].lower()), os.path.join(d, f))
    return pool


def resolve(name, pool):
    """Icon path for a location's display name, or None to fall back to a text chip."""
    n = name
    for pre in ("Tame: ", "Kill: ", "Killed: ", "Boss: ", "Artifact: ", "Dossier: "):
        if n.startswith(pre):
            n = n[len(pre):]
            break
    n = re.sub(r"\s*\((Gamma|Beta|Alpha)\)\s*$", "", n).strip()
    # a paired entry ("Dragon & Manticore") has no art of its own; use the first member's
    first = n.split(" & ")[0].strip() if " & " in n else ""
    tries = [n, ALIAS.get(n.lower(), ""), first, ALIAS.get(first.lower(), ""),
             n.replace("Alpha ", ""),
             ALIAS.get(n.lower().replace("alpha ", ""), ""),
             "Artifact of the " + n, n + " Trophy", n.replace(" ", "")]
    for t in tries:
        if not t:
            continue
        k = re.sub(r"[^a-z0-9]", "", t.lower())
        if k in pool:
            return pool[k]
    return None


def chip(draw, x, y, size, text):
    """Readable placeholder for a location whose art the pack does not ship."""
    draw.rectangle([x, y, x + size, y + size], fill=(38, 38, 44), outline=(105, 105, 120))
    try:
        font = ImageFont.truetype("arial.ttf", 9 if size <= 40 else 12)
    except OSError:
        font = ImageFont.load_default()
    words = [w for w in re.split(r"[\s:]+", text) if w][:3]
    for i, wd in enumerate(words):
        draw.text((x + 3, y + 3 + i * 10), wd[:8], fill=(210, 210, 215), font=font)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--source", required=True, help="the bare map image (GPS 0-100 edge to edge)")
    ap.add_argument("--pack", required=True, help="pack dir, for its images/ and the slot manifest")
    ap.add_argument("--out", required=True)
    ap.add_argument("--slots", default=None)
    ap.add_argument("--pop-name", default=None,
                    help="the map name inside the pack; never cropped from")
    args = ap.parse_args()
    if not args.pop_name:
        args.pop_name = "_".join(w.capitalize() for w in args.map.split("_"))

    slots_path = args.slots or os.path.join(ROOT, "build", "poptracker_%s_slots.json" % args.map)
    if not os.path.exists(slots_path):
        sys.exit("no slot manifest at %s - run patch_poptracker_pack.py first" % slots_path)
    slots = json.load(open(slots_path, encoding="utf-8"))
    pool = index_images(args.pack)
    existing = index_existing_art(args.pack, args.pop_name)
    cache = {}

    canvas = Image.new("RGB", CANVAS, (0, 0, 0))
    l, t, r, b = MAP_BOX
    canvas.paste(Image.open(args.source).convert("RGB").resize((r - l, b - t), Image.LANCZOS),
                 (l, t))
    draw = ImageDraw.Draw(canvas)

    # the pack paints each boss's required artifacts under its portrait; do the same, reading
    # the requirement from the same spec that gated the boss so the two cannot disagree
    import poptracker_logic_apply as L
    spec = L.load_logic(args.map)
    boss_arts = {k: v["artifacts"] for k, v in (spec.get("boss_pairs") or {}).items()}

    painted, cropped, chipped = 0, 0, []
    for s in slots:
        if s["kind"] == "bosses":
            members = [m.strip() for m in re.sub(r"^Boss: ", "", s["name"]).split("&")]
            # the pin is the art box's lower-right corner (same geometry the patcher used)
            units = L.PAIR_UNITS if len(members) > 1 else 1
            left, top, right, bottom = L.boss_box(s["x"], units)
            arts = []
            for m in members:
                art = portrait(args.pack, m, cache)
                if art is None:
                    icon = resolve("Boss: " + m, pool)
                    art = Image.open(icon).convert("RGBA") if icon else None
                if art is None:
                    chipped.append(m)
                arts.append(trim(art) if art is not None else None)
            # every portrait in the box at ONE common height, so a wide creature (the Manticore
            # is twice as wide as tall) reads at the same scale as its partner instead of being
            # squeezed into an equal-width column
            gap = 12
            box_w, box_h = right - left - gap, bottom - top
            aspects = [a.width / a.height if a is not None else 1.0 for a in arts]
            h = min(box_h, (box_w - gap * (len(arts) - 1)) / sum(aspects))
            widths = [asp * h for asp in aspects]
            x = left + gap / 2 + (box_w - sum(widths) - gap * (len(arts) - 1)) / 2
            for m, a, wdt in zip(members, arts, widths):
                if a is None:
                    chip(draw, int(x), int(bottom - h), int(min(wdt, h)), m)
                else:
                    a = a.resize((max(1, int(wdt)), max(1, int(h))), Image.LANCZOS)
                    canvas.paste(a, (int(x), int(bottom - a.height)), a)   # shared baseline
                    painted += 1
                x += wdt + gap
            need = boss_arts.get(s["name"])
            if need:
                # the summoning artifacts under the portraits, in rows of five
                per, w2 = 5, 26
                mid = (left + right) / 2
                for k, a in enumerate(need):
                    ic = pool.get(a.replace("_", "")) or pool.get(
                        re.sub(r"[^a-z0-9]", "", "artifactofthe" + a.split("_")[-1]))
                    if not ic:
                        continue
                    t2 = Image.open(ic).convert("RGBA").resize((w2, w2), Image.LANCZOS)
                    row, col = divmod(k, per)
                    canvas.paste(t2, (int(mid - per * w2 / 2 + col * w2),
                                      int(bottom + 6 + row * w2)), t2)
            continue
        size = TILE
        x, y = s["x"] - size // 2, s["y"] - size // 2
        im = None
        # 1. the pack's own canvas for a map this object already appears on - exact, and the only
        #    source for milestone drop art
        # a per-map counter we added borrows the art of the pack's own counter for another map
        hit = existing.get(s["name"]) or existing.get(BORROW_ART.get(s["name"], ""))
        if hit:
            src, ex, ey = hit
            if src not in cache:
                cache[src] = Image.open(src).convert("RGB")
            sheet = cache[src]
            # column 1 and row 1 start at pin 25, so the tile begins at -2: slide it back inside
            # rather than dropping the art, which is what silently emptied the first column.
            bx = min(max(ex - ART_LEAD, 0), sheet.width - CROP)
            by = min(max(ey - ART_LEAD, 0), sheet.height - CROP)
            box = (bx, by, bx + CROP, by + CROP)
            if True:
                im = sheet.crop(box).resize((size, size), Image.LANCZOS).convert("RGBA")
                cropped += 1
        # 2. a standalone icon the pack ships
        if im is None:
            icon = resolve(s["name"], pool)
            if icon:
                im = Image.open(icon).convert("RGBA").resize((size, size), Image.LANCZOS)
                painted += 1
        if im is None:
            chip(draw, x, y, size, s["name"])
            chipped.append(s["name"])
        else:
            canvas.paste(im, (x, y), im)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    canvas.save(args.out)
    print("wrote %s  (%dx%d)" % (args.out, *CANVAS))
    print("  %d cropped from the pack's own canvases, %d from icon files, %d text chips" %
          (cropped, painted, len(chipped)))
    for n in chipped:
        print("     chip: %s" % n)
    print("\nRe-run the patcher with this calibration so map pins follow the map into the canvas:")
    print("  --size %dx%d --calib 0,0,%d,%d --calib 100,100,%d,%d"
          % (CANVAS[0], CANVAS[1], l, t, r, b))


if __name__ == "__main__":
    main()
