"""Add maps to Lurch's PopTracker pack in one command.

    python tools/poptracker_add_map.py --all
    python tools/poptracker_add_map.py center valguero --pack <unpacked pack> --out build/poptracker_pack

For each map, in order, onto the previous map's result:

  1. patch_poptracker_pack  - pins, new objects, the map's tab and grids, and the access rules,
                              which are now GENERATED from data/tame_logic.json (poptracker_rules)
  2. compose_poptracker_map - the map image: sidebar artwork + the map + the boss row

The canvas calibration is derived instead of re-run: the source image spans GPS 0-100 and compose
places it in a fixed box, so (0,0) and (100,100) land on that box's corners.

Per-map input is tools/reference/poptracker_maps.json (pack name, source image, optional artifact
caves). Last, the finished pack is VALIDATED: every `$function`, item code and `@path` a rule names
has to exist - a dead reference is silently false in PopTracker, so the check would never go green.
Only problems this run introduced are reported; anything already in the base pack is listed apart.
"""
import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compose_poptracker_map as C                                     # noqa: E402
import patch_poptracker_pack as P                                      # noqa: E402
import poptracker_logic_apply as L                                     # noqa: E402
import poptracker_rules as R                                           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXES = os.path.join(ROOT, "tools", "reference", "poptracker_pack_fixes.json")
# Artwork the pack does not ship. Files here are copied over the pack's images/ tree (same sub-
# folders) before anything else runs, so a new item or creature picks up real art instead of a
# placeholder tile, and the composed map shows it instead of a text chip.
ART = os.path.join(ROOT, "tools", "reference", "poptracker_art")


def add_art(pack):
    n = 0
    if not os.path.isdir(ART):
        return n
    for dirpath, dirs, files in os.walk(ART):
        dirs[:] = [d for d in dirs if not d.startswith("_")]     # _source/ = the raw originals
        for f in files:
            if not f.lower().endswith(".png"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), ART)
            dst = os.path.join(pack, "images", rel)
            # the pack's own art wins: ours only fills gaps (and Windows paths ignore case, so a
            # metal_sickle.png here would otherwise replace the pack's Metal_Sickle.png)
            if os.path.exists(dst):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(dirpath, f), dst)
            n += 1
    return n


