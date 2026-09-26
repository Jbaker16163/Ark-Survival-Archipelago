"""Add a map to an existing PopTracker pack, in place, from data/.

`gen_poptracker_map.py` emits chunks for content that is NEW. Most of a map's checks are not new -
tames, kills and milestones are shared with maps the pack already carries, and the pack stores each
of those ONCE as a location object whose `map_locations` array lists every map it appears on. So
adding a map is mostly "append one more pin to 142 existing objects", which is what this does.

    python tools/patch_poptracker_pack.py ragnarok --pack ../Arkipelago-Poptracker-0.0.8 \
        --out build/pack_ragnarok --image images/maps/Ragnarok.png --size 1024x1024 \
        --calib 0,0,0,0 --calib 100,100,1024,1024

Reads `scripts/autotracking/location_mapping.lua` for the id-to-tree-path index the pack already
has, then for every location our `maps.json` gives this map:

  * already in the pack  -> append a pin to that object's `map_locations`
  * new, and a region    -> new object at its REAL pixel (polygon centroid, world->GPS->pixel)
  * new, anything else   -> new object in the margin grid, the same 8-column 33.2px grid Lurch
                            uses for tames and milestones (they have no single map position)

Writes a full patched copy of the pack to --out. The original is never touched: diff the two
before handing anything over.
"""
import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import poptracker_logic_apply as L                                      # noqa: E402
from gen_poptracker_map import fit, load, world_to_gps                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lurch's margin grid, measured off The_Island and Scorched_Earth in 0.0.8: 8 columns, 33.2px
# apart, first cell at (25, 25). Tames/kills/milestones sit here because they have no one place
# on the map. Keep the geometry identical so a new map's overlay lines up with the others.
GRID_X0, GRID_Y0, GRID_STEP, GRID_COLS = 25, 25, 33.2, 8
# Provisional boss slots, handed out while objects are created. Final boss positions come from
# poptracker_logic_apply.boss_row(), which the compositor shares, so pins and art always agree.
BOSS_X0, BOSS_Y0, BOSS_STEP, BOSS_COLS = 320, 800, 70, 10
# nothing is placed in a separate strip any more: artifacts are pinned on the map itself
STRIP_SECTIONS = ()
STRIP_X0, STRIP_Y0, STRIP_COLS = 700, 800, 9


def walk(node, path, out):
    """Yield (path, object) for every location object (anything carrying `sections`)."""
    if isinstance(node, list):
        for c in node:
            walk(c, path, out)
        return
    if not isinstance(node, dict):
        return
    here = path + [node["name"]] if node.get("name") else path
    if node.get("sections"):
        out["/".join(here)] = node
    for c in node.get("children") or []:
        walk(c, here, out)



def _span(text, start):
    """Text span of the JSON object starting at the `{` at or after `start`.

    Hand-written rather than parsed, because the layout file is JSONC - it carries `//` comments
    and a UTF-8 BOM, so json.load/dump would either choke or silently strip every comment the
    author left in. Skips strings and comments so their braces do not confuse the count.
    """
    i = text.index("{", start)
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == '"':
            j += 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == chr(92) else 1
        elif c == "/" and j + 1 < n and text[j + 1] == "/":
            j = text.find(chr(10), j)
            if j < 0:
                break
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i, j + 1
        j += 1
    raise ValueError("unbalanced braces from offset %d" % start)


def add_layout_tab(path, pop, model_title):
    """Clone the pack's existing tracker tab for a new map, so it gets its own top-level tab.

    Registering a map in maps.json only makes the IMAGE available - nothing displays it. The tab
    strip comes from the layout, so a map added without this step is fully wired and completely
    invisible, which is exactly how it looks when you load the pack and find nothing changed.
    """
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    title = '"%s Tracker"' % pop
    if title in text:
        return False

    anchor = text.find('"title": "%s"' % model_title)
    if anchor < 0:
        raise SystemExit("layout has no %r tab to clone" % model_title)
    lo, hi = _span(text, text.rfind("{", 0, anchor))
    block = text[lo:hi]

    # the inner left-docked tabbed widget is the per-map strip; this map needs exactly one entry
    key = '"type": "tabbed",'
    k = block.find(key)
    if k < 0:
        raise SystemExit("cloned tab has no inner map strip")
    ts = block.index('"tabs":', k)
    a = block.index("[", ts)
    depth, b = 0, a
    while b < len(block):
        if block[b] == "[":
            depth += 1
        elif block[b] == "]":
            depth -= 1
            if depth == 0:
                break
        b += 1
    pad = " " * 22
    strip = "\n".join([
        "[",
        pad + "{",
        pad + '  "title": "%s",' % pop.replace("_", " "),
        pad + '  "content": {',
        pad + '    "type": "map",',
        pad + '    "maps": ["%s"]' % pop,
        pad + "  }",
        pad + "}",
        " " * 20 + "]",
    ])
    block = block[:a] + strip + block[b + 1:]
    block = block.replace('"title": "%s"' % model_title, '"title": %s' % title, 1)

    indent = " " * (lo - text.rfind("\n", 0, lo) - 1)
    out = text[:hi] + ",\n" + indent + block + text[hi:]
    with open(path, "wb") as fh:
        if bom:
            fh.write(b"\xef\xbb\xbf")
        fh.write(out.encode("utf-8"))
    return True



