"""Apply a map's access rules to a patched PopTracker pack.

Everything here is driven by `tools/reference/poptracker_logic.json`, which holds the rules as
data so they can be corrected without touching code. Three jobs:

  1. Merge each new creature's separate Tame/Kill objects into ONE object with two sections,
     matching how the pack stores every other creature. That is also what makes the map pin go
     orange when one of the two is reachable and the other is not - PopTracker derives that from
     an object's sections, so two separate objects can only ever be red or green.
  2. Attach the access rules: creatures, artifacts, bosses, and the one-off locations.
  3. Register the item codes the rules need that the pack has no item for. A rule naming an
     undefined code is silently always-false, so the check it guards could never turn green.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "tools", "reference", "poptracker_logic.json")


def load_logic(map_key):
    if not os.path.exists(SPEC):
        return {}
    return json.load(open(SPEC, encoding="utf-8")).get(map_key, {})


def _placeholder_icon(path, label):
    """A visibly-placeholder icon, so a missing asset reads as missing rather than as artwork."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return
    im = Image.new("RGBA", (64, 64), (34, 34, 40, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([1, 1, 62, 62], outline=(120, 120, 140, 255))
    try:
        font = ImageFont.truetype("arial.ttf", 11)
    except OSError:
        font = ImageFont.load_default()
    words = [w for w in re.split(r"[\s\-]+", label) if w][-2:]
    for i, w in enumerate(words):
        d.text((5, 18 + i * 14), w[:9], fill=(215, 215, 225, 255), font=font)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)


def add_missing_items(pack, spec):
    """Define item codes the rules need, and map our AP ids onto them."""
    added = []
    if not spec:
        return added
    ipath = os.path.join(pack, "items", "items.json")
    items = json.load(open(ipath, encoding="utf-8"))
    have = set()
    for it in items:
        if isinstance(it, dict) and it.get("codes"):
            have.update(x.strip() for x in str(it["codes"]).split(","))

    mpath = os.path.join(pack, "scripts", "autotracking", "item_mapping.lua")
    mtext = open(mpath, encoding="utf-8").read()

    for code, meta in sorted(spec.items()):
        if code.startswith("_"):
            continue
        img = "images/engrams/%s.png" % code
        if code not in have:
            items.append({"name": meta["name"], "type": "toggle", "codes": code, "img": img})
            _placeholder_icon(os.path.join(pack, "images", "engrams", "%s.png" % code),
                              meta["name"])
            added.append(code)
        if meta.get("id") is None:
            continue                    # not an AP item: a location check ticks it instead
        line = '[%d] = {{"%s", "toggle"}},' % (meta["id"], code)
        # the pack stubs some of these out as comments (artifact_devious); replace in place
        dead = re.search(r"^[ \t]*--[ \t]*\[%d\][^\n]*$" % meta["id"], mtext, re.M)
        if dead:
            mtext = mtext[:dead.start()] + "  " + line + mtext[dead.end():]
        elif ("[%d]" % meta["id"]) not in mtext:
            body = mtext.rstrip().rstrip("}").rstrip()
            if not body.endswith(","):
                body += ","
            mtext = body + "\n  " + line + "\n}\n"

    with open(ipath, "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=4)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(mtext)
    return added


# Lurch's sidebar is grouped, not sorted: reading The_Island's pin rows top to bottom gives land
# creatures (alphabetical), then water, then flyers, then alphas, then the apex drop items, then
# crops, meats, creature resources, base resources, metals, chemicals, and the counters last.
# Each group starts on a fresh row. These are the y of the LAST row of each group on that canvas,
# which is how an object already pinned there tells us which group it belongs to.
ISLAND_BANDS = [(355, "land"), (455, "water"), (488, "air"), (521, "alpha"), (587, "drops"),
                (620, "crops"), (653, "meats"), (719, "creature_res"), (752, "base_res"),
                (785, "metals"), (818, "chems"), (9999, "counters")]
# where a group with no Island example goes; artifacts are ours, the pack has no such pins
# Scorched_Earth's canvas is laid out the same way with its own row bands, and it pins the
# creatures and resource checks the Island has never heard of - so it is the second source for
# anything Ragnarok shares with Scorched rather than with the Island.
SCORCHED_BANDS = [(224, "land"), (257, "air"), (290, "alpha"), (355, "drops"), (389, "crops"),
                  (422, "meats"), (455, "creature_res"), (488, "base_res"), (521, "metals"),
                  (554, "extra_res"), (587, "chems"), (9999, "counters")]
SECTION_ORDER = ["land", "water", "air", "alpha", "drops", "crops", "meats", "creature_res",
                 "base_res", "metals", "extra_res", "chems", "artifacts", "misc", "counters"]
# the one creature neither canvas pins, so neither can classify it
NO_PIN_HABITAT = {"Griffin": "air"}


def island_section(y, bands=None):
    for limit, name in (bands or ISLAND_BANDS):
        if y <= limit:
            return name
    return "misc"


def classify(name, habitat, tributes):
    """Group for an object the Island canvas has no pin for."""
    if name.startswith("Alpha ") or name.startswith("Killed: Alpha "):
        return "alpha"
    # a creature may still be carrying its check prefix here ("Tame: Griffin")
    for pre in ("Tamed: ", "Killed: ", "Tame: ", "Kill: "):
        if name.startswith(pre):
            name = name[len(pre):]
            break
    if name.startswith("Artifact: "):
        return "artifacts"
    if name.startswith("Dossier: "):
        return "counters"               # a one-off: the counters row has a free cell
    h = habitat.get(name) or NO_PIN_HABITAT.get(name)
    if h:
        return {"land": "land", "water": "water", "air": "air"}[h]
    base = name.rsplit(" x", 1)[0]
    if base in tributes:
        return "drops"
    return "misc"


def resolve_existing(name, objs):
    """Find a pack object that already represents this location, by display name.

    The pack authored Mantis, Wyvern, Deathworm and the rest for Scorched but never wired their AP
    ids, so they look "new" to the id index while an object of that exact name already exists.
    Creating another one would put two `Mantis` objects in the tree and split its two checks
    across them. Returns (path, object, section name) or None.
    """
    want = name
    for pre in ("Tamed: ", "Killed: ", "Tame: ", "Kill: "):
        if want.startswith(pre):
            want = want[len(pre):]
            break
    kind = "Tame" if name.startswith(("Tame: ", "Tamed: ")) else \
           "Kill" if name.startswith(("Kill: ", "Killed: ")) else ""
    for path, (fn, o) in objs.items():
        if path.rsplit("/", 1)[-1] != want:
            continue
        secs = [s.get("name", "") for s in o.get("sections") or []]
        pick = next((s for s in secs if kind and s.startswith(kind)), None)
        if pick is None:
            pick = secs[0] if len(secs) == 1 else None
        if pick is None:
            continue
        return path, o, pick
    return None


def _stem(word):
    w = word.lower().strip()
    if w.endswith("ies"):
        return w[:-3] + "y"
    return w[:-1] if w.endswith("s") else w


def attach_section(name, objs, table):
    """Give a check the pack has no section for a section on the object that should hold it.

    Two sources. `table` (spec "attach") names the object and section outright - used where the
    pack's own label would clash, e.g. its "Kill a Max Level Dino" is really our very-high check.
    Otherwise "Collect N <thing>" joins the existing <thing> object when that object already counts
    in xN sections (Mejoberry has x200; Collect 100 Mejoberries becomes its x100).
    Returns (object path, object, section name, file) or None.
    """
    def add(o, sec, like=None):
        secs = o.setdefault("sections", [])
        if any(x.get("name") == sec for x in secs):
            return
        new = {"name": sec, "item_count": 1, "access_rules": [""]}
        if like and like.get("visibility_rules"):
            n_new = re.sub(r"\D", "", sec)
            n_old = re.sub(r"\D", "", like["name"])
            new["visibility_rules"] = [r.replace("_%s_" % n_old, "_%s_" % n_new)
                                       for r in like["visibility_rules"]]
        secs.append(new)
        if all(re.fullmatch(r"x\d+", x.get("name", "")) for x in secs):
            secs.sort(key=lambda x: int(x["name"][1:]))

    if name in table:
        path, sec = table[name]
        hit = objs.get(path)
        if not hit:
            return None
        fn, o = hit
        add(o, sec)
        return path, o, sec, fn

    m = re.match(r"^Collect (\d+) (.+)$", name)
    if not m:
        return None
    n, thing = m.group(1), _stem(m.group(2))
    for path, (fn, o) in objs.items():
        if _stem(path.rsplit("/", 1)[-1]) != thing:
            continue
        secs = o.get("sections") or []
        counted = [x for x in secs if re.fullmatch(r"x\d+", x.get("name", ""))]
        if not counted or len(counted) != len(secs):
            continue
        add(o, "x" + n, like=counted[0])
        return path, o, "x" + n, fn
    return None


def merge_rules(existing, mine):
    """AND the pack's own rule into each of ours, so reusing its object never drops its gate.

    A PopTracker rule list is an OR of comma-separated ANDs, so combining means appending the
    pack's terms to every alternative of ours - not replacing them. `mantis` on the Mantis tame is
    its tame unlock, and losing it would make the check look reachable before you can tame one.
    """
    keep = [r for r in (existing or []) if r.strip()]
    if not keep:
        return list(mine)
    if not mine or mine == [""]:
        return keep
    return [", ".join([m] + keep) if m.strip() else ", ".join(keep) for m in mine]


def retarget_rules(trees, pop):
    """Point every `@<Map>/<Object>/...` rule at wherever that object actually ended up.

    The spec is written as if each object lives under the map's own group, but one the pack
    already had is reused in place (Deathworm stays at `Dinos/Deathworm`). A rule naming a path
    that does not exist is dead, so rewrite the prefix instead of duplicating the object.
    """
    where = {}
    def index(node, path):
        if isinstance(node, list):
            for c in node:
                index(c, path)
            return
        if not isinstance(node, dict):
            return
        here = path + [node["name"]] if node.get("name") else path
        if node.get("sections"):
            where[here[-1]] = "/".join(here)
        for c in node.get("children") or []:
            index(c, here)
    for t in trees:
        index(t, [])

    fixed = [0]
    def walk(node):
        if isinstance(node, list):
            for c in node:
                walk(c)
            return
        if not isinstance(node, dict):
            return
        for holder in [node] + list(node.get("sections") or []):
            rules = holder.get("access_rules")
            if not rules:
                continue
            out = []
            for r in rules:
                for leaf, real in where.items():
                    bad = "@%s/%s/" % (pop, leaf)
                    if bad in r and real != "%s/%s" % (pop, leaf):
                        r = r.replace(bad, "@%s/" % real)
                        fixed[0] += 1
                out.append(r)
            holder["access_rules"] = out
        for c in node.get("children") or []:
            walk(c)
    for t in trees:
        walk(t)
    return fixed[0]


def sidebar_layout(entries, cols, x0, y0, step):
    """(section, sort key, payload) -> pins, each section starting on a fresh row.

    Mirrors the pack's own grid geometry, so a new map's overlay lines up with the others."""
    out, row = [], 0
    for sec in SECTION_ORDER:
        mine = sorted([e for e in entries if e[0] == sec], key=lambda e: e[1])
        if not mine:
            continue
        for i, e in enumerate(mine):
            out.append((e[2], round(x0 + step * (i % cols)), round(y0 + step * (row + i // cols))))
        row += (len(mine) + cols - 1) // cols
    return out


def ap_location_ids(load):
    """Every id the apworld can create as a location, plus the bosses the pack shows by hand.

    Rebuilt from the same data apworld/ark_ase/Locations.py reads: the location categories (bosses
    excluded there - they are the goal), creature tame/kill checks, and exploration regions.
    """
    ids = set()
    cats = load("locations.json")["location_categories"]
    for key, cat in cats.items():
        entries = cat["entries"] if isinstance(cat, dict) else cat
        ids.update(e["id"] for e in entries)             # bosses included: shown, hand-ticked
    for d in load("dinos.json").get("dinos", []):
        for k in ("tame_loc", "kill_loc"):
            if d.get(k):
                ids.add(d[k])
    for r in load("explore_areas.json")["regions"].values():
        ids.add(r["id"])
    return ids


def add_artifact_toggles(lines, pop, codes):
    """Tick the artifact_* toggle as well as the pin, the way the pack wires every artifact check.

    The Dragon & Manticore rule reads those toggles, so a pin alone would leave the boss red."""
    out = []
    for line in lines:
        m = (re.match(r'^\[(\d+)\] = \{\{"@%s/Artifact: (\w+)/' % re.escape(pop), line) or
             re.match(r'^\[(\d+)\] = \{\{"@%s/[^/"]+/Artifact of the (\w+)"' % re.escape(pop), line))
        code = "artifact_%s" % m.group(2).lower() if m else None
        if code and code in codes:
            line = line.replace("{{", '{{"%s"}, {' % code, 1)
        out.append(line)
    return out


def merge_mapping_lines(body, new_lines):
    """Fold new `[id] = {{...}}` lines into existing lines for the same id.

    Returns (body, lines still to append)."""
    rest = []
    for line in new_lines:
        m = re.match(r'^\[(\d+)\] = \{(\{.*\})\},$', line)
        if not m:
            rest.append(line)
            continue
        pat = re.compile(r"^([ \t]*\[%s\][ \t]*=[ \t]*\{)(.*)\}(,?)[ \t]*$" % m.group(1), re.M)
        hit = pat.search(body)
        if not hit:
            rest.append(line)
            continue
        merged = "%s%s, %s}%s" % (hit.group(1), hit.group(2).rstrip(), m.group(2), hit.group(3))
        body = body[:hit.start()] + merged + body[hit.end():]
    return body, rest


def _article(name):
    return "an" if name[:1].upper() in "AEIOU" else "a"


def merge_creature_objects(children, mapping, pop, logic):
    """Fold `Tame: X` / `Kill: X` pairs into one `X` object with two sections.

    Returns (new children list, rewritten mapping lines). The pack names its sections
    "Tame a Rex" / "Kill a Rex", and its own logic.lua reads those exact paths when it asks
    whether a tame is usable, so the wording has to match.
    """
    tames = logic.get("tames", {})
    kills = logic.get("kills", {})

    order, merged = [], {}
    keep = []
    for c in children:
        m = re.match(r"^(Tame|Kill): (.+)$", c["name"])
        if not m:
            keep.append(c)
            continue
        kind, who = m.group(1), m.group(2)
        if who not in merged:
            merged[who] = {
                "name": who,
                "chest_unopened_img": c["chest_unopened_img"],
                "chest_opened_img": c["chest_opened_img"],
                "map_locations": list(c["map_locations"]),
                "access_rules": [""],
                "sections": [],
            }
            order.append(who)
        obj = merged[who]
        sec = {"name": "%s %s %s" % (kind, _article(who), who),
               "access_rules": (tames if kind == "Tame" else kills).get(who, [""]) or [""],
               "item_count": 1}
        if kind == "Tame":
            sec["visibility_rules"] = [
                "$tame_sanity_%s_enabled" % re.sub(r"[^a-z0-9]+", "_", who.lower()).strip("_")]
            obj["sections"].insert(0, sec)
        else:
            obj["sections"].append(sec)

    # every mapping line that pointed at one of the old objects has to follow it
    rewritten = []
    for line in mapping:
        m = re.match(r'^\[(\d+)\] = \{\{"@%s/(Tame|Kill): ([^/]+)/[^"]*"\}\},$' % re.escape(pop),
                     line)
        if not m:
            rewritten.append(line)
            continue
        who, kind = m.group(3), m.group(2)
        rewritten.append('[%s] = {{"@%s/%s/%s %s %s"}},'
                         % (m.group(1), pop, who, kind, _article(who), who))
    return keep + [merged[w] for w in order], rewritten


def apply_rules(children, logic, pop):
    """Attach access rules to artifacts, one-off locations, and bosses."""
    arts = logic.get("artifacts", {})
    locs = logic.get("locations", {})
    combat = logic.get("boss_combat", [])
    touched = 0
    for c in children:
        name = c["name"]
        m = re.match(r"^Artifact: (.+)$", name)
        if m and m.group(1) in arts:
            c["access_rules"] = arts[m.group(1)] or [""]
            touched += 1
        elif name in locs:
            c["access_rules"] = locs[name]
            touched += 1
        elif name.startswith("Boss: ") and combat:
            c["access_rules"] = list(combat)
            touched += 1
    return touched


# --- boss row under the map. Shared by the patcher (where pins go) and the compositor (where art
# goes), so the two can never drift apart. The pack puts a boss marker at the LOWER-RIGHT of its
# portrait, size 30, so it never sits on top of the artwork; we do the same. Portraits share one
# baseline so bosses of different heights still line up, and the row is centred under the map.
BOSS_UNIT = 105                  # width per single portrait
# a pair spans three units: both creatures are wide (the Manticore is 2:1), and at two units they
# came out visibly smaller than the single bosses beside them
PAIR_UNITS = 3
BOSS_ART_TOP, BOSS_ART_BOTTOM = 762, 872
BOSS_PIN_INSET = (16, -17)       # pin: 16px in from the art box right edge, just BELOW its baseline
BOSS_MARKER_SIZE = 30
MAP_SPAN = (274, 1024)           # left/right of the map area on the canvas
# the pack draws caves as diamonds (size 20); artifact caves use the same marker
CAVE_MARKER = {"shape": "diamond", "size": 20}


def boss_row(entries):
    """[(name, units)] -> {name: (pin_x, pin_y)}, left to right, centred under the map."""
    total = sum(u for _, u in entries) * BOSS_UNIT
    cursor = MAP_SPAN[0] + (MAP_SPAN[1] - MAP_SPAN[0] - total) // 2
    out = {}
    for name, units in entries:
        right = cursor + units * BOSS_UNIT
        out[name] = (right - BOSS_PIN_INSET[0], BOSS_ART_BOTTOM - BOSS_PIN_INSET[1])
        cursor = right
    return out


def boss_box(pin_x, units):
    """The art box (left, top, right, bottom) a boss pin belongs to."""
    right = pin_x + BOSS_PIN_INSET[0]
    return right - units * BOSS_UNIT, BOSS_ART_TOP, right, BOSS_ART_BOTTOM


def group_artifacts_by_cave(children, mapping, logic, pop, to_px):
    """Pin artifacts on the MAP, one pin per cave, instead of in a grid off to the side.

    Several share a cave (Life's Labyrinth holds four within a degree of each other), and a pin
    each would sit on top of one another, so each cave is one object with a section per artifact.
    Every section keeps its own artifact's rule. The pin goes at the mean artifact position.
    Returns (children, mapping, names of the cave objects).
    """
    arts = logic.get("artifacts", {})
    caves = arts.get("_caves", {})
    where = {k: v for k, v in arts.get("_positions", {}).items() if not k.startswith("_")}
    by_name = {c["name"]: c for c in children}
    groups = {}
    for art, cave in caves.items():
        o = by_name.get("Artifact: %s" % art)
        if o is None or art not in where:
            continue
        groups.setdefault(cave, []).append((art, o))
    made, drop, rename = [], set(), {}
    for cave, members in groups.items():
        lat = sum(where[a][0] for a, _ in members) / len(members)
        lon = sum(where[a][1] for a, _ in members) / len(members)
        x, y = to_px(lat, lon)
        first = members[0][1]
        cave_obj = {
            "name": cave,
            "chest_unopened_img": first["chest_unopened_img"],
            "chest_opened_img": first["chest_opened_img"],
            "map_locations": [dict({"map": pop, "x": x, "y": y}, **CAVE_MARKER)],
            "access_rules": [""],
            "sections": [],
        }
        for art, o in sorted(members):
            sec = "Artifact of the %s" % art
            cave_obj["sections"].append({"name": sec,
                                         "access_rules": o.get("access_rules") or [""],
                                         "item_count": 1})
            drop.add(id(o))
            rename["%s/%s/" % (pop, o["name"])] = "%s/%s/%s" % (pop, cave, sec)
        made.append(cave_obj)
    children = [c for c in children if id(c) not in drop] + made
    out = []
    for line in mapping:
        for old, new in rename.items():
            if ('"@%s' % old) in line:
                line = re.sub(r'"@%s[^"]*"' % re.escape(old), '"@%s"' % new, line)
        out.append(line)
    return children, out, [c["name"] for c in made]


def pair_bosses(children, mapping, logic, pop):
    """Merge bosses that share an arena into one entry gated on the artifacts that summon them."""
    pairs = logic.get("boss_pairs", {})
    combat = logic.get("boss_combat", [])
    if not pairs:
        return children, mapping, {}
    made = {}
    for label, spec in pairs.items():
        members = ["Boss: %s" % m for m in spec["members"]]
        parts = [c for c in children if c["name"] in members]
        if not parts:
            continue
        rule = ", ".join(spec["artifacts"])
        obj = {
            "name": label,
            "chest_unopened_img": parts[0]["chest_unopened_img"],
            "chest_opened_img": parts[0]["chest_opened_img"],
            "map_locations": list(parts[0]["map_locations"]),
            "access_rules": ["%s, %s" % (rule, alt) for alt in combat] or [rule],
            "sections": [],
        }
        seen = []
        for part in parts:
            for sec in part["sections"]:
                tier = sec.get("name", "")
                if tier not in seen:
                    seen.append(tier)
        for tier in seen:
            obj["sections"].append({"name": tier, "item_count": len(parts)})
        children = [c for c in children if c["name"] not in members] + [obj]

        out = []
        for line in mapping:
            m = re.match(r'^\[(\d+)\] = \{\{"@%s/(Boss: [^/]+)/([^"]*)"\}\},$' % re.escape(pop),
                         line)
            if m and m.group(2) in members:
                out.append('[%s] = {{"@%s/%s/%s"}},' % (m.group(1), pop, label, m.group(3)))
            else:
                out.append(line)
        mapping = out
        made[label] = (spec["members"], spec["artifacts"])
    return children, mapping, made