def apply_pack_fixes(pack):
    """Fix known bugs in the base pack (poptracker_pack_fixes.json), in place on a copy.

    Every replace must match exactly its expected count; anything else means the pack changed and
    the fix may no longer be right, so stop and say which one rather than patch blind."""
    if not os.path.exists(FIXES):
        return 0
    spec = json.load(open(FIXES, encoding="utf-8"))
    done = 0
    # whole blocks the author commented out (the Scorched Earth tab): strip the `//` from the line
    # holding `first`, the `// {` above it, and every following commented line - exactly `lines`
    for fx in spec.get("uncomment", []):
        path = os.path.join(pack, fx["file"])
        raw = open(path, "rb").read()
        bom = raw.startswith(b"\xef\xbb\xbf")
        lines = raw.decode("utf-8-sig").split("\n")
        at = next((i for i, l in enumerate(lines) if l.strip() == fx["first"]), None)
        start = at - 1 if at else None
        end = start
        while end is not None and end < len(lines) and lines[end].lstrip().startswith("//"):
            end += 1
        if start is None or not lines[start].strip().startswith("//") or end - start != fx["lines"]:
            sys.exit("pack fix no longer matches (commented block not found or not %d lines): %s\n"
                     "  why it existed: %s" % (fx["lines"], fx["first"], fx["why"]))
        for i in range(start, end):
            lines[i] = re.sub(r"^(\s*)// ?", r"\1", lines[i], count=1)
        with open(path, "wb") as fh:
            if bom:
                fh.write(b"\xef\xbb\xbf")
            fh.write("\n".join(lines).encode("utf-8"))
        done += 1
    for fx in spec.get("replace", []):
        path = os.path.join(pack, fx["file"])
        raw = open(path, "rb").read()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        n = len(re.findall(fx["pattern"], text))
        if n != fx["count"]:
            sys.exit("pack fix no longer matches (%d hits, expected %d): %s in %s\n  why it existed: %s"
                     % (n, fx["count"], fx["pattern"], fx["file"], fx["why"]))
        text = re.sub(fx["pattern"], lambda m: fx["repl"], text)
        with open(path, "wb") as fh:
            if bom:
                fh.write(b"\xef\xbb\xbf")
            fh.write(text.encode("utf-8"))
        done += 1
    # rules that should come from our data (placeholders, or a rule that disagrees with the
    # apworld): the object's rule becomes the generated one. Matched on the exact current rule
    # (`expect`), so a rule Lurch has since changed is never overwritten blind.
    by_file = {}
    for fx in spec.get("rule_from_data", []):
        by_file.setdefault(fx["file"], []).append(fx)
    for fn, fixes in by_file.items():
        path = os.path.join(pack, fn)
        tree = json.load(open(path, encoding="utf-8"))
        found = {}
        def walk(node, here):
            if isinstance(node, list):
                for c in node:
                    walk(c, here)
                return
            if not isinstance(node, dict):
                return
            p = here + [node["name"]] if node.get("name") else here
            found["/".join(p)] = node
            for c in node.get("children") or []:
                walk(c, p)
        walk(tree, [])
        for fx in fixes:
            node = found.get(fx["object"])
            # `section` targets one section's rule instead of the object's; `item_count` gives a
            # section that only hosts an item (no chest, so no map square) a check to clear
            if node is not None and fx.get("section"):
                node = next((s for s in node.get("sections") or []
                             if s.get("name") == fx["section"]), None)
            where = fx["object"] + ("/" + fx["section"] if fx.get("section") else "")
            if node is None or node.get("access_rules") != [fx["expect"]]:
                sys.exit("pack fix no longer matches: %s is not %r any more\n  why it existed: %s"
                         % (where, [fx["expect"]], fx["why"]))
            # `rule` = a pack term to use as-is, for a gate our generator cannot express (the
            # apworld's tame-count macros are built at generation time from the slot's options)
            node["access_rules"] = [fx["rule"] if "rule" in fx
                                    else L.generator().gate_term(fx["location"]) or ""]
            if "item_count" in fx:
                if "item_count" in node:
                    sys.exit("pack fix no longer matches: %s already has an item_count\n"
                             "  why it existed: %s" % (where, fx["why"]))
                node["item_count"] = fx["item_count"]
            done += 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(tree, fh, indent=4)
    done += fix_pins(pack, spec)
    items = {k: v for k, v in spec.get("new_items", {}).items() if not k.startswith("_")}
    if items:
        L.add_missing_items(pack, items)
    return done + len(items)