# The pack spells a few creatures differently from our display names (and carries one typo,
# "arthropluera"). Every entry was checked against a code that actually exists in the pack's own
# dino grids - an unmatched creature is reported, never guessed at.
DINO_CODE_ALIAS = {
    "arthropleura": "arthropluera_ride", "compsognathus": "compy",
    "dunkle": "dunkleosteus_ride", "pachycephalosaurus": "pachy_ride",
    "paraceratherium": "paracer_ride", "sarcosuchus": "sarco_ride",
    "therizinosaurus": "therizinosaur_ride",
}


def _grid_codes(text, key):
    """The item codes listed in one itemgrid layout."""
    i = text.index('"%s"' % key)
    j = text.index('"rows"', i)
    end = text.index("  },", j)
    # the scan starts at the "rows" key itself, so drop it - left in, it becomes an item code
    return [c for c in re.findall(r'"([a-z0-9_]+)"', text[j:end]) if c != "rows"]


def add_dino_grid(path, pop, wanted, habitat=None):
    """Give the map its own Tames grid, listing only the creatures that map actually has.

    Without this the new tab reuses `dino_grid`, which is the Island roster - so it shows Island
    creatures the map does not have and hides the ones it does. The pack already does this per
    map: `scorched_dino_grid` exists for the same reason.
    """
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    key = "%s_dino_grid" % pop.lower()

    pool = set(_grid_codes(text, "dino_grid")) | set(_grid_codes(text, "scorched_dino_grid"))
    by = {}
    for c in sorted(pool):
        by.setdefault(re.sub(r"[^a-z0-9]", "", c.replace("_ride", "")), c)

    codes, missing, back = [], [], {}
    for name in wanted:
        k = re.sub(r"[^a-z0-9]", "", name.lower())
        c = by.get(k) or DINO_CODE_ALIAS.get(k)
        if c and c in pool:
            codes.append(c)
            back[c] = name
        else:
            missing.append(name)
    # GROUPED like the pack's own grids, not flat-sorted: land creatures first, then water, then
    # flyers, each starting a fresh row. dino_grid and scorched_dino_grid are both built this way.
    habitat = habitat or {}
    rank = {"land": 0, "water": 1, "air": 2}
    codes = sorted(set(codes), key=lambda c: (rank.get(habitat.get(back.get(c, ""), "land"), 0), c))
    groups = []
    for c in codes:
        g = rank.get(habitat.get(back.get(c, ""), "land"), 0)
        if not groups or groups[-1][0] != g:
            groups.append((g, []))
        groups[-1][1].append(c)

    if '"%s"' % key not in text:
        rows = [g[i:i + 10] for _, g in groups for i in range(0, len(g), 10)]
        nl = chr(10)
        body = ("," + nl).join("      [%s]" % ", ".join('"%s"' % c for c in r) for r in rows)
        block = nl.join([
            '  "%s":' % key,
            "  {",
            '    "type": "itemgrid",',
            '    "h_alignment": "left",',
            '    "item_margin": "2,3,-10,-10",',
            '    "item_size": "40,40",',
            '    "rows":',
            "    [",
            body,
            "    ]",
            "  },",
            "",
            "",
        ])
        anchor = text.index('  "dino_grid"')
        text = text[:anchor] + block + text[anchor:]

    # point only THIS map's tab at the new grid
    tab = text.index('"%s Tracker"' % pop)
    lo, hi = tab, _span(text, text.rfind("{", 0, tab))[1]
    seg = text[lo:hi].replace('"key": "dino_grid"', '"key": "%s"' % key, 1)
    text = text[:lo] + seg + text[hi:]

    with open(path, "wb") as fh:
        if bom:
            fh.write(b"\xef\xbb\xbf")
        fh.write(text.encode("utf-8"))
    return codes, missing


def effective_items(map_key):
    """The item ids this map actually has.

    Mirrors the apworld's map filter, which is FAIL-OPEN: an id maps.json has never heard of, or
    one tagged `any`, is on every map. Filtering on `content[map].items` alone would strip the ~542
    base engrams that are simply untagged and leave a nearly empty grid.
    """
    content = load("maps.json").get("content", {})
    mine = set(content.get(map_key, {}).get("items") or [])
    anyset = set(content.get("any", {}).get("items") or [])
    tagged = set()
    for m, d in content.items():
        if m != "any":
            tagged |= set(d.get("items") or [])
    return mine, anyset, tagged


