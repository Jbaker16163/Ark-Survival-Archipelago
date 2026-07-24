"""ARK: Survival Evolved world for Archipelago.

Vertical-slice. Items = engram unlocks + taming/supply specials (+ filler).
Locations = dossiers/explorer notes + bosses + milestones. IDs come from the shared
data/ files so a generated game matches the in-game ArkServerApi plugin exactly.

Install: drop the `ark_ase` folder into Archipelago `worlds/`, or zip its contents to
`ark_ase.apworld` and install via the launcher.
"""
from typing import Dict

from BaseClasses import Item, ItemClassification, LocationProgressType, Region, Tutorial
from worlds.AutoWorld import World, WebWorld
from worlds.generic.Rules import add_rule

from .data import (load_engram_data, load_location_data, load_dino_data, load_crate_data,
                   load_filler_data, load_tek_data, load_spawn_class_data,
                   load_spawn_container_data, load_tame_logic_data)
from .Items import (ArkItem, build_item_table, FILLER_NAME, FILLER_ID,
                    STRUCTURE_BUNDLES, structure_bundle_members)
from .Locations import ArkLocation, build_location_table
from .Options import ArkASAOptions, StationPlacement
from .tame_logic import TameLogic, eval_ast

GAME = "ARK Survival Evolved"

# weak, sphere-1 dinos whose first-kill checks become PRIORITY when early_dino_checks is on
# (so AP fill drops progression - e.g. another game's early item - onto them).
EARLY_DINO_SHORTS = (
    "Dodo", "Parasaur", "Triceratops", "Dilophosaur",
    "Phiomia", "Lystrosaurus", "Compsognathus", "Dimorphodon",
)

# tames kept as items/checks but EXCLUDED from the access LOGIC: creatures whose real taming
# requirement can't be modelled, so no rule we write would be honest. Their "Tamed: X" check gets no
# combat rule and holds filler only; the Tame item + check still exist.
#   Electrophorus - passive underwater eel, unrideable, no combat taming method.
#   Titanoboa     - PASSIVE tame that needs a FERTILIZED EGG, i.e. breeding, which we don't model
#                   (same reason the breed-count milestones are filler-only). It was previously
#                   given a bogus "Crossbow KO + Gas Mask" cave floor.
NO_TAME_LOGIC = {"Electrophorus", "Titanoboa"}

# progression_tiers: station engrams gate 4 tiers. Tier i -> i+1 needs ALL of TIER_GATES[i].
# T1 = Smithy (Anvil Bench) + Mortar And Pestle (narcotics/paste = the real early-craft spine).
TIER_GATES = (
    ("Engram: Anvil Bench", "Engram: Mortar And Pestle"),   # T0 -> T1
    ("Engram: Forge",),                                     # T1 -> T2
    ("Engram: Fabricator",),                                # T2 -> T3
)
GATE_ENGRAMS = tuple(g for gates in TIER_GATES for g in gates)   # flat (classification/early modes)

# dino -> tier for kill/tame checks (unlisted = Tier 0). Reviewed/approved tier table.
DINO_TIER = {
    **{d: 1 for d in (
        "Ankylosaurus", "Araneo", "Arthropleura", "Beelzebufo", "Castoroides",
        "Chalicotherium", "Direwolf", "Doedicurus", "Equus", "Hyaenodon", "Ichthyosaurus",
        "Kentrosaurus", "Manta", "Pachyrhinosaurus", "Pteranodon", "Pulmonoscorpius", "Purlovia",
        "Raptor", "Sabertooth", "Stegosaurus", "Terror Bird", "Triceratops",
        "Woolly Rhino",
        "Piranha", "Leech", "Giant Bee", "Yeti")},  # kill-only Piranha/Leech/Yeti + tameable Giant Bee (Coelacanth/Trilobite/Onyc = T0)
    **{d: 2 for d in (
        "Acrocanthosaurus", "Allosaurus", "Argentavis", "Baryonyx", "Basilosaurus", "Bronto",
        "Carno", "Daeodon", "Direbear", "Dunkle", "Electrophorus", "Gigantopithecus", "Kaprosuchus",
        "Mammoth", "Megalania", "Megalodon", "Megalosaurus", "Megatherium",
        "Paraceratherium", "Pelagornis", "Plesiosaur", "Sarcosuchus", "Tapejara", "Thylacoleo",
        "Titanoboa")},
    **{d: 3 for d in (
        "Angler", "Giganotosaurus", "Leedsichthys", "Liopleurodon", "Mosasaur", "Quetzal", "Rex",
        "Spino", "Therizinosaurus", "Titanosaur", "Tusoteuthis", "Yutyrannus",
        "Ammonite", "Eurypterid", "Jellyfish",     # untameable deep-ocean kill-only
        "Rhyniognatha", "Carcharodontosaurus")},   # endgame tameable
    # alpha-predator kill checks (locations.json alpha_kills; "Killed: Alpha X")
    "Alpha Raptor": 2, "Alpha Carno": 2,
    **{d: 3 for d in ("Alpha Rex", "Alpha Megalodon", "Alpha Leedsichthys",
                      "Alpha Mosasaur", "Alpha Tusoteuthis")},
}


class ArkASAWeb(WebWorld):
    tutorials = [Tutorial(
        "Setup Guide", "How to set up ARK: Survival Evolved for Archipelago.",
        "English", "setup_en.md", "setup/en", ["you"],
    )]