def fix_pins(pack, spec):
    """Sidebar pins on the base pack's own canvases: `move_pin` moves an object's pin on one map
    (from must match exactly), `remove_pin` drops it from one map, `copy_art` paints an object's artwork from another map's canvas
    under its pin on this one (same crop geometry compose_poptracker_map uses, and only onto an
    empty tile)."""
    from PIL import Image, ImageStat
    done = 0
    trees = {}

    def find(fn, obj):
        if fn not in trees:
            trees[fn] = json.load(open(os.path.join(pack, fn), encoding="utf-8-sig"))
        hit = []
        def walk(node, here):
            if isinstance(node, list):
                for c in node:
                    walk(c, here)
                return
            p = here + [node["name"]] if node.get("name") else here
            if "/".join(p) == obj:
                hit.append(node)
            for c in node.get("children") or []:
                walk(c, p)
        walk(trees[fn], [])
        return hit[0] if hit else None

    def pin(node, mp):
        return next((m for m in (node or {}).get("map_locations") or [] if m.get("map") == mp), None)

    for fx in spec.get("move_pin", []):
        m = pin(find(fx["file"], fx["object"]), fx["map"])
        if m is None or [m["x"], m["y"]] != fx["from"]:
            sys.exit("pack fix no longer matches: %s on %s is not at %s any more\n  why it existed: %s"
                     % (fx["object"], fx["map"], fx["from"], fx["why"]))
        m["x"], m["y"] = fx["to"]
        done += 1
    # `remove_pin` takes an object off one map (the check does not exist there)
    for fx in spec.get("remove_pin", []):
        node = find(fx["file"], fx["object"])
        m = pin(node, fx["map"])
        if m is None or [m["x"], m["y"]] != fx["at"]:
            sys.exit("pack fix no longer matches: %s on %s is not at %s any more\n  why it existed: %s"
                     % (fx["object"], fx["map"], fx["at"], fx["why"]))
        node["map_locations"].remove(m)
        done += 1
    for fn, tree in trees.items():
        with open(os.path.join(pack, fn), "w", encoding="utf-8") as fh:
            json.dump(tree, fh, indent=4)

    canvases = {}
    for fx in spec.get("copy_art", []):
        node = find(fx["file"], fx["object"])
        to, src = pin(node, fx["map"]), pin(node, fx["from_map"])
        paths = [os.path.join(pack, "images", "maps", "%s.png" % mp)
                 for mp in (fx["map"], fx["from_map"])]
        if to is None or src is None or not all(os.path.exists(p) for p in paths):
            sys.exit("pack fix no longer matches: %s has no pin/canvas on %s and %s\n  why it existed: %s"
                     % (fx["object"], fx["map"], fx["from_map"], fx["why"]))
        for p in paths:
            if p not in canvases:
                canvases[p] = Image.open(p).convert("RGB")
        dst, sheet = canvases[paths[0]], canvases[paths[1]]
        lead, crop = C.ART_LEAD, C.CROP
        tx, ty = max(to["x"] - lead, 0), max(to["y"] - lead, 0)
        if ImageStat.Stat(dst.crop((tx, ty, tx + crop, ty + crop)).convert("L")).mean[0] > 8:
            sys.exit("pack fix no longer matches: %s's tile on %s already has art\n  why it existed: %s"
                     % (fx["object"], fx["map"], fx["why"]))
        sx, sy = max(src["x"] - lead, 0), max(src["y"] - lead, 0)
        dst.paste(sheet.crop((sx, sy, sx + crop, sy + crop)), (tx, ty))
        done += 1
    for p in {os.path.join(pack, "images", "maps", "%s.png" % fx["map"])
              for fx in spec.get("copy_art", [])}:
        canvases[p].save(p)
    return done


# functions archipelago.lua creates at runtime (_G["food_sanity_" .. name .. "_enabled"] = ...)
DYNAMIC_FUNCS = tuple(re.compile(r"^%s_\w+_enabled$" % p)
                      for p in ("tame_sanity", "explorer_note", "food_sanity", "death_sanity"))


def read_jsonc(path):
    """A PopTracker JSON file: tolerates a BOM, // comments and trailing commas."""
    raw = open(path, encoding="utf-8-sig").read()
    out, i, n = [], 0, len(raw)
    while i < n:                                       # drop // comments outside strings
        c = raw[i]
        if c == '"':
            j = i + 1
            while j < n and raw[j] != '"':
                j += 2 if raw[j] == "\\" else 1
            out.append(raw[i:j + 1])
            i = j + 1
        elif raw.startswith("//", i):
            k = raw.find("\n", i)
            i = k if k >= 0 else n
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[\]}])", r"\1", "".join(out)))