def build_item_grids(path, pop, map_key, model, extra, add=None):
    """Give the map its own Crafting/Weapons/Armor/Loot/Artifact grids.

    A cloned tab reuses the model map's grids, so it shows content this map does not have. Each
    grid here starts as the union of the model's and the alternate's - codes in both are base
    content and always stay - and then drops only what our data positively says this map lacks.
    A code that cannot be bound to one of our ids is KEPT, matching the fail-open map filter:
    showing one item too many is a cosmetic slip, hiding a real check is a wrong tracker.
    """
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")

    mine, anyset, tagged = effective_items(map_key)

    def has(item_id):
        return item_id in mine or item_id in anyset or item_id not in tagged

    crates = {}
    for c in load("crates.json").get("crate_items") or []:
        crates[re.sub(r"[^a-z0-9]", "", c["ap_name"].lower())] = c["id"]
    arts = {re.sub(r"[^a-z0-9]", "", a["name"].replace("Artifact: ", "").lower()): a["id"]
            for a in load("crates.json").get("artifact_locations") or []}
    map_locs = set(load("maps.json").get("content", {}).get(map_key, {}).get("locations") or [])
    cats = load("locations.json")["location_categories"]
    checks = [e["name"] for e in cats["inventory_checks"]["entries"] if e["id"] in map_locs]
    bosses = [e["name"] for e in cats["bosses"]["entries"] if e["id"] in map_locs]

    def keep(code):
        # crates: drop_<colour> is a beacon, crate_<colour>_<biome> a cave crate, plus the deep sea
        m = re.match(r"^drop_([a-z]+)$", code)
        if m:
            return has(crates.get(m.group(1), -1)) if m.group(1) in crates else True
        if code == "sea_crate":
            return has(crates.get("deepseacrate", -1))
        if code.startswith("crate_"):
            colour, _, biome = code[len("crate_"):].partition("_")
            want = {"easy": "cavecave", "ice": "caveicecave", "swamp": "caveswampcave",
                    "ocean": "caveunderwatercave"}.get(biome)
            if want:
                return any(k.startswith(want) and has(v) for k, v in crates.items())
            return True
        # artifacts and boss trophies
        m = re.match(r"^artifact_([a-z]+)$", code)
        if m:
            return m.group(1) in arts and (arts[m.group(1)] in map_locs
                                           or arts[m.group(1)] is None)
        m = re.match(r"^([a-z]+)_trophy$", code)
        if m:
            # a boss trophy belongs if this map HAS that boss; a few (the alpha wyvern) are only
            # inventory checks, so accept either source
            word = m.group(1)
            return (any(word in b.lower().replace(" ", "") for b in bosses)
                    or any("trophy" in c.lower() and word in c.lower().replace(" ", "")
                           for c in checks))
        return True

    # only codes that are real items: the pack's own grids carry a couple that were never
    # defined (wyvern_trophy), and copying those forward just moves the dead reference along
    real = set()

    def collect(n):
        if isinstance(n, list):
            for c in n:
                collect(c)
        elif isinstance(n, dict):
            if n.get("codes"):
                real.update(x.strip() for x in str(n["codes"]).split(","))
            for v in n.values():
                if isinstance(v, (list, dict)):
                    collect(v)
    collect(json.load(open(os.path.join(os.path.dirname(path), "..", "items", "items.json"),
                           encoding="utf-8")))

    made = {}
    for base in ("crafting_grid", "weapon_grid", "armor_grid", "loot_grid", "artifact_grid"):
        try:
            pool = sorted(set(_grid_codes(text, base)) | set(_grid_codes(text, extra + base)))
        except ValueError:
            continue
        codes = [c for c in pool if c in real and keep(c)]
        # codes this map needs that neither model grid carries (artifact_devious): without a
        # grid cell the toggle cannot even be clicked by hand
        codes += [c for c in (add or {}).get(base, []) if c in real and c not in codes]
        key = "%s_%s" % (pop.lower(), base)
        if '"%s"' % key not in text:
            nl = chr(10)
            per = 8 if base != "artifact_grid" else 5
            rows = [codes[i:i + per] for i in range(0, len(codes), per)]
            body = ("," + nl).join(
                "      [%s]" % ", ".join('"%s"' % c for c in r) for r in rows)
            block = nl.join(['  "%s":' % key, "  {", '    "type": "itemgrid",',
                             '    "h_alignment": "left",',
                             '    "item_margin": "2,3,-10,-10",',
                             '    "item_size": "35,35",', '    "rows":', "    [", body,
                             "    ]", "  },", "", ""])
            anchor = text.index('  "%s"' % base)
            text = text[:anchor] + block + text[anchor:]
        made[base] = (key, len(pool), len(codes))

    tab = text.index('"%s Tracker"' % pop)
    lo, hi = tab, _span(text, text.rfind("{", 0, tab))[1]
    seg = text[lo:hi]
    for base, (key, _, _) in made.items():
        seg = seg.replace('"key": "%s"' % base, '"key": "%s"' % key, 1)
    text = text[:lo] + seg + text[hi:]

    with open(path, "wb") as fh:
        if bom:
            fh.write(b"\xef\xbb\xbf")
        fh.write(text.encode("utf-8"))
    return made



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--pack", required=True, help="path to the unpacked pack directory")
    ap.add_argument("--out", required=True, help="where to write the patched copy")
    ap.add_argument("--pop-name")
    ap.add_argument("--image", required=True)
    ap.add_argument("--size", required=True)
    ap.add_argument("--calib", action="append", required=True, metavar="lat,lon,x,y")
    ap.add_argument("--dot", type=int, default=10)
    ap.add_argument("--layout", default="tracker_standard.json",
                    help="layout file that carries the top-level tab strip")
    ap.add_argument("--alt-prefix", default="scorched_",
                    help="prefix of the pack's other per-map grids, unioned with the model's")
    ap.add_argument("--model-tab", default="The Island Tracker",
                    help="existing tab to clone for this map's tab")
    args = ap.parse_args()

    pop = args.pop_name or "_".join(w.capitalize() for w in args.map.split("_"))
    w, h = (int(v) for v in args.size.lower().split("x"))
    pts = [[float(v) for v in c.split(",")] for c in args.calib]
    if len(pts) < 2 or any(len(p) != 4 for p in pts):
        sys.exit("pass --calib lat,lon,x,y at least twice")
    sx, ix = fit([(p[1], p[2]) for p in pts])
    sy, iy = fit([(p[0], p[3]) for p in pts])

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    shutil.copytree(args.pack, args.out)

    # --- what the pack already knows -------------------------------------------------------
    lm_path = os.path.join(args.out, "scripts/autotracking/location_mapping.lua")
    lm = open(lm_path, encoding="utf-8").read()
    # Three shapes of mapping line, and each needs different handling:
    #   {{"@Path"}}                      -> a pin on an existing object
    #   {{"level", "consumable", 1}}     -> an ITEM counter only; the pack shows no pin for it
    #   {{"@X (The Island)/..."}, {...}} -> each map's own counter object (Explore N Regions)
    id_to_path, item_only, per_map_line = {}, set(), {}
    for m in re.finditer(r'^[ \t]*\[(\d+)\][ \t]*=[ \t]*(\{.*\})[ \t]*,?[ \t]*(?:--.*)?$', lm, re.M):
        i = int(m.group(1))
        paths = re.findall(r'"@([^"]+)"', m.group(2))
        if not paths:
            item_only.add(i)
        elif all(re.search(r" \((The Island|Scorched Earth)\)(/|$)", pth) for pth in paths):
            per_map_line[i] = paths[0]
        else:
            id_to_path[i] = paths[0]

    trees, objs = {}, {}
    locdir = os.path.join(args.out, "locations")
    for fn in sorted(os.listdir(locdir)):
        if not fn.endswith(".json"):
            continue
        trees[fn] = json.load(open(os.path.join(locdir, fn), encoding="utf-8"))
        found = {}
        walk(trees[fn], [], found)
        for p, o in found.items():
            objs.setdefault(p, (fn, o))

    # --- what this map needs ----------------------------------------------------------------
    want = set(load("maps.json").get("content", {}).get(args.map, {}).get("locations") or [])
    if not want:
        sys.exit("data/maps.json lists no locations for %r" % args.map)
    # Only ids the multiworld can actually send. maps.json also tags crates.json's
    # artifact_locations (8752xxx), which the apworld never turns into locations - a pin mapped to
    # one of those can never tick. Bosses are not AP locations either, but they stay: the pack
    # shows them as hand-ticked goal markers, exactly as it does on the Island tab.
    real = set(L.ap_location_ids(load))
    # Same rule as the apworld's map filter: content tagged to this map, PLUS everything tagged
    # `any` or not tagged at all. Reading only this map's own list skipped every shared check - the
    # generic milestones, level-ups, deaths and the whole crop/meat/resource collect set - which is
    # why those sidebar groups came out nearly empty.
    content = load("maps.json").get("content", {})
    tagged_any = set(content.get("any", {}).get("locations") or [])
    tagged_maps = set()
    for mk, c in content.items():
        if mk != "any":
            tagged_maps |= set(c.get("locations") or [])
    other_regions = {r["id"] for r in load("explore_areas.json")["regions"].values()
                     if r.get("map") != args.map}
    shared = {i for i in real if (i in tagged_any or i not in tagged_maps)} - other_regions
    # "Collect N Explorer Notes" only exists when the slot has N notes; the apworld drops the
    # rest, so a map with few notes of its own must not show milestones it can never have.
    cats = load("locations.json")["location_categories"]
    note_ids = {e["id"] for e in cats["dossiers"]["entries"]}
    n_notes = len((want | shared) & note_ids)
    for e in cats["milestones"]["entries"]:
        tag = e.get("tag", "")
        if tag.startswith("milestone_notes_") and e["id"] in shared:
            try:
                if int(tag.rsplit("_", 1)[1]) > n_notes:
                    shared.discard(e["id"])
            except ValueError:
                pass
    want |= shared
    phantom = sorted(i for i in want if i not in real)
    want -= set(phantom)

    regions = {r["id"]: r for r in load("explore_areas.json")["regions"].values()
               if r.get("map") == args.map}
    # Every id source, because a name the pack can show has to come from somewhere: notes and
    # bosses live in locations.json, artifacts in crates.json, tames/kills in dinos.json.
    names = {}
    for cat, v in load("locations.json")["location_categories"].items():
        for e in (v["entries"] if isinstance(v, dict) else v):
            names[e["id"]] = (cat, e["name"])
    for e in load("crates.json").get("artifact_locations") or []:
        names[e["id"]] = ("artifacts", e["name"])
    dj = load("dinos.json")
    for e in (dj["dinos"] if isinstance(dj, dict) else dj):
        # ap_name is the ITEM name and already reads "Tame: Griffin", so strip that prefix
        # rather than prefixing it again ("Tame: Tame: Griffin") - and rather than using
        # dino_tag, which is ARK's internal short name (RockElemental, camelsaurus) and does
        # not match the display names the logic spec and the pack's own objects use.
        # same precedence as apworld Locations.py: the explicit `name` (kill-only creatures such
        # as "Jug Bug" only have that), else ap_name without its prefix. dino_tag is the internal
        # short name ("Jugbug", "camelsaurus") and matches neither our locations nor the pack.
        label = e.get("name") or re.sub(r"^Tame: ", "", e.get("ap_name") or "") or e["dino_tag"]
        if e.get("tame_loc"):
            names[e["tame_loc"]] = ("tames", "Tame: %s" % label)
        if e.get("kill_loc"):
            names[e["kill_loc"]] = ("kills", "Kill: %s" % label)

    # --- 1. existing objects: one more pin ---------------------------------------------------
    slot = [0]

    bslot = [0]
    slots = []
    pending = []

    def grid():
        n = slot[0]
        slot[0] += 1
        return (round(GRID_X0 + GRID_STEP * (n % GRID_COLS)),
                round(GRID_Y0 + GRID_STEP * (n // GRID_COLS)))

    def boss_slot():
        """Bosses get the wide strip under the map, like the pack's own boss portraits."""
        n = bslot[0]
        bslot[0] += 1
        return (round(BOSS_X0 + BOSS_STEP * (n % BOSS_COLS)),
                round(BOSS_Y0 + BOSS_STEP * (n // BOSS_COLS)))

    patched, touched, dirty = 0, set(), set()
    for loc_id in sorted(want):
        p = id_to_path.get(loc_id)
        if not p:
            continue
        parent = "/".join(p.split("/")[:2])
        if parent in touched:
            continue
        hit = objs.get(parent) or objs.get(p)
        if not hit:
            print("-- pack maps %d to %r but no such object; skipped" % (loc_id, parent))
            continue
        fn, o = hit
        touched.add(parent)
        if any(ml.get("map") == pop for ml in o.get("map_locations") or []):
            continue
        # position is assigned later, once every object is known: the pack groups its sidebar
        # (land / water / flyers / alphas / drops / food / resources) rather than sorting it,
        # and that grouping can only be applied to the whole set at once
        pins = {ml.get("map"): ml for ml in o["map_locations"]}
        src = pins.get("The_Island") or pins.get("Scorched_Earth")
        pending.append({"obj": o, "name": o["name"], "own": False,
                        "src": (src["y"], src["x"]) if src else None,
                        "src_map": src.get("map") if src else None})
        dirty.add(fn)
        patched += 1

    # --- 2. new objects ----------------------------------------------------------------------
    # Regions follow the pack's own naming for exploration - `Milestones/Exploration (<Map>)/
    # Visit <name>` - because the pack already has commented-out placeholder lines in exactly
    # that shape. Everything else goes in the map's own group.
    def obj(nm, x, y, sec):
        o = {"name": nm,
             "chest_unopened_img": "images/items/chest_gold_x.png",
             "chest_opened_img": "images/items/chest_o.png",
             "map_locations": [{"map": pop, "x": x, "y": y}]}
        o["sections"] = [{"name": sec, "item_count": 1}] if sec else [{"item_count": 1}]
        return o

    logic = L.load_logic(args.map)
    explore_group = "Exploration (%s)" % pop
    boss_objs = {}
    new_children, explore_children, new_mapping, off_image = [], [], [], 0
    reused, attached, progress_secs, skipped_item_only = [], [], [], 0
    PROGRESS_NAME = "Explore Regions"
    suffix = " (%s)" % pop
    for loc_id in sorted(want - set(id_to_path)):
        if loc_id in regions:
            r = regions[loc_id]
            poly = r["polygon"]
            if not poly:
                continue
            cx = sum(q[0] for q in poly) / len(poly)
            cy = sum(q[1] for q in poly) / len(poly)
            lat, lon = world_to_gps(args.map, r.get("realm"), cx, cy)
            x, y = round(ix + sx * lon), round(iy + sy * lat)
            if not (0 <= x <= w and 0 <= y <= h):
                off_image += 1
            base = r["name"][:-len(suffix)] if r["name"].endswith(suffix) else r["name"]
            nm = "Visit %s" % base
            explore_children.append(obj(nm, x, y, None))
            new_mapping.append('[%d] = {{"@Milestones/%s/%s"}},' % (loc_id, explore_group, nm))
        elif loc_id in item_only and not names.get(loc_id, ("", ""))[1].startswith("Artifact: "):
            skipped_item_only += 1          # a counter in the pack (levels): no pin by design
            continue
        elif loc_id in per_map_line:
            # another map's per-map counter (Explore N Regions): give Ragnarok its own section
            secname = per_map_line[loc_id].rsplit("/", 1)[-1]
            if secname not in [x["name"] for x in progress_secs]:
                progress_secs.append({"name": secname, "item_count": 1})
            new_mapping.append('[%d] = {{"@Milestones/%s/%s/%s"}},'
                               % (loc_id, explore_group, PROGRESS_NAME, secname))
            continue
        else:
            cat, nm = names.get(loc_id, ("unknown", "Location %d" % loc_id))
            # a check the pack has no section for, on an object it DOES have: add the section
            # there (Collect 100 Mejoberries -> Mejoberry/x100) instead of a loose new object
            att = L.attach_section(nm, objs, logic.get("attach", {}))
            if att:
                path, o, secname, fnm = att
                new_mapping.append('[%d] = {{"@%s/%s"}},' % (loc_id, path, secname))
                dirty.add(fnm)
                attached.append(nm)
                if not any(ml.get("map") == pop for ml in o.get("map_locations") or []):
                    o.setdefault("map_locations", []).append({"map": pop, "x": 0, "y": 0})
                    pins3 = {ml.get("map"): ml for ml in o["map_locations"]}
                    src3 = pins3.get("The_Island") or pins3.get("Scorched_Earth")
                    pending.append({"obj": o, "name": o["name"], "own": False,
                                    "src": (src3["y"], src3["x"]) if src3 else None,
                                    "src_map": src3.get("map") if src3 else None})
                continue
            # An object of this name may ALREADY exist: the pack authored Mantis, Wyvern,
            # Deathworm and friends for Scorched but never wired their ids, so they look new to
            # the id index. Reuse it instead of creating a second object of the same name.
            hit = L.resolve_existing(nm, objs)
            if hit and cat != "bosses":
                path, o, secname = hit
                fn = next(f for pth, (f, ob) in objs.items() if pth == path)
                if not any(ml.get("map") == pop for ml in o.get("map_locations") or []):
                    o.setdefault("map_locations", []).append({"map": pop, "x": 0, "y": 0})
                    pins2 = {ml.get("map"): ml for ml in o["map_locations"]}
                    src2 = pins2.get("The_Island") or pins2.get("Scorched_Earth")
                    pending.append({"obj": o, "name": o["name"], "own": False,
                                    "src": (src2["y"], src2["x"]) if src2 else None,
                                    "src_map": src2.get("map") if src2 else None})
                kind = "tames" if nm.startswith("Tame") else "kills" if nm.startswith("Kill") else ""
                spec_rules = (logic.get(kind, {}) or {}).get(o["name"]) if kind else None
                for sec_obj in o.get("sections") or []:
                    if sec_obj.get("name") == secname and spec_rules:
                        sec_obj["access_rules"] = L.merge_rules(sec_obj.get("access_rules"),
                                                                spec_rules)
                dirty.add(fn)
                reused.append(nm)
                new_mapping.append('[%d] = {{"@%s/%s"}},' % (loc_id, path, secname))
                continue
            # Gamma/Beta/Alpha are three checks on ONE fight, so they share a portrait and become
            # three sections of it, the way difficulty tiers read on the other maps.
            tier = re.search(r"\s*\((Gamma|Beta|Alpha)\)\s*$", nm)
            if cat == "bosses" and tier:
                base = nm[:tier.start()].strip()
                if base in boss_objs:
                    o = boss_objs[base]
                    o["sections"].append({"name": tier.group(1), "item_count": 1})
                else:
                    x, y = boss_slot()
                    o = obj(base, x, y, tier.group(1))
                    boss_objs[base] = o
                    slots.append({"name": base, "kind": cat, "x": x, "y": y})
                    new_children.append(o)
                new_mapping.append('[%d] = {{"@%s/%s/%s"}},'
                                   % (loc_id, pop, base, tier.group(1)))
                continue
            x, y = (boss_slot() if cat == "bosses" else (0, 0))
            sec = cat.replace("_", " ").title()
            o = obj(nm, x, y, sec)
            if cat == "bosses":
                slots.append({"name": nm, "kind": cat, "x": x, "y": y})
            else:
                pending.append({"obj": o, "name": nm, "src": None, "src_map": None, "own": True})
            new_children.append(o)
            new_mapping.append('[%d] = {{"@%s/%s/%s"}},' % (loc_id, pop, nm, sec))

    # exploration hangs off the pack's existing Milestones root, not a new one
    if progress_secs:
        prog = obj(PROGRESS_NAME, 0, 0, None)
        prog["sections"] = sorted(progress_secs, key=lambda x: int(re.sub(r"\D", "", x["name"]) or 0))
        explore_children.insert(0, prog)
        pending.append({"obj": prog, "name": PROGRESS_NAME, "src": None, "src_map": None,
                        "own": False, "force": "counters"})
    if explore_children:
        ms = trees.get("milestones.json")
        root = None
        for cand in (ms if isinstance(ms, list) else [ms]):
            if isinstance(cand, dict) and cand.get("name") == "Milestones":
                root = cand
                break
        if root is None:
            sys.exit("pack has no 'Milestones' root in locations/milestones.json")
        root.setdefault("children", [])
        root["children"] = [c for c in root["children"] if c.get("name") != explore_group]
        root["children"].append({"name": explore_group, "children": explore_children})
        dirty.add("milestones.json")

    # --- 2b. logic --------------------------------------------------------------------------
    # Merge Tame/Kill into one object per creature FIRST: an orange (partly reachable) pin comes
    # from an object's sections, so while tame and kill are separate objects each can only ever
    # be red or green.
    new_items = L.add_missing_items(args.out, logic.get("new_items", {}))
    new_children, new_mapping = L.merge_creature_objects(new_children, new_mapping, pop, logic)
    ruled = L.apply_rules(new_children, logic, pop)
    new_children, new_mapping, paired = L.pair_bosses(new_children, new_mapping, logic, pop)
    # artifacts go ON the map at their caves, one pin per cave (see group_artifacts_by_cave)
    new_children, new_mapping, on_map = L.group_artifacts_by_cave(
        new_children, new_mapping, logic, pop,
        lambda lat, lon: (round(ix + sx * lon), round(iy + sy * lat)))

    # Pairing frees the slot the second boss held, so reflow the strip - otherwise there is a
    # visible hole where Manticore used to be. Widest entry first, since a paired boss also has
    # to fit a row of artifact icons under it.
    boss_names = [c["name"] for c in new_children
                  if c["name"].startswith("Boss: ") or c["name"] in paired]
    boss_names.sort(key=lambda n: (n not in paired, n))
    # left to right, centred under the map; a pair is two portraits wide. Pins go where the pack
    # puts its own: the portrait's lower-right corner, at the larger boss marker size.
    pins = L.boss_row([(nm, L.PAIR_UNITS if nm in paired else 1) for nm in boss_names])
    for c in new_children:
        if c["name"] in pins and c.get("map_locations"):
            ml = c["map_locations"][0]
            ml["x"], ml["y"] = pins[c["name"]]
            ml["size"] = L.BOSS_MARKER_SIZE

    # Merging Tame/Kill into one object and pairing the bosses REPLACE objects, so a queued pin
    # can point at one that no longer exists - it would silently consume a grid cell and leave a
    # hole. Keep only surviving objects, then queue whatever the merge produced.
    alive = {id(c) for c in new_children}
    pending = [pd for pd in pending if not pd["own"] or id(pd["obj"]) in alive]
    # an object can be queued twice (pinned in pass 1, then given a section in pass 2); a second
    # entry would claim its own grid cell and leave a hole where the object is not
    seen_obj, deduped = set(), []
    for pd in pending:
        if id(pd["obj"]) not in seen_obj:
            seen_obj.add(id(pd["obj"]))
            deduped.append(pd)
    pending = deduped
    queued = {id(pd["obj"]) for pd in pending}
    for c in new_children:
        if id(c) in queued:
            continue
        if any(ml.get("map") == pop and (ml.get("x"), ml.get("y")) == (0, 0)
               for ml in c.get("map_locations") or []):
            pending.append({"obj": c, "name": c["name"], "src": None, "src_map": None,
                            "own": True})

    # --- 2c. place the sidebar pins in the pack's own order -----------------------------------
    # Grouped, not sorted: land creatures, water, flyers, alphas, apex drops, crops, meats,
    # resources - each starting a fresh row, exactly as The_Island's canvas reads top to bottom.
    # An object the pack already pins on the Island inherits ITS group and position, so shared
    # content lands in the same place on both maps; anything new is classified from our data.
    habitat = {}
    for e in load("spawn_classes.json").get("spawn_classes", []):
        habitat[e["name"]] = e.get("habitat")
    tributes = set(load("tame_logic.json").get("tribute_dino", {}))
    entries = []
    for pd in pending:
        if pd.get("force"):
            sec, key = pd["force"], (2, 10 ** 6, pd["name"])   # same shape as every other key
        elif pd["src"]:
            bands = L.SCORCHED_BANDS if pd["src_map"] == "Scorched_Earth" else None
            sec = L.island_section(pd["src"][0], bands)
            # Island-pinned content sorts first inside its group, so shared rows read the same
            key = ((1 if pd["src_map"] == "Scorched_Earth" else 0),) + tuple(pd["src"])
        else:
            sec = L.classify(pd["name"], habitat, tributes)
            key = (2, 10 ** 6, pd["name"])
        entries.append((sec, key, pd))
    # Ragnarok needs more sidebar rows than the Island's canvas has, so the groups that are ours
    # rather than the pack's - artifacts and the loose dossier - go in the empty space beside the
    # boss portraits, which is where the artifacts' purpose (summoning those bosses) is anyway.
    side = [e for e in entries if e[0] not in STRIP_SECTIONS]
    strip = [e for e in entries if e[0] in STRIP_SECTIONS]
    placed = (L.sidebar_layout(side, GRID_COLS, GRID_X0, GRID_Y0, GRID_STEP) +
              L.sidebar_layout(strip, STRIP_COLS, STRIP_X0, STRIP_Y0, GRID_STEP))
    for pd, gx, gy in placed:
        o = pd["obj"]
        placed = False
        for ml in o.get("map_locations") or []:
            if ml.get("map") == pop:
                ml["x"], ml["y"] = gx, gy
                placed = True
        if not placed:
            o.setdefault("map_locations", []).append({"map": pop, "x": gx, "y": gy})
        slots.append({"name": pd["name"], "kind": "grid", "x": gx, "y": gy})

    retargeted = L.retarget_rules(list(trees.values()) +
                                  [[{"name": pop, "children": new_children}]], pop)

    # --- 3. write the pack back out -----------------------------------------------------------
    # INDENT matters: the pack is indented 4, and re-dumping at any other width rewrites every
    # line of a 200KB file, burying the handful of real additions in a diff nobody can review.
    # Same reason files that gained nothing are left completely alone.
    def dump(path, obj):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=4)

    rewritten = []
    for fn, tree in trees.items():
        if fn in dirty:
            dump(os.path.join(locdir, fn), tree)
            rewritten.append(fn)

    new_file = "%s.json" % args.map
    dump(os.path.join(locdir, new_file),
         [{"name": pop, "color": "#FFFFFF", "children": new_children}])

    mj = os.path.join(args.out, "maps/maps.json")
    maps = json.load(open(mj, encoding="utf-8"))
    if not any(m["name"] == pop for m in maps):
        maps.append({"name": pop, "location_size": args.dot,
                     "location_border_thickness": max(2, args.dot // 4), "img": args.image})
        dump(mj, maps)

    # The pack keeps commented-out placeholder lines for content it planned but never authored.
    # Ours supersede them, and leaving both makes the next reader guess which is live.
    body = "\n".join(l for l in lm.splitlines()
                     if not (l.lstrip().startswith("--") and "(%s)" % pop in l))
    body = body.rstrip().rstrip("}").rstrip()
    # A Lua table separates entries with commas, and the last existing line often has none
    # (it was the final entry until now). Without this the appended block is a syntax error.
    if not body.endswith(","):
        body += ","
    # An id the pack already maps (Lurch maps each artifact check to its artifact_* toggle) must
    # gain our path INSIDE that line. A second `[id] =` line is legal Lua, but the table keeps only
    # the last one, which would silently drop his toggle.
    body, new_mapping = L.merge_mapping_lines(body, new_mapping)
    # only lines that did NOT merge: a merged artifact line already carries the pack's own toggle,
    # and a second one would tick the artifact twice
    icodes = set()
    for it in json.load(open(os.path.join(args.out, "items", "items.json"), encoding="utf-8")):
        if isinstance(it, dict) and it.get("codes"):
            icodes.update(x.strip() for x in str(it["codes"]).split(","))
    new_mapping = L.add_artifact_toggles(new_mapping, pop, icodes)
    with open(lm_path, "w", encoding="utf-8") as fh:
        fh.write(body + "\n" + "\n".join(new_mapping) + "\n}\n")

    init = os.path.join(args.out, "scripts/init.lua")
    body = open(init, encoding="utf-8").read()
    line = 'Tracker:AddLocations("locations/%s")' % new_file
    if line not in body:
        with open(init, "w", encoding="utf-8") as fh:
            fh.write(body.rstrip() + "\n" + line + "\n")

    print("pack patched -> %s" % args.out)
    print("  %d existing objects gained a %s pin" % (patched, pop))
    print("  %d new objects in locations/%s, plus %d exploration nodes at real pixels"
          % (len(new_children), new_file, len(explore_children)))
    print("  %d lines appended to location_mapping.lua" % len(new_mapping))
    if phantom:
        print("  skipped %d ids that are not AP locations (%d..%d)"
              % (len(phantom), phantom[0], phantom[-1]))
    # Rebuild the manifest from the FINAL tree. Merging Tame/Kill into one object and pairing the
    # bosses both drop objects, and a slot left behind for one of them would paint artwork at a
    # pixel no pin sits on any more.
    kinds = {sl["name"]: sl["kind"] for sl in slots}
    kinds.update({label: "bosses" for label in paired})   # a pair replaced its members' slots
    final = []
    for c in new_children:
        ml = (c.get("map_locations") or [{}])[0]
        if c["name"] in on_map:
            continue                    # a pin on the terrain, not a tile of artwork
        if "x" in ml:
            final.append({"name": c["name"], "kind": kinds.get(c["name"], "grid"),
                          "x": ml["x"], "y": ml["y"]})
    seen = {f["name"] for f in final}
    final += [sl for sl in slots if sl["name"] not in seen and sl["kind"] == "grid"]
    with open(os.path.join(ROOT, "build", "poptracker_%s_slots.json" % args.map),
              "w", encoding="utf-8") as fh:
        json.dump(final, fh, indent=2)

    print("  rewrote %s; every other pack file is byte-identical" % ", ".join(sorted(rewritten)))
    dino_names = []
    rag_items = set(load("maps.json").get("content", {}).get(args.map, {}).get("items") or [])
    for e in (dj["dinos"] if isinstance(dj, dict) else dj):
        if e.get("id") in rag_items:
            dino_names.append((e.get("ap_name") or "").replace("Tame: ", "") or e["dino_tag"])

    lay = os.path.join(args.out, "layouts", args.layout)
    if add_layout_tab(lay, pop, args.model_tab):
        print("  added a '%s Tracker' tab to layouts/%s" % (pop, args.layout))
    else:
        print("  layouts/%s already has a '%s Tracker' tab" % (args.layout, pop))
    codes, no_code = add_dino_grid(lay, pop, dino_names, habitat)
    print("  %s_dino_grid: %d creatures" % (pop.lower(), len(codes)))
    for n in no_code:
        print("     no item code in the pack: %s" % n)
    if new_items:
        print("  registered %d item codes the rules need: %s"
              % (len(new_items), ", ".join(new_items)))
    print("  %d objects gained access rules%s" %
          (ruled, ", %d rule paths retargeted" % retargeted if retargeted else ""))
    if skipped_item_only:
        print("  left %d item-counter checks unpinned (the pack tracks them as counters)"
              % skipped_item_only)
    if attached:
        print("  added %d sections to existing objects: %s" % (len(attached), ", ".join(attached)))
    if reused:
        print("  reused %d objects the pack already had: %s"
              % (len(reused), ", ".join(sorted(reused))))
    for label, (members, arts) in paired.items():
        print("  paired %s -> one entry behind %d artifacts" % (" + ".join(members), len(arts)))
    for base, (key, pool, kept) in sorted(build_item_grids(
            lay, pop, args.map, args.model_tab, args.alt_prefix,
            add={"artifact_grid": sorted({a for pr in logic.get("boss_pairs", {}).values()
                                          for a in pr.get("artifacts", [])})}).items()):
        print("  %-26s %d of %d codes kept" % (key, kept, pool))
    if off_image:
        print("  WARNING %d region pins land outside %dx%d - recheck --calib" % (off_image, w, h))
    print("\nStill manual: drop the image at %s, bump package_version in manifest.json."
          % args.image)


if __name__ == "__main__":
    main()
