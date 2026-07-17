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
                   load_spawn_container_data)
from .Items import (ArkItem, build_item_table, FILLER_NAME, FILLER_ID,
                    STRUCTURE_BUNDLES, structure_bundle_members)
from .Locations import ArkLocation, build_location_table
from .Options import ArkASAOptions, StationPlacement

GAME = "ARK Survival Evolved"

# weak, sphere-1 dinos whose first-kill checks become PRIORITY when early_dino_checks is on
# (so AP fill drops progression - e.g. another game's early item - onto them).
EARLY_DINO_SHORTS = (
    "Dodo", "Parasaur", "Triceratops", "Dilophosaur",
    "Phiomia", "Lystrosaurus", "Compsognathus", "Dimorphodon",
)

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
        "Raptor", "Sabertooth", "Stegosaurus", "Styracosaurus", "Terror Bird", "Triceratops",
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
        if name in self._filler_names:
            cls = ItemClassification.filler
        elif self.options.progression_tiers.value and name in GATE_ENGRAMS:
            cls = ItemClassification.progression
        elif self.options.lock_taming.value and name in self._tame_item_names:
            cls = ItemClassification.progression
        else:
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
        for key in ("bosses", "milestones", "levels", "alpha_kills", "inventory_checks"):
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
        # early_dino_checks EXCLUDES all non-note checks, so progression can only sit on notes + the
        # 8 priority kills. Make sure that subset still fits every progression item.
        if self.options.early_dino_checks.value and not self.options.progression_tiers.value:
            levels = self._locations["location_categories"].get("levels", {}).get("entries", [])
            n_early_levels = sum(1 for e in levels
                                 if self._early_eligible(e["name"], set()))
            extra = len(EARLY_DINO_SHORTS) + n_early_levels       # notes counted via dossier_checks
            prog_hosts = self.options.dossier_checks.value + extra
            if len(pool) > prog_hosts:
                raise ValueError(
                    f"ARK: early_dino_checks leaves only {prog_hosts} progression locations for "
                    f"{len(pool)} items. Raise dossier_checks to >= {len(pool) - extra}.")
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

    # Force items into AP's early-item system. station_placement local/global_early routes the tier
    # gates (progression_tiers only); extra_early_items always route by the same mode.
    def generate_early(self) -> None:
        mode = self.options.station_placement.value
        glob = mode == StationPlacement.option_global_early
        target = self.multiworld.early_items if glob else self.multiworld.local_early_items
        if self.options.progression_tiers.value and mode != StationPlacement.option_tiered:
            for g in GATE_ENGRAMS:                     # stations early (tiered handled in pre_fill)
                target[self.player][g] = 1
        for name in self._extra_early_names():         # player picks, with or without tiers
            target[self.player][name] = 1

    # (tiered placement) place each tier gate on a KILL or LEVEL check in the tier it opens FROM
    # (gate i in Tier i: Smithy in T0, Forge in T1, Fabricator in T2). Locking them here keeps them
    # in this world (never wait on a friend) and correctly ordered. Only kills/levels are eligible:
    # notes are tedious to hunt, and TAMES are locked behind a Tame: X item (lock_taming), so a gate
    # on a tame wouldn't be reachable early.
    def pre_fill(self) -> None:
        if not self.options.progression_tiers.value:
            return
        if self.options.station_placement.value != StationPlacement.option_tiered:
            return                                          # local/global early handled in generate_early
        used = self._used_locations()
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
        menu = Region("Menu", self.player, self.multiworld)
        regions = [menu]
        if self.options.progression_tiers.value:
            self._regions_tiered(menu, regions)            # tech-tree mode (supersedes early guard)
        else:
            self._regions_flat(menu, regions)              # open, or early_dino_checks guard
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

    # no progression tiers: single region, plus the optional early_dino_checks lockout guard
    # (8 early kills PRIORITY, notes gated behind any early tame, everything slow/late EXCLUDED).
    def _regions_flat(self, menu: Region, regions: list) -> None:
        island = Region("The Island", self.player, self.multiworld)
        regions.append(island)
        gate = self.options.early_dino_checks.value
        early = {"Killed: " + s for s in EARLY_DINO_SHORTS}
        notes = Region("Explorer Notes", self.player, self.multiworld) if gate else island
        if gate:
            regions.append(notes)
        hologram_notes = {"Hologram: Broodmother", "Hologram: Megapithecus",
                          "Hologram: Dragon", "Hologram: Overseer"}
        for loc_name, loc_id in self._used_locations().items():
            parent = notes if (gate and self._is_note(loc_name)) else island
            loc = ArkLocation(self.player, loc_name, loc_id, parent)
            if gate and loc_name in early:
                loc.progress_type = LocationProgressType.PRIORITY
            elif gate and (loc_name in hologram_notes or not self._early_eligible(loc_name, early)):
                loc.progress_type = LocationProgressType.EXCLUDED
            parent.locations.append(loc)
        for ev_name in self._boss_events():
            ev = ArkLocation(self.player, ev_name, None, island)
            ev.place_locked_item(ArkItem(ev_name, ItemClassification.progression, None, self.player))
            island.locations.append(ev)
        menu.connect(island)
        if gate:   # notes reachable once the player has received any early-dino tame unlock
            tames = [f"Tame: {s}" for s in EARLY_DINO_SHORTS]
            island.connect(notes, "The Island -> Explorer Notes",
                           rule=lambda state: state.has_any(tames, self.player))

    def _goal_bosses(self) -> int:
        return self.options.goal.value + 1     # 1..4 cumulative (BM, +MP, +Dragon, +Overseer)

    def set_rules(self) -> None:
        # lock_taming: you can't tame X in-game until you receive its "Tame: X" unlock, so the
        # "Tamed: X" check logically requires that item (prevents AP placing Tame: X on Tamed: X =
        # a softlock, and keeps tame checks out of logic until reachable).
        if self.options.lock_taming.value:
            excluded = self._sanity_excluded()
            for d in self._dinos.get("dinos", []):
                if not d.get("tame_loc"):
                    continue
                item = d["ap_name"]                         # "Tame: X"
                short = item.replace("Tame: ", "")
                if "Tamed: " + short in excluded:           # tame_sanity dropped this check
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
        # boss_groups: per boss (in goal order), ALL its difficulty loc ids - the connector sends
        # the AP goal once each required boss has ANY difficulty checked.
        groups: Dict[str, list] = {}
        for b in self._locations["location_categories"]["bosses"]["entries"]:
            short = b["name"].replace("Boss: ", "").split(" (")[0]
            groups.setdefault(short, []).append(b["id"])
        return {"goal_bosses": self._goal_bosses(),
                "boss_groups": list(groups.values()),
                "bundle_saddles": bool(self.options.bundle_saddles.value),
                "free_starter_engrams": bool(self.options.free_starter_engrams.value),
                "death_link": bool(self.options.death_link.value),
                "npc_replacements": [],           # legacy key (permutation design retired)
                "spawn_additions": [],            # legacy key (additions design superseded)
                "spawn_overrides": self._spawn_overrides()}