class ArkASAWorld(World):
    """ARK: Survival Evolved - engrams, taming, and dossiers as Archipelago items/checks."""

    game = GAME
    web = ArkASAWeb()
    options_dataclass = ArkASAOptions
    options: ArkASAOptions

    _engrams = load_engram_data()
    _locations = load_location_data()
    _dinos = load_dino_data()
    _crates = load_crate_data()
    _filler = load_filler_data()
    _spawn_classes = load_spawn_class_data().get("spawn_classes", [])
    _spawn_containers = load_spawn_container_data().get("spawn_containers", [])
    _tame_logic_data = load_tame_logic_data()
    # Tek engrams: never in the AP pool - the plugin grants each boss's set on its first kill.
    _tek_names = {n for grants in load_tek_data().get("grants", {}).values() for n in grants}
    _filler_names = {f["ap_name"] for f in _filler.get("filler", [])} | {FILLER_NAME}
    _tame_item_names = {d["ap_name"] for d in _dinos.get("dinos", []) if d.get("ap_name")}
    _good_filler = [f["ap_name"] for f in _filler.get("filler", []) if not f.get("trap")]
    item_name_to_id: Dict[str, int] = build_item_table(_engrams, _dinos, _crates, _filler)
    location_name_to_id: Dict[str, int] = build_location_table(_locations, _dinos)

    # classify: only items that actually GATE logic are progression.
    #   progression = station gates (tiers) + tame unlocks (they gate "Tamed: X" via lock_taming)
    #   filler      = traps / bonus resources
    #   useful      = everything else (saddles, non-gating engrams, crate access, ...) - nice, not required
    def create_item(self, name: str) -> Item:
        no_logic = {"Tame: " + d for d in NO_TAME_LOGIC}
        if name in self._filler_names:
            cls = ItemClassification.filler
        elif name in self._tame_required_items():      # engrams that GATE a tame -> must be progression
            cls = ItemClassification.progression
        elif self.options.lock_taming.value and name in self._tame_item_names and name not in no_logic:
            cls = ItemClassification.progression
        else:                                          # useful: saddles, non-gating engrams, NO_TAME_LOGIC tames
            cls = ItemClassification.useful
        return ArkItem(name, cls, self.item_name_to_id[name], self.player)

    def get_filler_item_name(self) -> str:
        return self.random.choice(self._good_filler) if self._good_filler else FILLER_NAME

    # tame_sanity / food_sanity: deterministic per-seed sample of location NAMES to drop.
    # Cached so every _used_locations() call sees the same roll.
    def _sanity_excluded(self) -> set:
        cached = getattr(self, "_sanity_excluded_cache", None)
        if cached is not None:
            return cached
        excluded: set = set()
        pct = self.options.tame_sanity.value
        if pct < 100:
            tames = sorted("Tamed: " + (d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", ""))
                           for d in self._dinos.get("dinos", []) if d.get("tame_loc"))
            keep = round(len(tames) * pct / 100)
            excluded |= set(tames) - set(self.random.sample(tames, keep))
        pct = self.options.food_sanity.value
        if pct < 100:
            foods = sorted(e["name"] for e in
                           self._locations["location_categories"]["inventory_checks"]["entries"]
                           if e.get("food"))
            keep = round(len(foods) * pct / 100)
            excluded |= set(foods) - set(self.random.sample(foods, keep))
        self._sanity_excluded_cache = excluded
        return excluded

    # locations actually used by THIS slot: first N dossiers (option) + all bosses + milestones.
    # The class-level location_name_to_id keeps the full set for the datapackage; unused ones
    # just hold no item (the plugin may still report them, AP harmlessly ignores).
    def _used_locations(self) -> Dict[str, int]:
        cats = self._locations["location_categories"]
        used: Dict[str, int] = {}
        for e in cats["dossiers"]["entries"][: self.options.dossier_checks.value]:
            used[e["name"]] = e["id"]
        # NOTE: "bosses" is intentionally NOT here - boss kills are the goal (via boss_out.jsonl),
        # not item-bearing checks, so nothing gets stranded behind a boss kill.
        for key in ("milestones", "levels", "alpha_kills", "inventory_checks"):
            for e in cats.get(key, {}).get("entries", []):
                used[e["name"]] = e["id"]
        for d in self._dinos.get("dinos", []):           # per-dino tame + kill checks
            short = d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")
            if d.get("tame_loc"):
                used["Tamed: " + short] = d["tame_loc"]
            if d.get("kill_loc"):
                used["Killed: " + short] = d["kill_loc"]
        for name in self._sanity_excluded():             # tame_sanity / food_sanity drops
            used.pop(name, None)
        return used

    # saddle engram ap_names removed from the pool when bundle_saddles is on (granted with the tame).
    def _bundled_saddle_names(self) -> set:
        if not self.options.bundle_saddles.value:
            return set()
        saddle_classes = {d["saddle_class"] for d in self._dinos.get("dinos", []) if d.get("saddle_class")}
        return {e["ap_name"] for e in self._engrams["engrams"] if e["engram_class"] in saddle_classes}

    # engrams granted free at start (the engrams.json "starter_engrams" set) -> removed from the pool.
    def _free_starter_names(self) -> set:
        if not self.options.free_starter_engrams.value:
            return set()
        return set(self._engrams.get("starter_engrams", []))

    def create_items(self) -> None:
        pool = []
        skip = self._bundled_saddle_names() | self._free_starter_names() | self._tek_names
        bundles = structure_bundle_members(self._engrams) if self.options.bundle_structures.value else {}
        for members in bundles.values():                 # bundled structure engrams leave the pool
            skip |= members
        skip_bundle_items = set() if bundles else set(STRUCTURE_BUNDLES)   # bundle items only when on
        # (player-picked starting items: use the standard start_inventory_from_pool yaml option -
        #  AP core precollects them + swaps filler into the pool.)
        # one of each progression item (skip every filler/trap entry + any bundled saddles)
        for name in self.item_name_to_id:
            if name in self._filler_names or name in skip or name in skip_bundle_items:
                continue
            pool.append(self.create_item(name))
        # pad to location count with a mix of traps and neutral filler (trap_percentage).
        n_locations = len(self._used_locations())
        if len(pool) > n_locations:
            raise ValueError(
                f"ARK: {len(pool)} items > {n_locations} locations. Raise dossier_checks / "
                f"tame_sanity / food_sanity, or shrink the pool (bundle_structures, "
                f"bundle_saddles, free_starter_engrams) in the yaml.")
        traps = [f["ap_name"] for f in self._filler.get("filler", []) if f.get("trap")]
        goods = [f["ap_name"] for f in self._filler.get("filler", []) if not f.get("trap")] or [FILLER_NAME]
        pct = self.options.trap_percentage.value
        while len(pool) < n_locations:
            use_trap = traps and self.random.randint(1, 100) <= pct
            pool.append(self.create_item(self.random.choice(traps if use_trap else goods)))
        self.multiworld.itempool += pool

    # boss-defeat goal: "Broodmother Defeated" etc, derived from the boss location names.
    def _boss_events(self) -> Dict[str, str]:
        events: Dict[str, str] = {}
        for b in self._locations["location_categories"]["bosses"]["entries"]:
            short = b["name"].replace("Boss: ", "").split(" (")[0]   # "Broodmother"
            events[short + " Defeated"] = b["name"]
        return events

    # real note location names (Dossier: X / Helena Note #N / Hologram: X / etc - NOT the generic
    # "Explorer Note N" placeholder, which stopped matching anything after the Island rebalance).
    def _is_note(self, loc_name: str) -> bool:
        names = getattr(self, "_note_names_cache", None)
        if names is None:
            names = {e["name"] for e in self._locations["location_categories"]["dossiers"]["entries"]}
            self._note_names_cache = names
        return loc_name in names

    # under early_dino_checks: which checks may still hold progression (instantly reachable).
    # = the 8 priority kills + explorer notes + low level milestones (Reach Level <= 40).
    EARLY_MAX_LEVEL = 40

    def _early_eligible(self, loc_name: str, early: set) -> bool:
        if loc_name in early or self._is_note(loc_name):
            return True
        if loc_name.startswith("Reach Level "):
            try:
                return int(loc_name.rsplit(" ", 1)[1]) <= self.EARLY_MAX_LEVEL
            except ValueError:
                return False
        return False

    # per-dino Tier-0 overrides from the yaml (add forces T0, remove bumps a default-T0 to T1).
    def _dino_tier(self, short: str) -> int:
        if short in self.options.tier0_add.value:
            return 0
        base = DINO_TIER.get(short, 0)
        if base == 0 and short in self.options.tier0_remove.value:
            return 1
        return base

    # ---- tame/craft ACCESS LOGIC (softlock prevention) ----
    # A "Tamed: X" check requires the engrams X's taming method needs (from data/tame_logic.json,
    # expanded through the recipe graph). Dinos the sheet doesn't cover fall back to DINO_TIER.
    def _tame(self):
        tl = getattr(self, "_tame_cache", "?")
        if tl == "?":
            tl = TameLogic(self._tame_logic_data) if self._tame_logic_data.get("dino_tame_raw") else None
            self._tame_cache = tl
        return tl

    @staticmethod
    def _dino_short(d: dict) -> str:
        return d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")

    # engram ap_name -> its bundle item name when bundle_structures removes it from the pool
    # (else the rule's has(<engram>) would be unsatisfiable). Cached per world.
    def _bundle_remap(self):
        m = getattr(self, "_bundle_remap_cache", None)
        if m is None:
            m = {}
            if self.options.bundle_structures.value:
                for bundle, members in structure_bundle_members(self._engrams).items():
                    for mem in members:
                        m[mem] = bundle
            self._bundle_remap_cache = m
        return lambda short: m.get("Engram: " + short, "Engram: " + short)

    # ap_item_names that are auto-granted (never in the pool) -> tame logic treats has(x) as always
    # true for them, else a requirement on a start engram (e.g. Waterskin) would strand the location.
    def _free_items(self) -> set:
        m = getattr(self, "_free_items_cache", None)
        if m is None:
            m = self._free_starter_names() | self._bundled_saddle_names()
            self._free_items_cache = m
        return m

    # AST rule for taming a roster dino ('true' = no requirement).
    def _tame_ast(self, short: str):
        tl = self._tame()
        if not tl:
            return ("true",)
        return tl.compile(tl.dino_expr(short, self._dino_tier(short)), self._bundle_remap(),
                          self._free_items())

    def _compile_expr(self, expr: str):
        tl = self._tame()
        return tl.compile(expr, self._bundle_remap(), self._free_items()) if tl else ("true",)

    # ---- KILL gating (realism): water creatures need diving gear, apex predators a real weapon ----
    # Reuses spawn_classes.json (habitat/danger). A tiny manual map fixes the few creatures the two
    # shorthand systems don't join on, or that are missing/mis-tagged there.
    _KILL_HD_ALIAS = {"Mosasaur": "Mosa", "Therizinosaurus": "Therizino"}  # roster short -> spawn name
    _KILL_HD_FORCE = {"Titanosaur": ("land", "apex"),          # absent from spawn_classes
                      "Basilosaurus": ("water", "apex"),       # deep-ocean but tagged docile
                      "Unicorn": ("land", "apex"),             # trivial to kill but RARE - gate to stage 3+
                      "Quetzal": ("air", "apex")}              # flies very high - needs ranged/strong weapon

    # rate/volume grinds + specific-source collections that shouldn't sit at sphere 0/1 (tester
    # feedback). Light "you've established a base" bump = Forge; tough-source harvests = weapon floor.
    # metal tools double as the gate for their matching harvest (hatchet=hide/plants, pick=stone/oil/
    # pearls); requiring them here also makes Metal Pick/Hatchet real progression key items. Each
    # metal tool needs the Forge (recipe), so these land at the metal-age tier, not sphere 0.
    _EXTRA_GATES = {"Collect 1000 Hide": "Metal Hatchet",
                    "Collect 250 Silica Pearls": "Metal Pick",
                    "Collect 250 Oil": "Metal Pick",
                    "Rare Flower x50": "Metal Hatchet",
                    "Kill 100 Creatures": "Forge",
                    "Collect 5 Woolly Rhino Horn": "Crossbow | Longneck Rifle"}
    _KILL_WATER_APEX = "Scuba Tank + Crossbow"                 # dive + the underwater weapon
    _KILL_WATER_MID = "Scuba Tank"                             # just needs to get down there
    _KILL_APEX = "Crossbow | Longneck Rifle"                   # a real damage weapon

    def _spawn_hd(self) -> dict:
        m = getattr(self, "_spawn_hd_cache", None)
        if m is None:
            m = {e["name"]: (e.get("habitat"), e.get("danger")) for e in self._spawn_classes}
            self._spawn_hd_cache = m
        return m

    def _hab_danger(self, short: str, tag: str):
        if short in self._KILL_HD_FORCE:
            return self._KILL_HD_FORCE[short]
        sc = self._spawn_hd()
        return sc.get(short) or sc.get(tag) or sc.get(self._KILL_HD_ALIAS.get(short, ""))

    def _kill_gate_expr(self, short: str, tag: str) -> str:
        hd = self._hab_danger(short, tag)
        if not hd:
            return ""                                          # unknown -> stay early (ungated)
        hab, dng = hd
        if hab == "water":
            return self._KILL_WATER_APEX if dng == "apex" else self._KILL_WATER_MID if dng == "mid" else ""
        return self._KILL_APEX if dng == "apex" else ""

    # cave requirement AST for an artifact short name (e.g. "Hunter").
    def _cave_ast(self, art: str):
        return self._compile_expr(self._tame_logic_data.get("cave_reqs", {}).get(art, ""))

    # boss reachability AST: a boss needs all its artifacts' caves done; Overseer needs the 3
    # island bosses defeated. Boss kills are the goal, gated here so the win requires real prep.
    def _boss_ast(self, boss_short: str):
        arts = self._tame_logic_data.get("boss_artifacts", {}).get(boss_short)
        if arts:
            kids = [k for k in (self._cave_ast(a) for a in arts) if k != ("true",)]
            return ("and", kids) if len(kids) > 1 else (kids[0] if kids else ("true",))
        if boss_short in self._tame_logic_data.get("overseer_bosses", []) or boss_short == "Overseer":
            if boss_short == "Overseer":
                ob = self._tame_logic_data.get("overseer_bosses", [])
                return ("and", [("has", b + " Defeated") for b in ob]) if ob else ("true",)
        return ("true",)

    # tribute check -> the dino you kill for the organ (same combat capability as taming it).
    def _tribute_ast(self, loc_name: str):
        prefix = loc_name.rsplit(" x", 1)[0]           # "Argentavis Talon x10" -> "Argentavis Talon"
        dino = self._tame_logic_data.get("tribute_dino", {}).get(prefix)
        return self._tame_ast(dino) if dino else None

    # explorer note / dossier physically in a cave: "underwater" = deep ocean (scuba + water combat);
    # an artifact name = that land cave's access; "tek" = the Tek Cave (post-bosses).
    def _note_ast(self, key: str):
        if key == "underwater":
            return self._compile_expr("Rifle KO + Scuba Tank")
        if key == "tek":
            return self._boss_ast("Overseer")
        return self._cave_ast(key)                     # a land artifact cave

    # every AP item name any access rule can require -> must be PROGRESSION so the fill guarantees
    # reachability (received before AP requires the tame/cave/tribute/boss it gates).
    def _tame_required_items(self) -> set:
        cache = getattr(self, "_tame_req_cache", None)
        if cache is None:
            tl = self._tame()
            out: set = set()
            if tl:
                from .tame_logic import _collect
                asts = [self._tame_ast(self._dino_short(d))
                        for d in self._dinos.get("dinos", []) if d.get("tame_loc")]
                asts += [self._cave_ast(a) for a in self._tame_logic_data.get("cave_reqs", {})]
                asts += [self._boss_ast(b) for b in
                         list(self._tame_logic_data.get("boss_artifacts", {})) + ["Overseer"]]
                asts += [self._note_ast(k) for k in self._tame_logic_data.get("note_caves", {}).values()]
                # KILL/collection gates (set_rules) also require engrams (e.g. Metal Pick/Hatchet):
                # they MUST be progression too, else the fill won't guarantee they're reachable
                # before the check that needs them (-> accessibility failure).
                asts += [self._compile_expr(self._kill_gate_expr(self._dino_short(d), d.get("dino_tag")))
                         for d in self._dinos.get("dinos", []) if d.get("kill_loc")]
                asts += [self._compile_expr(e) for e in self._EXTRA_GATES.values()]
                asts += [self._compile_expr(e) for e in
                         (self._KILL_APEX, "Crossbow + Scuba Tank", "Longneck Rifle + Scuba Tank")]
                for a in asts:
                    _collect(a, out)
            self._tame_req_cache = out
            cache = out
        return cache

    # explicit per-location tiers from the data (inventory checks + count milestones carry "tier").
    def _loc_tier(self, loc_name: str):
        m = getattr(self, "_loc_tier_cache", None)
        if m is None:
            m = {}
            for cat in self._locations["location_categories"].values():
                for e in cat.get("entries", []):
                    if "tier" in e:
                        m[e["name"]] = e["tier"]
            self._loc_tier_cache = m
        return m.get(loc_name)

    # tier (0-3) a location belongs to under progression_tiers.
    def _tier_of(self, loc_name: str) -> int:
        t = self._loc_tier(loc_name)
        if t is not None:
            return t
        if loc_name.startswith("Killed: "):
            return self._dino_tier(loc_name[len("Killed: "):])
        if loc_name.startswith("Tamed: "):
            return self._dino_tier(loc_name[len("Tamed: "):])
        if loc_name.startswith("Boss:") or loc_name.endswith(" Defeated"):
            return 3
        if loc_name.startswith("Reach Level "):
            try:
                n = int(loc_name.rsplit(" ", 1)[1])
            except ValueError:
                return 0
            return 0 if n <= 40 else 1 if n <= 80 else 2 if n <= 120 else 3
        return 0                                            # notes, first-tame milestone, etc.

    # player-listed items to force early (only valid item names).
    def _extra_early_names(self) -> set:
        return set(self.options.extra_early_items.value) & set(self.item_name_to_id)

    # Force the player's chosen extra_early_items into AP's early-item system. (The old
    # progression_tiers station-gate forcing is retired - tame rules order the fill now.)
    def generate_early(self) -> None:
        glob = self.options.station_placement.value == StationPlacement.option_global_early
        target = self.multiworld.early_items if glob else self.multiworld.local_early_items
        for name in self._extra_early_names():
            target[self.player][name] = 1

    # (tiered placement) place each tier gate on a KILL or LEVEL check in the tier it opens FROM
    # (gate i in Tier i: Smithy in T0, Forge in T1, Fabricator in T2). Locking them here keeps them
    # in this world (never wait on a friend) and correctly ordered. Only kills/levels are eligible:
    # notes are tedious to hunt, and TAMES are locked behind a Tame: X item (lock_taming), so a gate
    # on a tame wouldn't be reachable early.
    def pre_fill(self) -> None:
        return                                              # tiered gate placement retired (tame rules order the fill now)
        used = self._used_locations()                       # noqa: unreachable (kept for reference)
        for i, gates in enumerate(TIER_GATES):
            cands = [n for n in used
                     if (n.startswith("Killed: ") or n.startswith("Reach Level "))
                     and self._tier_of(n) == i]
            for gate in gates:
                item = next((it for it in self.multiworld.itempool
                             if it.player == self.player and it.name == gate), None)
                if item is None:        # gate listed as an extra starter -> already open
                    continue
                pick = self.random.choice(cands)
                cands.remove(pick)      # distinct spot per gate in the same tier
                self.multiworld.itempool.remove(item)
                self.multiworld.get_location(pick, self.player).place_locked_item(item)

    def create_regions(self) -> None:
        # Single-region layout. Ordering/softlock-safety comes from the tame/craft ACCESS RULES
        # (set_rules), which supersede the old progression_tiers region gating (now retired - the
        # option is ignored). early_dino_checks still applies its PRIORITY/EXCLUDED overlay here.
        menu = Region("Menu", self.player, self.multiworld)
        regions = [menu]
        self._regions_flat(menu, regions)
        self.multiworld.regions += regions

    # progression_tiers: 4 regions T0->T1->T2->T3, each gated by the prior station engram. Every
    # check lives in its tier (DINO_TIER / level / boss). The 3 gate engrams are hard-placed on
    # active non-note checks in pre_fill, so a tier is never gated behind hunting an explorer note.
    # Explorer notes are pulled behind TIER 2 (needs BOTH T0->T1 and T1->T2 gates transitively -
    # genuinely 2 rounds deep, sphere-2+) so ARK's sphere-0 set = ONLY T0 kills + low levels. A
    # single-hop gate (e.g. "any early tame") isn't deep enough - AP's early-item placement still
    # treats sphere-1 as "early". Sphere-2+ is deep enough that another game's early-forced item
    # (e.g. DS3 early_banner) can only land on a T0 kill/level-up here - never a note, never T1+.
    def _regions_tiered(self, menu: Region, regions: list) -> None:
        tiers = [Region(f"Tier {i}", self.player, self.multiworld) for i in range(4)]
        notes = Region("Explorer Notes", self.player, self.multiworld)
        regions.extend(tiers)
        regions.append(notes)
        menu.connect(tiers[0])
        for i, gates in enumerate(TIER_GATES):             # T0->T1 needs Anvil Bench + Mortar And Pestle, etc.
            tiers[i].connect(tiers[i + 1], f"Tier {i} -> Tier {i + 1}",
                             rule=lambda state, g=gates: state.has_all(g, self.player))
        tiers[2].connect(notes, "Tier 2 -> Explorer Notes")   # inherits gates 0 AND 1 transitively
        # Boss checks (12) + boss-adjacent lore notes (4 Holograms, physically in/near the boss
        # arenas) are EXCLUDED from holding progression: reaching them takes real prep + a fight,
        # not just an item, so another player's needed item could sit there for a very long time
        # even when "logically reachable". They still fire as checks, just filler-only.
        HOLOGRAM_NOTES = {"Hologram: Broodmother", "Hologram: Megapithecus",
                          "Hologram: Dragon", "Hologram: Overseer"}
        for loc_name, loc_id in self._used_locations().items():
            parent = notes if self._is_note(loc_name) else tiers[self._tier_of(loc_name)]
            loc = ArkLocation(self.player, loc_name, loc_id, parent)
            if loc_name.startswith("Boss:") or loc_name in HOLOGRAM_NOTES:
                loc.progress_type = LocationProgressType.EXCLUDED
            parent.locations.append(loc)
        for ev_name in self._boss_events():                # boss events live where bosses do (T3)
            ev = ArkLocation(self.player, ev_name, None, tiers[3])
            ev.place_locked_item(ArkItem(ev_name, ItemClassification.progression, None, self.player))
            tiers[3].locations.append(ev)

    # Single open region. Logical ordering + softlock-safety come from the tame/craft ACCESS RULES
    # (set_rules), not region gating. early_dino_checks is RETIRED (its aggressive EXCLUDE overlay
    # was built for the old tier model and starves the now progression-heavy pool of filler).
    # Boss-defeat events live here; their reachability is gated in set_rules (Crossbow KO floor).
    def _regions_flat(self, menu: Region, regions: list) -> None:
        island = Region("The Island", self.player, self.multiworld)
        regions.append(island)
        # filler-only checks: NO_TAME_LOGIC tames (in-game still gated) + big note-collection
        # milestones (>= 50 notes) + high level-ups (> 70) - too grindy to sit progression behind.
        # Also tame/breed COUNT milestones: taming+breeding are locked behind Tame: items, but the
        # count isn't modelled in logic (AP treats them sphere-0), so progression there can bury a
        # gating engram (e.g. Anvil Bench on "Tame 50 Creatures") behind the very grind it enables.
        GRIND_TAGS = ("milestone_tametotal_", "milestone_tames_", "milestone_breedtotal_",
                      "milestone_first_breed")
        excluded_progression = {"Tamed: " + d for d in NO_TAME_LOGIC}
        for mst in self._locations["location_categories"].get("milestones", {}).get("entries", []):
            tag = mst.get("tag", "")
            if tag.startswith("milestone_notes_"):
                try:
                    if int(tag.rsplit("_", 1)[1]) >= 50:
                        excluded_progression.add(mst["name"])
                except ValueError:
                    pass
            elif tag.startswith(GRIND_TAGS):
                excluded_progression.add(mst["name"])
        # special/obscure note families that are hard to physically reach (cross-map narrative notes;
        # "??? Note" is a real in-game note name) - keep progression off them so it's never stranded
        # on a note a player may never find. (Ordinary character notes stay eligible; cave/water ones
        # are gated by note_caves in set_rules.)
        HARD_NOTE_PREFIXES = ("Hologram: ", "Genesis Chronicles", "HLN-A Discovery", "??? Note")
        # ALL alpha kills are filler-only. An alpha realistically needs a good TAME to kill, and
        # tames are themselves locked behind Tame: items - so progression here can strand a
        # foundational engram behind a fight the player can't take yet (playtest: Mortar And Pestle
        # landed on Killed: Alpha Carno). Land + water alike.
        excluded_progression |= {e["name"] for e in
                                 self._locations["location_categories"].get("alpha_kills", {})
                                 .get("entries", [])}
        # dossiers earned only by taming a very-late-game creature - the dossier is as gated as the
        # tame, so don't strand key progression on it.
        excluded_progression |= {"Dossier: Rhyniognatha", "Dossier: Carcharodontosaurus"}
        for loc_name in self._used_locations():
            if loc_name.startswith("Reach Level "):
                try:
                    if int(loc_name.rsplit(" ", 1)[1]) > 70:
                        excluded_progression.add(loc_name)
                except ValueError:
                    pass
            elif loc_name.startswith(HARD_NOTE_PREFIXES):
                excluded_progression.add(loc_name)
        for loc_name, loc_id in self._used_locations().items():
            loc = ArkLocation(self.player, loc_name, loc_id, island)
            if loc_name in excluded_progression:
                loc.progress_type = LocationProgressType.EXCLUDED
            island.locations.append(loc)
        for ev_name in self._boss_events():
            ev = ArkLocation(self.player, ev_name, None, island)
            ev.place_locked_item(ArkItem(ev_name, ItemClassification.progression, None, self.player))
            island.locations.append(ev)
        menu.connect(island)

    def _goal_bosses(self) -> int:
        return self.options.goal.value + 1     # 1..4 cumulative (BM, +MP, +Dragon, +Overseer)

    def set_rules(self) -> None:
        excluded = self._sanity_excluded()
        # TAME/CRAFT ACCESS RULES (always on): "Tamed: X" requires the engrams X's taming method
        # needs (from tame_logic; prevents the fill stranding a needed item behind a dino you can't
        # yet tame). lock_taming ALSO requires the "Tame: X" unlock. Both add_rule -> ANDed.
        for d in self._dinos.get("dinos", []):
            if not d.get("tame_loc"):
                continue
            short = self._dino_short(d)
            if "Tamed: " + short in excluded or short in NO_TAME_LOGIC:   # sanity-dropped or logic-excluded
                continue
            loc = self.multiworld.get_location("Tamed: " + short, self.player)
            ast = self._tame_ast(short)
            if ast != ("true",):
                add_rule(loc, lambda state, a=ast: eval_ast(a, state, self.player))
        if self.options.lock_taming.value:
            for d in self._dinos.get("dinos", []):
                if not d.get("tame_loc"):
                    continue
                item = d["ap_name"]                         # "Tame: X"
                short = item.replace("Tame: ", "")
                if "Tamed: " + short in excluded or short in NO_TAME_LOGIC:
                    continue
                loc = self.multiworld.get_location("Tamed: " + short, self.player)
                add_rule(loc, lambda state, it=item: state.has(it, self.player))
            # "Tame N Species" (distinct) milestones honestly require N tame unlocks.
            tame_items = sorted(self._tame_item_names)
            for m in self._locations["location_categories"].get("milestones", {}).get("entries", []):
                if m.get("tag", "").startswith("milestone_tames_"):
                    n = int(m["tag"].rsplit("_", 1)[1])
                    loc = self.multiworld.get_location(m["name"], self.player)
                    add_rule(loc, lambda state, k=n: state.has_from_list(tame_items, self.player, k))
            # (collective "Tame N Creatures" milestones need no item rule - same species can be
            #  tamed repeatedly; they're placed by their data "tier" field.)
        tl = self._tame()
        if tl:
            # Artifact checks require their CAVE capability (combat + gear: gas mask for swamp,
            # scuba for water, fur for cold - see data/tame_logic.json cave_reqs).
            for e in self._locations["location_categories"].get("inventory_checks", {}).get("entries", []):
                name = e["name"]
                if name in excluded:
                    continue
                if name.startswith("Artifact: "):
                    ast = self._cave_ast(name[len("Artifact: "):])
                    if ast != ("true",):
                        add_rule(self.multiworld.get_location(name, self.player),
                                 lambda state, a=ast: eval_ast(a, state, self.player))
                else:                                   # tribute organ check -> can kill the source dino
                    tast = self._tribute_ast(name)
                    if tast and tast != ("true",):
                        add_rule(self.multiworld.get_location(name, self.player),
                                 lambda state, a=tast: eval_ast(a, state, self.player))
            # Boss-defeat EVENTS require the boss's artifacts (its caves done); Overseer requires
            # the 3 island bosses defeated. This makes the WIN require real ARK prep, not just a
            # crossbow. (Goal = any difficulty = Gamma = artifacts only; tributes gate their own
            # checks, not the goal.)
            for ev_name in self._boss_events():         # "Broodmother Defeated" -> "Broodmother"
                ast = self._boss_ast(ev_name.replace(" Defeated", ""))
                if ast != ("true",):
                    add_rule(self.multiworld.get_location(ev_name, self.player),
                             lambda state, a=ast: eval_ast(a, state, self.player))
            # explorer notes / dossiers physically in caves or deep underwater (from wiki map data)
            # require their cave/water access, so progression is never stranded on an unreachable note.
            used = self._used_locations()
            for name, key in self._tame_logic_data.get("note_caves", {}).items():
                if name not in used or name in excluded:   # note not in this seed (dossier_checks) or dropped
                    continue
                ast = self._note_ast(key)
                if ast != ("true",):
                    add_rule(self.multiworld.get_location(name, self.player),
                             lambda state, a=ast: eval_ast(a, state, self.player))
            # REALISM: tough KILL checks shouldn't sit at sphere 0/1 (a kill has no tame-lock, so
            # by default any Killed: X is instantly reachable). Gate water creatures behind diving
            # gear and apex predators behind a real weapon, so they hold LATER progression. Easy
            # kills (docile/mid land) stay early. Never strands progression: the gate items are
            # always in the pool. (See _kill_gate_expr - reuses spawn_classes.json habitat/danger.)
            used_kill = self._used_locations()
            for d in self._dinos.get("dinos", []):
                if not d.get("kill_loc"):
                    continue
                kloc = "Killed: " + self._dino_short(d)
                if kloc not in used_kill or kloc in excluded:
                    continue
                ast = self._compile_expr(self._kill_gate_expr(self._dino_short(d), d.get("dino_tag")))
                if ast != ("true",):
                    add_rule(self.multiworld.get_location(kloc, self.player),
                             lambda state, a=ast: eval_ast(a, state, self.player))
            # land alpha kills (water alphas are already progression-excluded) -> apex weapon floor
            for e in self._locations["location_categories"].get("alpha_kills", {}).get("entries", []):
                name = e["name"]
                if name not in used_kill or name in excluded:
                    continue
                ast = self._compile_expr(self._KILL_APEX)
                if ast != ("true",):
                    add_rule(self.multiworld.get_location(name, self.player),
                             lambda state, a=ast: eval_ast(a, state, self.player))
            # big KILL-SPECIES milestones (>= 50 distinct species) need broad combat + underwater
            # reach (the roster includes deep-water species), so they hold late progression.
            for mst in self._locations["location_categories"].get("milestones", {}).get("entries", []):
                tag = mst.get("tag", "")
                if not tag.startswith("milestone_kills_"):
                    continue
                name = mst["name"]
                if name not in used_kill or name in excluded:
                    continue
                try:
                    n = int(tag.rsplit("_", 1)[1])
                except ValueError:
                    continue
                if n < 50:
                    continue
                expr = "Longneck Rifle + Scuba Tank" if n >= 100 else "Crossbow + Scuba Tank"
                ast = self._compile_expr(expr)
                if ast != ("true",):
                    add_rule(self.multiworld.get_location(name, self.player),
                             lambda state, a=ast: eval_ast(a, state, self.player))
            # named rate/volume grinds + tough-source harvests -> light bump off sphere 0/1
            for name, expr in self._EXTRA_GATES.items():
                if name not in used_kill or name in excluded:
                    continue
                ast = self._compile_expr(expr)
                if ast != ("true",):
                    add_rule(self.multiworld.get_location(name, self.player),
                             lambda state, a=ast: eval_ast(a, state, self.player))
        # Goal = defeat the first N bosses (collect their defeat events). Boss order matches
        # loc-id order: Broodmother, Megapithecus, Dragon, Overseer.
        events = list(self._boss_events())[: self._goal_bosses()]
        self.multiworld.completion_condition[self.player] = \
            lambda state: all(state.has(n, self.player) for n in events)

    # randomize_dino_spawns: FULL biome roster randomization. Every species is dealt (partitioned)
    # across the biome spawn containers, and the connector emits one
    # ConfigOverrideNPCSpawnEntriesContainer Game.ini line per container - each biome's spawn
    # roster is completely REPLACED by its seeded hand, at natural spawn density. Partitioning
    # guarantees every species still spawns somewhere, so all Killed:/Tamed: checks stay
    # obtainable. Caves / specialty spawners (Giga, Quetz, beaver dams...) aren't overridden and
    # keep their natives. (NPCReplacements-based shuffling is impossible: live-tested 2026-07-15,
    # ARK resolves replacement chains recursively so any cycle cancels or voids the spawn.)
    # grouped = land+air species dealt across land biomes, water species across water zones,
    # with predators DOWN-WEIGHTED (danger tag from spawn_classes.json: apex 0.1, mid 0.5,
    # docile 1.0) so zones read as fauna with predators in them, not predator walls;
    # chaos = everything dealt across everything at equal weight (beached mosas and all).
    # Apex is intentionally low (0.1) - the big water giants (Mosa/Plesio/Tuso/Liop/Leeds) and
    # land apexes (Carcha/Rex/Giga...) were overpopulating; this thins them to rare encounters.
    DANGER_WEIGHT = {"apex": 0.1, "mid": 0.5, "docile": 1.0}

    def _spawn_overrides(self) -> list:
        mode = self.options.randomize_dino_spawns.value
        if not mode or not self._spawn_classes or not self._spawn_containers:
            return []
        if mode == 2:      # chaos: one big deal across every container, equal weights
            weight = {e["class"]: 1.0 for e in self._spawn_classes}
            pools = {"all": [e["class"] for e in self._spawn_classes]}
            groups = {"all": [c["container"] for c in self._spawn_containers]}
        else:              # grouped: land+air species -> land containers, water -> water
            weight = {e["class"]: self.DANGER_WEIGHT.get(e.get("danger", "docile"), 1.0)
                      for e in self._spawn_classes}
            pools = {
                "land":  [e["class"] for e in self._spawn_classes if e["habitat"] in ("land", "air")],
                "water": [e["class"] for e in self._spawn_classes if e["habitat"] == "water"],
            }
            groups = {"land": [], "water": []}
            for c in self._spawn_containers:
                groups.setdefault(c["habitat"], []).append(c["container"])
        assign: Dict[str, list] = {}
        for key, containers in groups.items():
            species = pools.get(key, [])[:]
            if not containers or not species:
                continue
            self.random.shuffle(species)
            for i, cls in enumerate(species):            # round-robin deal -> every species lives
                assign.setdefault(containers[i % len(containers)], []).append(cls)
        return [[c, sorted([cls, weight[cls]] for cls in classes)]
                for c, classes in sorted(assign.items())]

    # tell the connector which bosses count for the goal + whether saddles are bundled (it relays
    # bundle_saddles to the plugin so the plugin grants the saddle on tame unlock).
    def fill_slot_data(self) -> dict:
        # goal_boss_tags: the boss BASE-TAGS (e.g. "SpiderBoss") for the first N bosses, in order.
        # Boss KILLS are no longer AP check locations (nothing can get stranded behind a hard or
        # near-impossible boss kill). The plugin signals each defeat by base-tag to boss_out.jsonl;
        # the client sends the AP goal once every required tag has appeared.
        order: list = []
        seen: set = set()
        for b in self._locations["location_categories"]["bosses"]["entries"]:
            base = b["tag"].split("_")[0]
            if base not in seen:
                seen.add(base)
                order.append(base)
        return {"goal_bosses": self._goal_bosses(),
                "goal_boss_tags": order[: self._goal_bosses()],
                "bundle_saddles": bool(self.options.bundle_saddles.value),
                "free_starter_engrams": bool(self.options.free_starter_engrams.value),
                "death_link": bool(self.options.death_link.value),
                "npc_replacements": [],           # legacy key (permutation design retired)
                "spawn_additions": [],            # legacy key (additions design superseded)
                "spawn_overrides": self._spawn_overrides()}