def add_map_switch(pack, cfg_all, base_tabs):
    """Show only the slot's maps once connected.

    Every map's top-level tab becomes its own layout key (layouts/ap_tabs.json), and every
    combination of maps gets a tiny `tracker_horizontal` listing just those tabs
    (layouts/ap_maps/<a+b>.json). scripts/ap_maps.lua registers its own Archipelago clear handler:
    on connect it reads slot_data["maps"] and loads the matching file with Tracker:AddLayouts.
    Not connected, or a slot whose maps have no tab here (Fjordur alone): every tab stays.
    Lurch's own files are untouched apart from two lines appended to init.lua.
    """
    lay_path = os.path.join(pack, "layouts", "tracker_standard.json")
    lay = read_jsonc(lay_path)
    top = lay["tracker_horizontal"]
    tabs = top["content"]["tabs"]
    by_title = {t["title"]: t for t in tabs}
    order = []                                         # (ap option, tab title), in tab order
    wanted = dict(base_tabs)
    for mk, cfg in cfg_all.items():
        if cfg.get("ap_option"):
            wanted[cfg["ap_option"]] = P.tab_title(cfg["pop"]).strip('"')
    for t in tabs:
        opt = next((o for o, title in wanted.items() if title == t["title"]), None)
        if opt:
            order.append((opt, t["title"]))
    missing = sorted(set(wanted) - {o for o, _ in order})
    if missing:
        print("map switch: no tab in the layout for %s - always shown with the rest"
              % ", ".join(missing))
    pieces = {"ap_tab_%s" % o: by_title[title]["content"] for o, title in order}
    with open(os.path.join(pack, "layouts", "ap_tabs.json"), "w", encoding="utf-8") as fh:
        json.dump(pieces, fh, indent=2)
    combo_dir = os.path.join(pack, "layouts", "ap_maps")
    os.makedirs(combo_dir, exist_ok=True)
    n = 0
    for mask in range(1, 1 << len(order)):
        chosen = [order[i] for i in range(len(order)) if mask >> i & 1]
        strip = json.loads(json.dumps(top))           # keep the wrapper exactly, swap the tabs
        strip["content"]["tabs"] = [{"title": title, "content": {"type": "layout",
                                                                  "key": "ap_tab_%s" % o}}
                                    for o, title in chosen]
        name = "+".join(o for o, _ in chosen)
        with open(os.path.join(combo_dir, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"tracker_horizontal": strip}, fh, indent=2)
        n += 1
    lua = [
        "-- GENERATED by tools/poptracker_add_map.py. On connect, show only the slot's maps.",
        "-- slot_data['maps'] holds the apworld's `maps` option names (the_island, ragnarok, ...).",
        "AP_MAP_ORDER = {%s}" % ", ".join(json.dumps(o) for o, _ in order),
        "",
        "local function ap_show_maps(slot_data)",
        "  local want = {}",
        "  local maps = slot_data and slot_data['maps']",
        "  if type(maps) ~= 'table' then return end",
        "  for _, m in pairs(maps) do want[m] = true end",
        "  local keys = {}",
        "  for _, m in ipairs(AP_MAP_ORDER) do",
        "    if want[m] then table.insert(keys, m) end",
        "  end",
        "  if #keys == 0 then return end   -- none of the slot's maps has a tab: keep them all",
        "  Tracker:AddLayouts('layouts/ap_maps/' .. table.concat(keys, '+') .. '.json')",
        "end",
        "",
        "if Archipelago then",
        "  Archipelago:AddClearHandler('ap map tabs', ap_show_maps)",
        "end",
        "",
    ]
    with open(os.path.join(pack, "scripts", "ap_maps.lua"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lua))
    init = os.path.join(pack, "scripts", "init.lua")
    text = open(init, encoding="utf-8").read()
    for line in ('Tracker:AddLayouts("layouts/ap_tabs.json")',
                 'ScriptHost:LoadScript("scripts/ap_maps.lua")'):
        if line not in text:
            text = text.rstrip() + "\n" + line + "\n"
    with open(init, "w", encoding="utf-8") as fh:
        fh.write(text)
    return [t for _, t in order], n


SEED_MARKER = "ap_seed_marker"


