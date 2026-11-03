"""Generate PopTracker access rules from data/tame_logic.json, so the tracker uses the apworld's logic.

The hand-written spec in tools/reference/poptracker_logic.json had to be re-transcribed for every
map, and it borrowed the pack's own logic.lua macros by NAME - which is not the same thing: the
pack's `EnterSnow` is two fur pieces, ours is three. This module compiles our own expressions
instead, so a map's rules come straight from the data the generator uses.

Two outputs:

  * a rule spec per map, the same shape poptracker_logic_apply already consumes (tames, kills,
    locations, artifacts, boss_rules, boss_pairs, new_items). Every rule is a short call such as
    `rex, $ap_tame_rex` - the unlock code the pack already uses, ANDed with a generated function.
  * scripts/logic_ap.lua in the pack: one Lua function per rule, plus one per shared recipe node,
    compiled from the same expression grammar and resolution order as apworld/ark_ase/tame_logic.py
    and the world's _direct_nodes (Ride<X>, bare creature, boss name, count macros).

Each map run appends its rules to scripts/logic_ap_rules.json inside the pack, and the Lua is
rebuilt from that whole file, so maps added in separate runs keep their functions.

Hand entries in poptracker_logic.json still win, key by key. That keeps the Ragnarok rules Justin
approved exactly as they were, and any new override is a one-line data edit.
"""
import ast
import importlib.util
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
APWORLD = os.path.join(ROOT, "apworld", "ark_ase")
HAND_SPEC = os.path.join(ROOT, "tools", "reference", "poptracker_logic.json")
MAP_CONFIG = os.path.join(ROOT, "tools", "reference", "poptracker_maps.json")
RULES_FILE = os.path.join("scripts", "logic_ap_rules.json")
LUA_FILE = os.path.join("scripts", "logic_ap.lua")
# Checks the pack has no section for, placed on objects it does have. These are facts about the
# pack's tree, not about any one map. The pack already labels our VERY-HIGH checks "High/Max Level
# Dino", so the true level-cap checks get a distinct label.
# Our roster name -> the pack's object name, where dino_alias does not already cover it. Each one
# is the pack's own spelling, read off logic.lua's tame lists (Brontosaurus, Pachy, Paracer,
# Therizinosaur) - a rename, never a guess at a different creature.
PACK_DINO_NAMES = {
    "Bronto": "Brontosaurus", "Pachycephalosaurus": "Pachy",
    "Paraceratherium": "Paracer", "Therizinosaurus": "Therizinosaur",
}
DEFAULT_ATTACH = {
    "Tame a max-level creature": ["Milestones/Tame Creatures", "Tame a Level Cap Dino"],
    "Kill a max-level creature": ["Milestones/Kill Creatures", "Kill a Level Cap Dino"],
}


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def _apworld_parser():
    """The apworld's own expression parser, so both sides read the grammar identically."""
    spec = importlib.util.spec_from_file_location("ark_tame_logic", os.path.join(APWORLD,
                                                                                "tame_logic.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apworld_constants():
    """Literal tables the apworld keeps in code rather than data: DINO_TIER, NO_TAME_LOGIC and the
    class-level kill/collect gates. Read out of __init__.py, never copied, so they cannot drift."""
    src = open(os.path.join(APWORLD, "__init__.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    out = {}
    want_mod = {"DINO_TIER", "NO_TAME_LOGIC"}
    want_cls = {"_EXTRA_GATES", "_KILL_APEX", "_KILL_WATER_APEX", "_KILL_WATER_MID"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in want_mod | want_cls and name not in out:
                # DINO_TIER is built with {**{d: 1 for d in (...)}} - evaluate the expression alone
                out[name] = eval(compile(ast.Expression(node.value), "<apworld>", "eval"), {})
    missing = (want_mod | want_cls) - set(out)
    if missing:
        raise SystemExit("apworld/__init__.py no longer defines %s - update poptracker_rules.py"
                         % ", ".join(sorted(missing)))
    return out


def ride_key(s):
    return re.sub(r"[^a-z]", "", s.lower())


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def dino_short(d):
    return d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")


class Pack:
    """What the pack knows: item codes, AP id -> code, and where each creature's tame lives."""

    def __init__(self, pack_dir):
        self.dir = pack_dir
        self.codes = set()
        for it in json.load(open(os.path.join(pack_dir, "items", "items.json"), encoding="utf-8")):
            if isinstance(it, dict) and it.get("codes"):
                self.codes.update(x.strip() for x in str(it["codes"]).split(","))
        self.id_to_code = {}
        text = open(os.path.join(pack_dir, "scripts", "autotracking", "item_mapping.lua"),
                    encoding="utf-8").read()
        for m in re.finditer(r'^[ \t]*\[(\d+)\][ \t]*=[ \t]*\{\{"([^"]+)"', text, re.M):
            self.id_to_code.setdefault(int(m.group(1)), m.group(2))

    def tree_paths(self):
        """creature name -> '@path/Tame ...' and boss name -> ['@path', ...], from the location tree."""
        tames, bosses = {}, {}
        locdir = os.path.join(self.dir, "locations")
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
                for sec in node.get("sections") or []:
                    nm = sec.get("name", "")
                    if re.match(r"^Tame an? ", nm):
                        tames.setdefault(ride_key(here[-1]), "@%s/%s" % ("/".join(here), nm))
                if node.get("sections") and node.get("name", "").startswith("Boss: "):
                    base = re.sub(r"\s*\((Gamma|Beta|Alpha)\)\s*$", "", node["name"][6:])
                    for b in base.split(" & "):
                        bosses.setdefault(b.strip(), []).append("@" + "/".join(here))
                for c in node.get("children") or []:
                    walk(c, here)
            walk(json.load(open(os.path.join(locdir, fn), encoding="utf-8")), [])
        return tames, bosses


class RuleGen:
    def __init__(self):
        self.tl = _load("tame_logic.json")
        self.k = _apworld_constants()
        self.apmod = _apworld_parser()
        self.parse = self.apmod._parse
        self.recipes = self.tl.get("item_recipes", {})
        self.alias = self.tl.get("alias", {})
        self.count_macros = self.tl.get("count_macros", {})
        self.dinos = _load("dinos.json").get("dinos", [])
        self.locs = _load("locations.json")["location_categories"]
        self.maps = _load("maps.json").get("content", {})
        engrams = _load("engrams.json")
        self.item_ids = {e["ap_name"]: e["id"] for e in engrams["engrams"]}
        for e in engrams.get("special_items") or []:
            if e.get("ap_name") and e.get("id"):
                self.item_ids.setdefault(e["ap_name"], e["id"])
        for d in self.dinos:
            if d.get("ap_name") and d.get("id"):
                self.item_ids[d["ap_name"]] = d["id"]
        cls_to_engram = {e["engram_class"]: e["ap_name"] for e in engrams["engrams"]}
        craft = {"Smithy": ["Engram: Anvil Bench"], "Cementing Paste": ["Engram: Mortar And Pestle"],
                 "Smithy + Cementing Paste": ["Engram: Anvil Bench", "Engram: Mortar And Pestle"]}
        # same table as the world's _ride_map: key -> (creature, saddle + saddle stations)
        self.ride = {}
        for d in self.dinos:
            if not d.get("ap_name"):
                continue
            short = dino_short(d)
            saddle = cls_to_engram.get(d.get("saddle_class") or "")
            gear = ([saddle] + craft.get(d.get("saddle_craft") or "", [])) if saddle else []
            self.ride[ride_key(short)] = (short, gear)
        self.boss_names = sorted({b["name"].replace("Boss: ", "").split(" (")[0]
                                  for b in self.locs["bosses"]["entries"]})
        self.rules = {}          # function name -> tame_logic expression
        self.free_leaves = {}    # leaf that resolved to nothing -> a rule that used it
        self.new_items = {}      # code -> {"id", "name"} for AP items the pack has no code for
        self.pack = None

    # ---- items --------------------------------------------------------------------------------
    def code_for(self, ap_name):
        """Pack item code for an AP item name; registers one when the pack has none."""
        item_id = self.item_ids.get(ap_name)
        if item_id is not None and item_id in self.pack.id_to_code:
            return self.pack.id_to_code[item_id]
        for c, meta in self.new_items.items():
            if meta["id"] == item_id:
                return c
        bare = re.sub(r"^(Engram|Tame): ", "", ap_name)
        code = slug(bare)
        # The pack authored many items it never wired to an id (the Scorched creatures: `mantis`,
        # `wyvern`), and its own rules already name them. Wire that code rather than minting a
        # second item for the same thing - unless it is already some OTHER id's code.
        mapped = set(self.pack.id_to_code.values())
        if not (code in self.pack.codes and code not in mapped):
            while code in self.pack.codes or code in self.new_items:
                code += "_ap"
        self.new_items[code] = {"id": item_id, "name": ap_name.replace(": ", " - ", 1)}
        return code

    # ---- rule registration ----------------------------------------------------------------------
    def fn(self, name, expr):
        """Register a rule; returns the pack term that calls it, or '' when it needs nothing."""
        if not expr or not expr.strip():
            return ""
        self.rules[name] = expr
        return "$" + name

    def gate_term(self, loc_name):
        """The pack term for a location's collect/milestone gate (same tables as the world's
        _collect_gates), or '' when the data has none."""
        gates = {**self.k["_EXTRA_GATES"], **self.tl.get("collect_reqs", {}),
                 **self.tl.get("milestone_reqs", {})}
        expr = gates.get(loc_name, "")
        if not expr and loc_name.startswith("Killed: Alpha "):     # the world's alpha kill rule
            expr = self.tl.get("alpha_kill_reqs", {}).get(loc_name[len("Killed: "):],
                                                           self.k["_KILL_APEX"])
        return self.fn("ap_loc_%s" % slug(loc_name), expr)

    def dino_expr(self, short):
        """apworld TameLogic.dino_expr: cave override > sheet > DINO_TIER fallback."""
        cave = self.tl.get("cave_tames", {})
        if short in cave:
            return cave[short]
        sheet = self.tl.get("dino_alias", {}).get(short, short)
        raw = self.tl.get("dino_tame_raw", {})
        expr = raw.get(sheet) or raw.get(short)
        if expr and expr.strip():
            return expr
        tier = self.k["DINO_TIER"].get(short)
        if tier is None:
            tier = next((int(d["tier"]) for d in self.dinos
                         if dino_short(d) == short and d.get("tier")), 0)
        # the apworld's own tables, so the fallback can never drift from it again
        base = self.apmod._TIER_REQ.get(tier, "")
        if short in self.apmod.DEEP_WATER:
            dive = getattr(self.apmod, "_DEEP_DIVE", "DeepDive")
            base = (base + " + " + dive) if base else dive
        return base

    def tame_method(self, short):
        """Everything the world's _tame_ast requires, as one expression."""
        if short in self.k["NO_TAME_LOGIC"]:
            return ""
        expr = self.dino_expr(short)
        boss = self.tl.get("tame_requires_boss", {}).get(short)
        if boss:
            expr = "(%s) + %s" % (expr, boss) if expr else boss
        return expr

    def kill_expr(self, short, tag, map_key):
        names = [n for n in (short, self.tl.get("dino_alias", {}).get(short), tag) if n]
        over = self.tl.get("map_kill_reqs", {}).get(map_key, {})
        expr = next((over[n] for n in names if n in over), None)
        if expr is None:
            table = {ride_key(n): e for n, e in self.tl.get("kill_reqs", {}).items()}
            expr = next((table[ride_key(n)] for n in names if ride_key(n) in table), "")
        floor = self.tl.get("cave_tames", {}).get(short, "")
        if floor:
            expr = "(%s) + (%s)" % (expr, floor) if expr else floor
        return expr

    def cave_expr(self, art, map_key):
        over = self.tl.get("map_cave_reqs", {}).get(map_key, {})
        if art in over:
            return over[art]
        return self.tl.get("cave_reqs", {}).get(art, "")

    # ---- per-map spec ---------------------------------------------------------------------------
    def art_code(self, art):
        """The pack's toggle for an artifact. Case-insensitive: it spells one `artifact_Crag`."""
        want = "artifact_%s" % art.lower()
        return next((c for c in sorted(self.pack.codes) if c.lower() == want), want)

    def present(self, map_key):
        """Location ids this map carries - the apworld's fail-open map filter, the same set
        patch_poptracker_pack pins: this map's own list, plus `any`, plus anything untagged."""
        mine = set(self.maps.get(map_key, {}).get("locations") or [])
        anyset = set(self.maps.get("any", {}).get("locations") or [])
        tagged = set()
        for mk, c in self.maps.items():
            if mk != "any":
                tagged |= set(c.get("locations") or [])
        return lambda i: i in mine or i in anyset or i not in tagged

    def spec(self, map_key, pack_dir):
        self.pack = Pack(pack_dir)
        tl = self.tl
        has_loc = self.present(map_key)
        tames, kills = {}, {}
        for d in self.dinos:
            short = dino_short(d)
            if d.get("tame_loc") and has_loc(d["tame_loc"]):
                unlock = self.code_for(d["ap_name"])
                call = self.fn("ap_tame_%s" % slug(short), self.tame_method(short))
                tames[short] = [", ".join(t for t in (unlock, call) if t)]
            if d.get("kill_loc") and has_loc(d["kill_loc"]):
                expr = self.kill_expr(short, d.get("dino_tag") or "", map_key)
                name = "ap_kill_%s" % slug(short)
                if short in tl.get("map_kill_reqs", {}).get(map_key, {}):
                    name = "ap_kill_%s_%s" % (map_key, slug(short))
                kills[short] = [self.fn(name, expr)]

        locations = {}
        names = {e["id"]: e["name"] for cat in self.locs.values()
                 for e in (cat["entries"] if isinstance(cat, dict) else cat)}
        here = {nm for i, nm in names.items() if has_loc(i)}
        gates = {**self.k["_EXTRA_GATES"], **tl.get("collect_reqs", {}),
                 **tl.get("milestone_reqs", {})}
        for nm in sorted(here):
            expr = None
            if nm in gates:
                expr = gates[nm]
            elif nm.startswith("Killed: Alpha "):
                expr = tl.get("alpha_kill_reqs", {}).get(nm[len("Killed: "):], self.k["_KILL_APEX"])
            elif nm in tl.get("note_requires_tame", {}):
                cr = tl["note_requires_tame"][nm]
                call = self.fn("ap_tame_%s" % slug(cr), self.tame_method(cr))
                unlock = self.code_for("Tame: " + cr) if ("Tame: " + cr) in self.item_ids else ""
                locations[nm] = [", ".join(t for t in (unlock, call) if t)]
                continue
            else:
                dino = tl.get("tribute_dino", {}).get(nm.rsplit(" x", 1)[0])
                if dino:
                    expr = self.tame_method(dino)
            if expr is not None:
                locations[nm] = [self.fn("ap_loc_%s" % slug(nm), expr)]

        arts = {}
        for nm in sorted(here):
            if nm.startswith("Artifact: "):
                art = nm[len("Artifact: "):]
                name = "ap_art_%s_%s" % (map_key, slug(art))
                arts[art] = [self.fn(name, self.cave_expr(art, map_key))]

        # bosses: the summoning artifacts (as the pack's artifact_* toggles, the way it gates its
        # own bosses) AND everything else the world's _boss_ast asks for on this map
        boss_rules, art_sets = {}, {}
        mba = tl.get("map_boss_artifacts", {}).get(map_key, {})
        mbr = tl.get("map_boss_reqs", {}).get(map_key, {})
        for b in self.boss_names:
            if not any(nm == "Boss: " + b or nm.startswith("Boss: %s (" % b) for nm in here):
                continue
            need = mba.get(b, tl.get("boss_artifacts", {}).get(b, []))
            art_sets[b] = tuple(sorted(need))
            parts = []
            extra = mbr.get(b, mbr.get("*"))
            if extra:
                parts.append("(%s)" % extra)
            if tl.get("boss_artifacts", {}).get(b):
                parts += ["(%s)" % self.tame_method(x)
                          for x in tl.get("boss_tribute_dino", {}).get(b, []) if self.tame_method(x)]
            parts += ["%s" % x for x in tl.get("map_boss_extra", {}).get(map_key, {}).get(b, [])]
            parts += ["(%s)" % self.tame_method(x)
                      for x in tl.get("map_boss_tribute", {}).get(map_key, {}).get(b, [])
                      if self.tame_method(x)]
            if tl.get("world_boss_reqs", {}).get(b):
                parts.append("(%s)" % tl["world_boss_reqs"][b])
            prereq = tl.get("final_boss_prereqs", {}).get(b)
            if prereq is None and b == "Overseer":
                prereq = tl.get("overseer_bosses", [])
            parts += list(prereq or [])
            call = self.fn("ap_boss_%s_%s" % (map_key, slug(b)), " + ".join(parts))
            terms = [self.art_code(a) for a in need] + ([call] if call else [])
            boss_rules[b] = [", ".join(terms)] if terms else [""]

        # two bosses summoned by the very same artifacts share one arena (Ragnarok's Dragon and
        # Manticore): pair them, the shape poptracker_logic_apply.pair_bosses already handles
        pairs, seen = {}, set()
        for b, s in art_sets.items():
            if b in seen or not s:
                continue
            mates = [x for x, t in art_sets.items() if t == s and x != b and x in mba and b in mba]
            if mates:
                members = [b] + mates
                seen.update(members)
                pairs[" & ".join(members)] = {
                    "members": members, "artifacts": [self.art_code(a) for a in s],
                    "_note": "generated: these bosses are summoned by the same artifacts"}
        combat = []
        if mbr.get("*"):
            combat = [self.fn("ap_bosscombat_%s" % map_key, mbr["*"])]

        out = {"tames": tames, "kills": kills, "locations": locations, "artifacts": arts,
               "boss_rules": boss_rules, "boss_pairs": pairs, "boss_combat": combat,
               "attach": dict(DEFAULT_ATTACH), "new_items": dict(self.new_items)}
        cfg = load_map_config().get(map_key, {})
        if cfg.get("artifact_caves"):
            out["artifacts"]["_caves"] = cfg["artifact_caves"]
        if cfg.get("artifact_positions"):
            out["artifacts"]["_positions"] = cfg["artifact_positions"]
        return out

    # ---- Lua --------------------------------------------------------------------------------------
    def write_lua(self, pack_dir):
        """Merge this run's rules into the pack's rule file and compile all of it to Lua."""
        self.pack = Pack(pack_dir)
        path = os.path.join(pack_dir, RULES_FILE)
        rules = {}
        if os.path.exists(path):
            rules = json.load(open(path, encoding="utf-8"))
        rules.update(self.rules)
        tames, bosses = self.pack.tree_paths()
        # the pack names a few creatures by their short form (Stego, Sarco); dino_alias is the same
        # roster-name <-> sheet-name table the generator uses, read in both directions
        for a, b in list(self.tl.get("dino_alias", {}).items()) + list(PACK_DINO_NAMES.items()):
            for x, y in ((a, b), (b, a)):
                if ride_key(y) in tames:
                    tames.setdefault(ride_key(x), tames[ride_key(y)])
        c = _LuaCompiler(self, tames, bosses)
        bodies = {name: c.top(expr, name) for name, expr in sorted(rules.items())}
        if self.new_items:
            import poptracker_logic_apply as L
            L.add_missing_items(pack_dir, self.new_items)
        lines = [
            "-- GENERATED by tools/poptracker_rules.py from data/tame_logic.json. Do not edit:",
            "-- change the data (or tools/reference/poptracker_logic.json) and rebuild the pack.",
            "-- Mirrors the apworld's rule compiler, so a check is in logic here exactly when it is",
            "-- in logic for the generator.",
            "",
            "local function ap_reach(code)",
            "  local o = Tracker:FindObjectForCode(code)",
            "  if not o then return false end",
            "  return o.AccessibilityLevel >= 3 or o.AvailableChestCount < o.ChestCount",
            "end",
            "",
            "local function ap_cleared(code)",
            "  local o = Tracker:FindObjectForCode(code)",
            "  return o ~= nil and o.AvailableChestCount < o.ChestCount",
            "end",
            "",
            "local function ap_count(codes, n)",
            "  local got = 0",
            "  for _, c in ipairs(codes) do",
            "    if has(c) then got = got + 1 end",
            "  end",
            "  return got >= n",
            "end",
            "",
            "-- a creature is usable once its Tame check is reachable or done (the pack's own rule for",
            "-- can_use_tame); its saddle and saddle stations are ANDed in where the rule rides it",
            "AP_TAME = {",
        ]
        for k in sorted(c.used_tames):
            lines.append('  [%s] = %s,' % (json.dumps(k), json.dumps(tames[k]) if k in tames
                                           else "nil"))
        lines += ["}", "", "function ap_tame(key)", "  local p = AP_TAME[key]",
                  "  return p ~= nil and ap_reach(p)", "end", "", "AP_BOSS = {"]
        for b in sorted(c.used_bosses):
            lines.append("  [%s] = {%s}," % (json.dumps(b), ", ".join(json.dumps(p)
                                                                       for p in bosses.get(b, []))))
        lines += ["}", "", "function ap_beaten(name)", "  for _, p in ipairs(AP_BOSS[name] or {}) do",
                  "    if ap_cleared(p) then return true end", "  end", "  return false", "end", ""]
        for name in c.order:
            lines += ["function %s()" % name, "  return %s" % c.nodes[name], "end", ""]
        for name, body in bodies.items():
            lines += ["function %s()" % name, "  return %s" % body, "end", ""]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dict(sorted(rules.items())), fh, indent=2)
        with open(os.path.join(pack_dir, LUA_FILE), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        init = os.path.join(pack_dir, "scripts", "init.lua")
        text = open(init, encoding="utf-8").read()
        load = 'ScriptHost:LoadScript("scripts/logic_ap.lua")'
        if load not in text:
            anchor = 'ScriptHost:LoadScript("scripts/logic.lua")'
            text = text.replace(anchor, anchor + "\n" + load, 1) if anchor in text \
                else load + "\n" + text
            with open(init, "w", encoding="utf-8") as fh:
                fh.write(text)
        for leaf, where in c.free.items():
            self.free_leaves.setdefault(leaf, where)
        return len(rules), c


class _LuaCompiler:
    """tame_logic expression -> Lua boolean, resolving names in the apworld's order:
    count macro > direct node (Ride<X> / Tame<X> / bare creature / boss) > engram alias (+ its
    recipe) > recipe macro > nothing (a free resource node, exactly as the world treats it)."""

    def __init__(self, gen, tames, bosses):
        self.g = gen
        self.tames = tames
        self.bosses = set(gen.boss_names)
        self.nodes, self.order = {}, []
        self.used_tames, self.used_bosses = set(), set()
        self.free = {}
        self.cyclic = self._cyclic_nodes()

    def _cyclic_nodes(self):
        """Recipe nodes on a cycle. Those are inlined with the world's seen-guard instead of becoming
        functions: a Lua function calling itself back would never return."""
        graph = {}
        for n, e in self.g.recipes.items():
            graph[n] = {t.strip() for t in re.split(r"[+|()]", e or "") if t.strip()}
        cyc = set()
        for start in graph:
            stack, seen = [iter(graph.get(start, ()))], {start}
            path = [start]
            while stack:
                nxt = next(stack[-1], None)
                if nxt is None:
                    stack.pop()
                    path.pop()
                    continue
                if nxt == start:
                    cyc.add(start)
                    break
                if nxt in graph and nxt not in seen:
                    seen.add(nxt)
                    stack.append(iter(graph[nxt]))
                    path.append(nxt)
        return cyc

    def top(self, expr, owner):
        self.owner = owner
        out = self.expr(self.g.parse(expr), frozenset())
        return out

    def expr(self, node, seen):
        kind = node[0]
        if kind in ("and", "or"):
            kids = [self.expr(c, seen) for c in node[1]]
            if kind == "or":
                if "true" in kids:
                    return "true"
                kids = [k for k in kids if k != "false"] or ["false"]
            else:
                if "false" in kids:
                    return "false"
                kids = [k for k in kids if k != "true"] or ["true"]
            uniq = list(dict.fromkeys(kids))
            return uniq[0] if len(uniq) == 1 else "(%s)" % (" %s " % kind).join(uniq)
        if kind == "name":
            return self.name(node[1], seen)
        return "true"

    def name(self, nm, seen):
        g = self.g
        if nm in seen:
            return "true"
        if nm in g.count_macros:
            spec = g.count_macros[nm]
            codes = [g.code_for(x) for x in spec.get("of", [])]
            return "ap_count({%s}, %d)" % (", ".join(json.dumps(c) for c in codes),
                                           int(spec.get("n", 1)))
        key = ride_key(nm)
        for pre in ("ride", "tame", ""):
            if key.startswith(pre) and key[len(pre):] in g.ride:
                short, gear = g.ride[key[len(pre):]]
                tk = ride_key(short)
                self.used_tames.add(tk)
                terms = ['ap_tame(%s)' % json.dumps(tk)]
                if pre == "ride":
                    terms += ['has(%s)' % json.dumps(g.code_for(x)) for x in gear]
                return terms[0] if len(terms) == 1 else "(%s)" % " and ".join(terms)
        if nm in self.bosses:
            self.used_bosses.add(nm)
            return "ap_beaten(%s)" % json.dumps(nm)
        parts = []
        if nm in g.alias:
            parts.append('has(%s)' % json.dumps(g.code_for("Engram: " + g.alias[nm])))
        recipe = g.recipes.get(nm)
        if recipe and recipe.strip():
            if nm in self.cyclic:
                parts.append(self.expr(g.parse(recipe), seen | {nm}))
            else:
                parts.append(self.node_fn(nm))
        parts = [p for p in parts if p != "true"]
        if "false" in parts:
            return "false"
        if not parts:
            if nm not in g.alias and nm not in g.recipes:
                self.free.setdefault(nm, self.owner)
            return "true"
        return parts[0] if len(parts) == 1 else "(%s)" % " and ".join(parts)

    def node_fn(self, nm):
        fname = "ap_n_%s" % slug(nm)
        if fname not in self.nodes:
            self.nodes[fname] = "true"           # placeholder: recursion lands here, not forever
            body = self.expr(self.g.parse(self.g.recipes[nm]), frozenset({nm}))
            self.nodes[fname] = body
            self.order.append(fname)
        return "%s()" % fname


def load_map_config():
    if not os.path.exists(MAP_CONFIG):
        return {}
    return {k: v for k, v in json.load(open(MAP_CONFIG, encoding="utf-8")).items()
            if not k.startswith("_")}


def merge_hand(spec, map_key):
    """Overlay the hand-written spec for this map: each entry it defines replaces the generated one."""
    if not os.path.exists(HAND_SPEC):
        return spec
    hand = json.load(open(HAND_SPEC, encoding="utf-8")).get(map_key, {})
    overridden = []
    for section, val in hand.items():
        if section.startswith("_"):
            continue
        if isinstance(val, dict) and isinstance(spec.get(section), dict):
            for k, v in val.items():
                if not k.startswith("_") and k in spec[section] and spec[section][k] != v:
                    overridden.append("%s/%s" % (section, k))
                spec[section][k] = v
        else:
            if section in spec and spec[section] != val:
                overridden.append(section)
            spec[section] = val
    spec["_overridden"] = overridden
    return spec