def add_new_slot_reset(pack):
    """Start clean when connecting to a different seed or slot.

    Lurch's onClear only resets what item_mapping / location_mapping name, so anything else (manual
    clicks, the Level counter, derived toggles, sections with no AP id) carried over from the last
    session, even onto a brand-new server. scripts/ap_reset.lua resets EVERY toggle, consumable and
    location section, but only when a hash of Archipelago.Seed + slot differs from the one stored in
    a hidden consumable (PopTracker saves item state, so it survives restarts). Reconnecting to the
    same slot keeps the old behaviour. onClear calls it first (pack fix), before the slot settings
    and the server's own item/location replay are applied."""
    items_path = os.path.join(pack, "items", "items.json")
    items = json.load(open(items_path, encoding="utf-8-sig"))
    if not any(isinstance(i, dict) and i.get("codes") == SEED_MARKER for i in items):
        items.append({"name": "AP Seed Marker", "type": "consumable", "codes": SEED_MARKER,
                      "min_quantity": 0, "max_quantity": 999999, "initial_quantity": 0,
                      "img": "images/settings/op_DM.png"})
        with open(items_path, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=4)
    reset_items = []
    for it in items:
        if not isinstance(it, dict) or it.get("codes") == SEED_MARKER:
            continue
        code = it.get("codes", "").split(",")[0].strip()
        if not code:
            continue
        if it.get("type") in ("toggle", "toggle_badged"):
            reset_items.append((code, "toggle", 0))
        elif it.get("type") == "consumable":
            reset_items.append((code, "consumable",
                                it.get("initial_quantity", it.get("min_quantity", 0)) or 0))
    sections = []
    locdir = os.path.join(pack, "locations")
    for fn in sorted(os.listdir(locdir)):
        if not fn.endswith(".json"):
            continue
        def walk(node, here):
            if isinstance(node, list):
                for c in node:
                    walk(c, here)
                return
            if not isinstance(node, dict) or not node.get("name"):
                return
            p = here + [node["name"]]
            for s in node.get("sections") or []:
                # an unnamed section (the generated Visit <region> objects) is "@<path>/"
                sections.append("@" + "/".join(p + [s.get("name") or ""]))
            for c in node.get("children") or []:
                walk(c, p)
        walk(json.load(open(os.path.join(locdir, fn), encoding="utf-8-sig")), [])
    sections = sorted(set(sections))

    def q(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lua = ["-- generated by tools/poptracker_add_map.py (add_new_slot_reset); do not edit",
           "AP_RESET_ITEMS = {"]
    lua += ["  {%s, %s, %d}," % (q(c), q(t), v) for c, t, v in reset_items]
    lua += ["}", "AP_RESET_LOCS = {"]
    lua += ["  %s," % q(s) for s in sections]
    lua += ["}", "",
            "local function ap_slot_hash()",
            "  if not Archipelago or Archipelago.Seed == nil then return nil end",
            "  local key = tostring(Archipelago.Seed) .. '|' .. tostring(Archipelago.TeamNumber)"
            " .. '|' .. tostring(Archipelago.PlayerNumber)",
            "  local h = 0",
            "  for i = 1, #key do h = (h * 31 + string.byte(key, i)) % 999983 end",
            "  return h + 1   -- never 0, the marker's starting value",
            "end",
            "",
            "-- called first thing in onClear: a different seed/slot than last time wipes everything",
            "function ap_new_slot_reset()",
            "  local h = ap_slot_hash()",
            "  local marker = Tracker:FindObjectForCode('%s')" % SEED_MARKER,
            "  if h == nil or not marker or marker.AcquiredCount == h then return end",
            "  for _, e in ipairs(AP_RESET_ITEMS) do",
            "    local obj = Tracker:FindObjectForCode(e[1])",
            "    if obj then",
            "      if e[2] == 'toggle' then obj.Active = false else obj.AcquiredCount = e[3] end",
            "    end",
            "  end",
            "  for _, code in ipairs(AP_RESET_LOCS) do",
            "    local obj = Tracker:FindObjectForCode(code)",
            "    if obj then",
            "      obj.AvailableChestCount = obj.ChestCount",
            "      if obj.Highlight then obj.Highlight = Highlight.None end",
            "    end",
            "  end",
            "  marker.AcquiredCount = h",
            "  print('ArkAP tracker: new seed/slot - cleared the previous session')",
            "end",
            ""]
    with open(os.path.join(pack, "scripts", "ap_reset.lua"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lua))
    init = os.path.join(pack, "scripts", "init.lua")
    text = open(init, encoding="utf-8").read()
    line = 'ScriptHost:LoadScript("scripts/ap_reset.lua")'
    if line not in text:
        text = text.rstrip() + "\n" + line + "\n"
    with open(init, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(reset_items), len(sections)


def validate(pack):
    """Every dead reference in the pack's rules, as a set of (kind, ref, where) tuples."""
    codes = set()
    for it in json.load(open(os.path.join(pack, "items", "items.json"), encoding="utf-8")):
        if isinstance(it, dict) and it.get("codes"):
            codes.update(x.strip() for x in str(it["codes"]).split(","))
    funcs = set()
    for dirpath, _, files in os.walk(os.path.join(pack, "scripts")):
        for f in files:
            if f.endswith(".lua"):
                text = open(os.path.join(dirpath, f), encoding="utf-8", errors="replace").read()
                funcs.update(re.findall(r"function\s+([A-Za-z_][\w.]*)\s*\(", text))
    paths, sec_paths, rules = set(), set(), []
    locdir = os.path.join(pack, "locations")
    for fn in sorted(os.listdir(locdir)):
        if not fn.endswith(".json"):
            continue
        def walk(node, path):
            if isinstance(node, list):
                for c in node:
                    walk(c, path)
                return
            if not isinstance(node, dict):
                return
            here = path + [node["name"]] if node.get("name") else path
            p = "/".join(here)
            paths.add(p)
            for key in ("access_rules", "visibility_rules"):
                for r in node.get(key) or []:
                    rules.append((r, p))
            for sec in node.get("sections") or []:
                sp = "%s/%s" % (p, sec.get("name", ""))
                paths.add(sp)
                sec_paths.add(sp)
                for key in ("access_rules", "visibility_rules"):
                    for r in sec.get(key) or []:
                        rules.append((r, sp))
            for c in node.get("children") or []:
                walk(c, here)
        walk(json.load(open(os.path.join(locdir, fn), encoding="utf-8")), [])
    bad = set()
    for rule, where in rules:
        for term in rule.split(","):
            t = term.strip().strip("[]").strip()
            if not t:
                continue
            if t.startswith("$"):
                f = t[1:].split("|")[0]
                if f not in funcs and not any(rx.match(f) for rx in DYNAMIC_FUNCS):
                    bad.add(("function", f, where))
            elif t.startswith("@"):
                if t[1:] not in paths:
                    bad.add(("path", t[1:], where))
            elif t.split(":")[0] not in codes:
                bad.add(("item code", t.split(":")[0], where))
    # every "@" code in location_mapping.lua must name a SECTION ("@<path>/<section>", "@<path>/"
    # for an unnamed one). A Location there resolves too, but has no AvailableChestCount: Lurch's
    # onClear raises on it and skips applying slot_data, and onLocation can never clear it
    lm = os.path.join(pack, "scripts", "autotracking", "location_mapping.lua")
    for line in open(lm, encoding="utf-8").read().splitlines():
        if not line.strip().startswith("--"):
            for code in re.findall(r'"@((?:[^"\\]|\\.)*)"', line):
                if code.replace('\\"', '"') not in sec_paths:
                    bad.add(("mapping not a section", code, "location_mapping.lua"))
    # the layout: parses (JSONC: // comments and trailing commas allowed), and every map a tab
    # shows and every layout key it names exists - an uncommented or generated tab that points at
    # nothing just renders empty
    map_names = {m["name"] for m in json.load(open(os.path.join(pack, "maps", "maps.json"),
                                                   encoding="utf-8-sig"))}
    # every layout file, subfolders too; a key may be defined in one file and used in another
    # (the map-switch files point at ap_tabs.json, which points at the grids in the main file)
    layouts, keys = {}, set()
    for dirpath, _, files in os.walk(os.path.join(pack, "layouts")):
        for lf in files:
            rel = os.path.relpath(os.path.join(dirpath, lf), pack).replace("\\", "/")
            try:
                layouts[rel] = read_jsonc(os.path.join(dirpath, lf))
                keys |= set(layouts[rel])
            except ValueError as e:
                bad.add(("layout", "does not parse: %s" % e, rel))
    for rel, lay in layouts.items():
        def walk_lay(node):
            if isinstance(node, list):
                for c in node:
                    walk_lay(c)
            elif isinstance(node, dict):
                for m in node.get("maps") or []:
                    if m not in map_names:
                        bad.add(("map", m, rel))
                if node.get("type") == "layout" and node.get("key") not in keys:
                    bad.add(("layout key", node.get("key"), rel))
                for v in node.values():
                    walk_lay(v)
        walk_lay(lay)
    # the generated Lua: every code it tests, every function it calls, balanced parentheses (no
    # Lua interpreter is assumed, so this is the structural check that stands in for a parse)
    lua_path = os.path.join(pack, "scripts", "logic_ap.lua")
    if os.path.exists(lua_path):
        lua = open(lua_path, encoding="utf-8").read()
        for m in re.finditer(r"^function (\w+)\(\)\n  return (.*)\nend$", lua, re.M):
            name, body = m.group(1), m.group(2)
            depth = 0
            for ch in body:
                depth += ch == "("
                depth -= ch == ")"
                if depth < 0:
                    break
            if depth != 0:
                bad.add(("lua parens", name, "logic_ap.lua"))
            for code in re.findall(r'has\("([^"]+)"\)', body):
                if code not in codes:
                    bad.add(("item code", code, "logic_ap.lua " + name))
            for lst in re.findall(r"ap_count\(\{([^}]*)\}", body):
                for code in re.findall(r'"([^"]+)"', lst):
                    if code not in codes:
                        bad.add(("item code", code, "logic_ap.lua " + name))
            for f in re.findall(r"\b(ap_\w+)\(", body):
                if f not in funcs and f not in ("ap_reach", "ap_cleared", "ap_count"):
                    bad.add(("function", f, "logic_ap.lua " + name))
    return bad


def main():
    cfg_all = R.load_map_config()
    base_default = json.load(open(R.MAP_CONFIG, encoding="utf-8")).get("_base_pack")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("maps", nargs="*", help="map keys from poptracker_maps.json, applied in order")
    ap.add_argument("--all", action="store_true", help="every map in poptracker_maps.json")
    ap.add_argument("--pack", default=base_default, help="the unpacked pack to start from")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "poptracker_pack"))
    args = ap.parse_args()
    maps = list(cfg_all) if args.all else args.maps
    if not maps:
        ap.error("name at least one map, or pass --all")
    unknown = [m for m in maps if m not in cfg_all]
    if unknown:
        sys.exit("not in tools/reference/poptracker_maps.json: %s" % ", ".join(unknown))
    if not args.pack or not os.path.isdir(args.pack):
        sys.exit("base pack not found: %r (pass --pack)" % args.pack)

    l, t, r, b = C.MAP_BOX
    canvas = "%dx%d" % C.CANVAS
    stage_root = os.path.join(ROOT, "build", "_poptracker_stages")
    if os.path.exists(stage_root):
        shutil.rmtree(stage_root)
    # stage 0: the base pack with its known bugs fixed; every map builds on top of this
    prev = os.path.join(stage_root, "0_fixes")
    shutil.copytree(args.pack, prev)
    print("added %d images from tools/reference/poptracker_art" % add_art(prev))
    print("applied %d fixes to the base pack (tools/reference/poptracker_pack_fixes.json)"
          % apply_pack_fixes(prev))
    for i, mk in enumerate(maps, 1):
        cfg = cfg_all[mk]
        pop = cfg["pop"]
        src = os.path.join(ROOT, cfg["source"])
        if not os.path.exists(src):
            sys.exit("%s: source image %s does not exist" % (mk, src))
        out = os.path.join(stage_root, "%d_%s" % (i, mk))
        print("\n================ %s (%s) ================" % (mk, pop))
        P.main([mk, "--pack", prev, "--out", out, "--pop-name", pop,
                "--image", "images/maps/%s.png" % pop, "--size", canvas,
                "--calib", "0,0,%d,%d" % (l, t), "--calib", "100,100,%d,%d" % (r, b)])
        C.main([mk, "--source", src, "--pack", out, "--pop-name", pop,
                "--out", os.path.join(out, "images", "maps", "%s.png" % pop)])
        # the canvas calibration was already applied above; compose's own reminder does not apply
        prev = out

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    shutil.copytree(prev, args.out)
    shutil.rmtree(stage_root)
    base_tabs = {k: v for k, v in json.load(open(R.MAP_CONFIG, encoding="utf-8"))
                 .get("_base_tabs", {}).items() if not k.startswith("_")}
    shown, combos = add_map_switch(args.out, cfg_all, base_tabs)
    print("\nmap switch: on connect only the slot's maps show (%s; %d combinations)"
          % (", ".join(shown), combos))
    n_items, n_secs = add_new_slot_reset(args.out)
    print("new-slot reset: a different seed/slot clears %d items and %d location sections"
          % (n_items, n_secs))

    print("\n================ validation ================")
    before, after = validate(args.pack), validate(args.out)
    new = sorted(after - before)
    if new:
        print("%d dead references introduced by this run:" % len(new))
        for kind, ref, where in new:
            print("  %-9s %-40s in %s" % (kind, ref, where))
    else:
        print("no dead references introduced (every $function, item code and @path resolves)")
    if before - after:
        print("%d dead references in the base pack fixed" % len(before - after))
    if before & after:
        print("%d dead references still in the base pack:" % len(before & after))
        for kind, ref, where in sorted(before & after):
            print("  %-9s %-40s in %s" % (kind, ref, where))
    gen = L.generator()
    missing_tames = []
    lua = open(os.path.join(args.out, R.LUA_FILE), encoding="utf-8").read()
    for m in re.finditer(r'^\s*\[("[^"]+")\] = nil,', lua, re.M):
        missing_tames.append(json.loads(m.group(1)))
    if missing_tames:
        print("creatures a rule rides but the pack has no Tame check for (always false): %s"
              % ", ".join(missing_tames))
    if gen.free_leaves:
        print("tokens that resolve to NO requirement, exactly as the apworld treats them:")
        for leaf, owner in sorted(gen.free_leaves.items()):
            print("  %-28s (first seen in %s)" % (leaf, owner))
    # artwork still missing: placeholder tiles we drew, and image paths the pack names but lacks
    from PIL import Image
    need = []
    for it in json.load(open(os.path.join(args.out, "items", "items.json"), encoding="utf-8")):
        if not isinstance(it, dict) or not it.get("img"):
            continue
        p = os.path.join(args.out, *it["img"].split("/"))
        if not os.path.exists(p):
            need.append("%s  (pack names %s, file missing)" % (it.get("name"), it["img"]))
            continue
        im = Image.open(p).convert("RGBA")
        if im.size == (64, 64) and im.getpixel((10, 10))[:3] == (34, 34, 40):
            need.append("%s  -> %s" % (it.get("name"), it["img"]))
    if need:
        print("art still missing (drop PNGs in tools/reference/poptracker_art/, same subfolder "
              "and name):")
        for n in need:
            print("  " + n)
    print("\npack -> %s" % args.out)
    print("Still manual: bump package_version in manifest.json, zip, ship.")


if __name__ == "__main__":
    main()
