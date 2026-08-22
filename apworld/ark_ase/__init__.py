"""ARK: Survival Evolved world for Archipelago.

Vertical-slice. Items = engram unlocks + taming/supply specials (+ filler).
Locations = dossiers/explorer notes + bosses + milestones. IDs come from the shared
data/ files so a generated game matches the in-game ArkServerApi plugin exactly.

Install: drop the `ark_ase` folder into Archipelago `worlds/`, or zip its contents to
`ark_ase.apworld` and install via the launcher.
"""
import re
from typing import Dict

from BaseClasses import Item, ItemClassification, LocationProgressType, Region, Tutorial
from Options import OptionError
from worlds.AutoWorld import World, WebWorld
from worlds.generic.Rules import add_rule

from .data import (load_engram_data, load_location_data, load_dino_data, load_crate_data,
                   load_filler_data, load_tek_data, load_spawn_class_data,
                   load_spawn_container_data, load_tame_logic_data, load_mod_catalog,
                   load_explore_data, load_map_data)
from .Items import (ArkItem, build_item_table, FILLER_NAME, FILLER_ID,
                    STRUCTURE_BUNDLES, structure_bundle_members)
from .Locations import ArkLocation, build_location_table
from .Options import ArkASAOptions, StationPlacement, Goal
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

# Building-mod variant naming. A mod engram that is just the mod's version of a vanilla structure
# folds into the vanilla engram (see _variant_pairs), so ONE unlock grants both. Suffix rules are
# tried first, then the curated table for the ones the mod RENAMES rather than suffixes. Every
# result is still checked against the real vanilla engram list, so a bad guess simply doesn't pair.
VARIANT_SUFFIXES = (" Plus", " Tek", " SS", " SP", " LR")   # LR = Lethal's Reusables
VARIANT_ALIAS = {
    "Engram: Smithy Plus": "Engram: Anvil Bench",
    "Engram: Mortar Pestle Plus": "Engram: Mortar And Pestle",
    # vanilla's Refrigerator is internally PrimalItemStructure_IceBox, so its generated ap_name is
    # "Ice Box" - the display name in-game is "Refrigerator".
    "Engram: Fridge Plus": "Engram: Ice Box",
    "Engram: Bed Plus": "Engram: Simple Bed",
    "Engram: Bunk Bed Plus": "Engram: Modern Bed",
    "Engram: Cloning Chamber Plus": "Engram: Tek Cloning Chamber",
    "Engram: Replicator Plus": "Engram: Tek Replicator",
    "Engram: Transmitter Plus": "Engram: Tek Transmitter",
    "Engram: Teleporter Plus": "Engram: Tek Teleporter",
    "Engram: Incubator Plus": "Engram: Egg Incubator",
    "Engram: Industrial Grill Plus": "Engram: Grill",
    # vanilla "Loadout Mannequin" is internally PrimalItemStructure_LoadoutDummy (Training Dummy is
    # a DIFFERENT structure), so both S+ loadout dummies pair with it.
    "Engram: Loadout Dummy Plus": "Engram: Loadout Mannequin",
    "Engram: Loadout Dummy SS": "Engram: Loadout Mannequin",
    # (S+ Fridge Plus / Bee Hive Plus have NO vanilla counterpart in our engram set, so they stay
    #  their own separate unlocks - nothing to pair them with.)
    # Lethal's Reusables: readable names that differ from the vanilla engram ap_name.
    "Engram: Scuba Tank LR": "Engram: Scuba Shirt Suit With Tank",
    "Engram: Whip LR": "Engram: Weapon Whip",
    "Engram: Taxidermy Plus Large": "Engram: Taxidermy Base Large",
    "Engram: Taxidermy Plus Medium": "Engram: Taxidermy Base Medium",
    "Engram: Taxidermy Plus Small": "Engram: Taxidermy Base Small",
}

# "Collect N <resource>" checks for CRAFTED resources are GATED behind the engram/station that makes
# the resource. Without this the fill can self-gate ("Collect 100 Sparkpowder" holding Engram:
# Sparkpowder = you need the engram to make what unlocks the engram = softlock). {resource substring
# in the location name -> required engram}. Raw-gather collects (Wood/Stone/Hide/Metal Ore/...) need
# no engram and stay ungated.
CRAFTED_COLLECT_ENGRAM = {
    "Sparkpowder": "Engram: Sparkpowder", "Gunpowder": "Engram: Gunpowder",
    "Narcotic": "Engram: Narcotic", "Stimulant": "Engram: Stimulant",
    "Electronics": "Engram: Electronics", "Cementing Paste": "Engram: Mortar And Pestle",
    "Metal Ingot": "Engram: Forge", "Gasoline": "Engram: Forge",
    "Absorbent Substrate": "Engram: Fabricator", "Element Dust": "Engram: Fabricator",
    "Charcoal": "Engram: Campfire",
}

# Core engrams that are HARD-PLACED (locked) onto an easy early-dino KILL instead of pooled or given
# free. Sphere-0 in practice (Killed: X on a weak dino has no gate), so deep recipes that need them
# (Campfire feeds cooked meat -> gunpowder -> Rifle KO) are available from the start - but you still
# earn it in-world by killing something trivial, not for free. See pre_fill.
HARD_PLACED = {"Engram: Campfire"}
# weak early dinos whose "Killed: X" check may host a HARD_PLACED engram (seeded pick among those
# actually present in the seed). All sphere-0 kills. NOTE: these are the LOCATION-name shorts
# (ap_name-derived), not the dino_tags - "Killed: Compsognathus", not "Killed: Compy".
EARLY_KILL_HOSTS = ("Dodo", "Compsognathus", "Dilophosaur", "Lystrosaurus", "Moschops")
# (nothing is auto-granted free by the base game now; mods can still list auto_grant engrams.)
CORE_AUTO_GRANT: set = set()

# progression_tiers: station engrams gate 4 tiers. Tier i -> i+1 needs ALL of TIER_GATES[i].
# T1 = Smithy (Anvil Bench) + Mortar And Pestle (narcotics/paste = the real early-craft spine).
# Order follows Lurch's sheet (and ARK's real craft chain): the Refining Forge and Mortar & Pestle
# are both tier 1 (a foundation is all they need), the Smithy/Anvil Bench is tier 2 because it costs
# metal INGOTS and therefore needs the Forge first, and the Fabricator is tier 3. Gating T1 on the
# Anvil Bench (as this did before 2026-07-26) was inverted: it opened a tier on a station you could
# not actually build yet.
TIER_GATES = (
    ("Engram: Forge", "Engram: Mortar And Pestle"),         # T0 -> T1
    ("Engram: Anvil Bench",),                               # T1 -> T2 (Smithy: needs the Forge)
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
    _mod_catalog = load_mod_catalog()          # every SUPPORTED mod (datapackage); mod_ids picks active
    # exploration checks: polygons measured in-game. Map-scoped - the datapackage holds every
    # mapped region, _used_locations keeps only the ones for the maps this slot enabled.
    _explore = load_explore_data().get("regions", {})
    # id -> the maps that carry it, from data/maps.json. Class level, like every other data table:
    # the datapackage must be identical for every player in a multiworld, so this NEVER filters
    # location_name_to_id / item_name_to_id. It only narrows the per-slot pool.
    _map_data = load_map_data()
    _map_content = _map_data.get("content", {})
    # key -> can this map carry a slot on its own? Only the Island can today. The ~549 "any" items
    # (engrams, craftable everywhere) go to EVERY slot, but locations are map-specific, and no
    # other map has a location count in that range - Ragnarok has no explorer notes at all, so it
    # never gets the 232 note checks that carry the pool. Enforced in generate_early so the player
    # gets a sentence naming the fix instead of a raw headroom failure from create_items.
    _map_standalone = {m["key"]: m.get("standalone", True) for m in _map_data.get("maps", [])}
    # Tek engrams: never in the AP pool - the plugin grants each boss's set on its first kill.
    _tek_names = {n for grants in load_tek_data().get("grants", {}).values() for n in grants}
    _filler_names = {f["ap_name"] for f in _filler.get("filler", [])} | {FILLER_NAME}
    _tame_item_names = {d["ap_name"] for d in _dinos.get("dinos", []) if d.get("ap_name")}
    _good_filler = [f["ap_name"] for f in _filler.get("filler", []) if not f.get("trap")]
    # optional per-entry "weight" (default 1) biases which good/trap filler fills a slot - the
    # resource Packs ship weights 2-5 so they show up more than one-off buffs/single-resource gives.
    _filler_weight = {f["ap_name"]: f.get("weight", 1) for f in _filler.get("filler", [])}
    item_name_to_id: Dict[str, int] = build_item_table(_engrams, _dinos, _crates, _filler, _mod_catalog)
    location_name_to_id: Dict[str, int] = build_location_table(_locations, _dinos, _mod_catalog,
                                                              _explore)

    # classify: only items that actually GATE logic are progression.
    #   progression = station gates (tiers) + tame unlocks (they gate "Tamed: X" via lock_taming)
    #   filler      = traps / bonus resources
    #   useful      = everything else (saddles, non-gating engrams, crate access, ...) - nice, not required
    def create_item(self, name: str) -> Item:
        no_logic = {"Tame: " + d for d in NO_TAME_LOGIC}
        if name in self._filler_names:
            cls = ItemClassification.filler
        elif name in self._tame_required_items() or name in self._group_forced_progression():
            cls = ItemClassification.progression      # engram (or a folded group rep) that GATES a tame
        elif self.options.progression_tiers.value and name in self._tier_gate_items():
            cls = ItemClassification.progression      # tier-gate station (or its group rep) opens a region
        elif name in self._crafted_collect_engrams():
            cls = ItemClassification.progression      # engram (or rep) that gates a crafted Collect check
        elif self.options.lock_taming.value and name in self._tame_item_names and name not in no_logic:
            cls = ItemClassification.progression
        else:                                          # useful: saddles, non-gating engrams, NO_TAME_LOGIC tames
            cls = ItemClassification.useful
        return ArkItem(name, cls, self.item_name_to_id[name], self.player)

    def get_filler_item_name(self) -> str:
        if not self._good_filler:
            return FILLER_NAME
        w = [self._filler_weight.get(n, 1) for n in self._good_filler]
        return self.random.choices(self._good_filler, weights=w, k=1)[0]

    # tame_sanity / food_sanity: deterministic per-seed sample of location NAMES to drop.
    # Cached so every _used_locations() call sees the same roll.
    # ---- exploration checks -------------------------------------------------------------------
    # The yaml names maps as "the_island"; explore_areas.json tags them "island". One mapping, here,
    # so neither side has to know about the other's spelling.
    _MAP_KEY = {"the_island": "island", "scorched_earth": "scorched", "aberration": "aberration",
                "extinction": "extinction", "genesis_part_1": "genesis1",
                "genesis_part_2": "genesis2", "the_center": "center", "ragnarok": "ragnarok",
                "valguero": "valguero", "crystal_isles": "crystalisles",
                "lost_island": "lostisland", "fjordur": "fjordur",
                "lost_colony": "lostcolony", "astraeos": "astraeos"}

    def _active_map_keys(self) -> set:
        return {self._MAP_KEY.get(m, m) for m in self.options.maps.value}

    def _check_maps_can_carry_a_slot(self) -> None:
        """A cluster-only map picked on its own cannot fill a pool. Say so plainly.

        Without this the player gets create_items' headroom error, which talks about engrams_per_item
        and dossier_checks - none of which can fix the real problem, because the shortfall is the
        map choice itself."""
        if not self._map_standalone:                     # no registry = nothing to enforce
            return
        active = self._active_map_keys()
        if any(self._map_standalone.get(k, True) for k in active):
            return
        to_yaml = {v: k for k, v in self._MAP_KEY.items()}
        picked = ", ".join(sorted(to_yaml.get(k, k) for k in active))
        carriers = ", ".join(sorted(to_yaml.get(k, k) for k, ok in self._map_standalone.items()
                                    if ok and k != "any"))
        raise OptionError(
            f"ARK: maps [{picked}] cannot fill a slot on their own. These maps are supported as "
            f"part of a CLUSTER - they share most of their content with the Island but have far "
            f"fewer locations of their own (Ragnarok has no explorer notes at all), so the item "
            f"pool has nowhere to go. Add a map that can carry a slot ({carriers}) to your 'maps' "
            f"list, e.g. maps: [the_island, {sorted(to_yaml.get(k, k) for k in active)[0]}].")

    def _check_rules_reachable(self) -> None:
        """A location this slot KEEPS whose rule compiled to ('false',) can never be reached.

        That happens when a requirement names content from a map this slot is not running - a cave
        whose only listed mounts are Scorched creatures, say. The location filter cannot catch it,
        because the location itself is on a map we ARE running; only the rule is impossible.

        Left alone, AP surfaces this much later as an unfillable seed or a stranded item. Fail here
        instead, naming the location, so the fix (widen the macro, or tag the location to the map
        whose creatures it actually requires) is obvious. Costs nothing when nothing is missing."""
        if not self._missing_items():
            return
        impossible = []
        for name in self._used_locations():
            if name.startswith("Tamed: "):
                ast = self._tame_ast(name[len("Tamed: "):])
            elif name.startswith("Killed: "):
                ast = self._kill_ast(name[len("Killed: "):])
            elif name.startswith("Artifact: "):
                ast = self._cave_ast(name[len("Artifact: "):])
            elif name.startswith("Boss: "):
                ast = self._boss_ast(name[len("Boss: "):].split(" (")[0])
            else:
                continue
            if ast == ("false",):
                impossible.append(name)
        if impossible:
            shown = ", ".join(sorted(impossible)[:8])
            more = f" (+{len(impossible) - 8} more)" if len(impossible) > 8 else ""
            raise OptionError(
                f"ARK: {len(impossible)} location(s) kept for your maps but gated on content those "
                f"maps do not have: {shown}{more}. Either widen the requirement in tame_logic.json "
                f"so it lists a creature these maps do have, or tag the location to the map whose "
                f"content it really needs (Map column, docs/ADDING_A_MAP.md).")

    def _map_filter(self, kind: str):
        """Returns a predicate: is this id usable by THIS slot?

        FAIL-OPEN by design. An id maps.json has never heard of is kept, because the alternative -
        dropping anything untagged - turns "we forgot to tag a new category" into a silent content
        loss that generation reports as a headroom failure somewhere else entirely. Mod content and
        exploration regions are deliberately untagged here: mods are chosen by mod_ids, and regions
        carry their own per-entry "map" field, so both must survive this filter untouched."""
        buckets = self._map_content
        if not buckets:                                  # no maps.json = no filtering (pre-map behaviour)
            return lambda _i: True
        active = self._active_map_keys() | {"any"}
        allowed, known = set(), set()
        for key, bucket in buckets.items():
            ids = bucket.get(kind, ())
            known.update(ids)
            if key in active:
                allowed.update(ids)
        return lambda i: i in allowed or i not in known

    def _used_explore(self) -> dict:
        """region key -> entry, for the maps THIS slot runs. A region for a map the player is not
        on can never be reached, so it must not become a location for them."""
        cache = getattr(self, "_used_explore_cache", None)
        if cache is None:
            active = self._active_map_keys()
            cache = {k: r for k, r in self._explore.items() if r.get("map", "island") in active}
            self._used_explore_cache = cache
        return cache

    # gear a region physically demands -> the same expression language the caves use, so it
    # compiles through the recipe graph (Fur needs the Smithy, Scuba needs the Fabricator, ...).
    _EXPLORE_GATE = {"Fur": "Fur", "Scuba": "Scuba Tank"}

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
        # death_sanity: the cause-of-death checks. Same shape as the two above - a random subset per
        # seed - so 0 removes them entirely for anyone who would rather not be nudged into dying.
        pct = self.options.death_sanity.value
        if pct < 100:
            deaths = sorted(e["name"] for e in
                            self._locations["location_categories"].get("deaths", {})
                            .get("entries", []))
            keep = round(len(deaths) * pct / 100)
            excluded |= set(deaths) - set(self.random.sample(deaths, keep))
        # death_milestones: the cumulative "die N times" set. Deliberately its own option rather
        # than part of death_sanity - the cause checks happen while you play, whereas these reward
        # dying over and over, which is a different thing to opt out of.
        if not self.options.death_milestones.value:
            excluded |= {m["name"] for m in
                         self._locations["location_categories"].get("milestones", {})
                         .get("entries", [])
                         if m.get("tag", "").startswith("milestone_deaths_")}
        self._sanity_excluded_cache = excluded
        return excluded

    # locations actually used by THIS slot: first N dossiers (option) + all bosses + milestones.
    # The class-level location_name_to_id keeps the full set for the datapackage; unused ones
    # just hold no item (the plugin may still report them, AP harmlessly ignores).
    def _used_locations(self) -> Dict[str, int]:
        cats = self._locations["location_categories"]
        used: Dict[str, int] = {}
        # Take the first N notes THIS SLOT CAN REACH. The list holds every map's notes interleaved
        # by note index, so slicing before filtering would hand an Island player a window full of
        # Scorched notes, drop them again at the end, and quietly leave them with far fewer than N
        # locations - which surfaces much later as a headroom failure.
        keep_note = self._map_filter("locations")
        notes = [x for x in cats["dossiers"]["entries"] if keep_note(x["id"])][
            : self.options.dossier_checks.value]
        for e in notes:
            used[e["name"]] = e["id"]
        # NOTE: "bosses" is intentionally NOT here - boss kills are the goal (via boss_out.jsonl),
        # not item-bearing checks, so nothing gets stranded behind a boss kill.
        # "Collect N Explorer Notes" milestones scale with dossier_checks: you can't collect more
        # notes than exist as checks. dossier_checks=0 (notes off) drops them all, so a player who
        # turned notes off isn't stuck with impossible note-count milestones.
        # Gate the "Collect N Explorer Notes" milestones on the notes this slot ACTUALLY has, not on
        # the raw option. The option is a cap, and the real count is min(option, notes on your maps)
        # - so an Island slot asking for 400 still only has 232, and "Collect 250" must not appear.
        n_notes = len(notes)
        for key in ("milestones", "levels", "alpha_kills", "inventory_checks", "deaths"):
            for e in cats.get(key, {}).get("entries", []):
                tag = e.get("tag", "")
                if tag.startswith("milestone_notes_"):
                    try:
                        if int(tag.rsplit("_", 1)[1]) > n_notes:
                            continue
                    except ValueError:
                        pass
                used[e["name"]] = e["id"]
        for d in self._dinos.get("dinos", []):           # per-dino tame + kill checks
            short = d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")
            if d.get("tame_loc"):
                used["Tamed: " + short] = d["tame_loc"]
            if d.get("kill_loc"):
                used["Killed: " + short] = d["kill_loc"]
        for mod in self._active_mods().values():         # creature checks from ACTIVE mods only
            for d in mod.get("dinos", []):
                short = d.get("name") or d.get("ap_name", "").replace("Tame: ", "")
                if d.get("tame_loc"):
                    used["Tamed: " + short] = d["tame_loc"]
                if d.get("kill_loc"):
                    used["Killed: " + short] = d["kill_loc"]
        for r in self._used_explore().values():          # exploration checks for this slot's maps
            used["Explore: " + r["name"]] = r["id"]
        for name in self._sanity_excluded():             # tame_sanity / food_sanity drops
            used.pop(name, None)
        # Finally drop anything that belongs only to a map this slot is not running. A Scorched
        # note or a Ragnarok-only tame is unreachable on an Island server, and an unreachable
        # location either fails generation or strands an item behind it.
        keep = self._map_filter("locations")
        return {n: i for n, i in used.items() if keep(i)}

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

    # ---- mod support -----------------------------------------------------------------------
    # The catalog is ALWAYS in the datapackage (see the class-level tables); these helpers decide
    # what this SLOT actually uses. Different players in one multiworld may run different mod_ids.
    def _active_mods(self) -> Dict[str, dict]:
        cache = getattr(self, "_active_mods_cache", None)
        if cache is not None:
            return cache
        wanted = {str(m).strip() for m in self.options.mod_ids.value if str(m).strip()}
        # A fork that kept the parent's /Game/Mods/<folder>/ ships identical class paths, so one
        # catalog entry serves several workshop ids (Super Structures <- Structures Plus). Resolve
        # any alias to its canonical entry, and dedupe so listing both ids isn't counted twice.
        alias_to_canon = {a: m for m, d in self._mod_catalog.items() for a in d.get("aliases", [])}
        resolved = {alias_to_canon.get(m, m) for m in wanted}
        # Two ids that resolve to the SAME entry are alternative forks of one mod (Structures Plus
        # vs Super Structures). A server can only load one of them - and listing both would defeat
        # per-variant pooling, silently handing out the union incl. engrams the installed fork
        # doesn't ship. Make the player pick.
        for canon in sorted(resolved):
            listed = sorted(m for m in wanted if alias_to_canon.get(m, m) == canon)
            if len(listed) > 1:
                d = self._mod_catalog[canon]
                raise OptionError(
                    f"ARK: mod_ids lists {' and '.join(listed)}, which are alternative versions of "
                    f"the same mod ({d['name']}) - a server can only run one. Keep the id of the "
                    f"one you actually have installed and remove the other, or their exclusive "
                    f"engrams get mixed into your pool and won't exist in game.")
        unknown = sorted(m for m in resolved if m not in self._mod_catalog)
        if unknown:
            known = ", ".join(
                f"{m} ({d['name']})" + (f" [also {', '.join(d['aliases'])}]" if d.get("aliases") else "")
                for m, d in sorted(self._mod_catalog.items())) or "(none bundled yet)"
            raise OptionError(
                f"ARK: mod_ids lists {', '.join(unknown)}, which this apworld doesn't know. A mod ID "
                f"is just a number - the apworld can't know what engrams a mod adds unless its data "
                f"is bundled. Supported: {known}.")
        out = {m: self._mod_catalog[m] for m in resolved}
        # DECLARED ids only - NOT the alias-resolved ones. Variant pooling must know which FORK the
        # player actually runs; folding in the canonical id would match both variants and pool the
        # union again, which is exactly what the filter exists to prevent.
        self._declared_mod_ids = set(wanted)
        # A building mod adds hundreds of engrams but NO locations, so it only fits once
        # bundle_structures collapses the vanilla structure set (~190 slots freed).
        if not self.options.bundle_structures.value:
            big = [d for d in out.values() if d.get("kind") == "building"]
            if big:
                n = sum(len(d["engrams"]) for d in big)
                raise OptionError(
                    f"ARK: {', '.join(d['name'] for d in big)} adds {n} engrams, but with "
                    f"bundle_structures off the item pool already nearly fills every location. Set "
                    f"'bundle_structures: true' (it collapses ~197 structure engrams into bundle "
                    f"items, freeing the room).")
        self._active_mods_cache = out
        return out

    # Engrams that exist in the catalogue but which the player's actual mod does NOT ship. Two
    # workshop ids can share one /Game/Mods/<folder>/ (Structures Plus vs its fork Super Structures)
    # so the catalogue is their UNION; without this filter an SS player would receive 64 items whose
    # blueprint classes don't exist on their server - inert "you got Engram: X" that unlocks nothing.
    def _wrong_variant_names(self) -> set:
        cache = getattr(self, "_wrong_variant_cache", None)
        if cache is None:
            self._active_mods()                          # populates _declared_mod_ids
            declared = getattr(self, "_declared_mod_ids", set())
            cache = set()
            for mod in self._active_mods().values():
                for e in mod.get("engrams", []):
                    vs = e.get("variants")
                    if vs and not (set(vs) & declared):
                        cache.add(e["ap_name"])
            self._wrong_variant_cache = cache
        return cache

    # ap_names contributed by mods that are NOT active for this slot -> excluded from the pool and
    # from the used locations, exactly like unused dossiers.
    def _inactive_mod_names(self) -> set:
        cache = getattr(self, "_inactive_mod_cache", None)
        if cache is None:
            active = set(self._active_mods())
            cache = set()
            for mod_id, mod in self._mod_catalog.items():
                if mod_id in active:
                    continue
                cache |= {e["ap_name"] for e in mod.get("engrams", [])}
                cache |= {b["ap_name"] for b in mod.get("bundles", [])}
                for d in mod.get("dinos", []):
                    if d.get("ap_name"):
                        cache.add(d["ap_name"])
                    short = d.get("name") or (d.get("ap_name", "").replace("Tame: ", ""))
                    if d.get("tame_loc"):
                        cache.add("Tamed: " + short)
                    if d.get("kill_loc"):
                        cache.add("Killed: " + short)
            self._inactive_mod_cache = cache
        return cache

    # engrams contributed by this slot's ACTIVE mods (bundling + pooling must see these)
    def _active_mod_engrams(self) -> list:
        out = []
        for mod in self._active_mods().values():
            out.extend(mod.get("engrams", []))
        return out

    # engrams a mod says to grant free at start (only names that are real items in this seed).
    def _auto_grant_names(self) -> set:
        cache = getattr(self, "_auto_grant_cache", None)
        if cache is None:
            cache = {n for n in CORE_AUTO_GRANT if n in self.item_name_to_id}
            for mod in self._active_mods().values():
                for n in mod.get("auto_grant", []):
                    if n in self.item_name_to_id:
                        cache.add(n)
            self._auto_grant_cache = cache
        return cache

    # Caches that depend on how engrams are grouped. _fit_pool_to_locations drops these when it
    # changes the factor. Everything ELSE is left alone on purpose - _sanity_excluded_cache in
    # particular must never be dropped, because it is a per-seed random sample that create_regions
    # has already used to decide which locations exist; re-rolling it here would desync the two.
    _GROUPING_CACHES = ("_engram_groups_cache", "_tame_groups_cache", "_bundle_remap_cache",
                        "_tame_rep_cache", "_auto_grant_cache", "_free_items_cache",
                        "_group_forced_prog_cache", "_direct_cache", "_ride_map_cache",
                        "_tame_req_cache", "_ccg_cache")

    def _build_pool_names(self) -> list:
        """The item names this slot would pool at the current grouping factor."""
        skip = (self._nonpool_names() | self._engram_group_members() | self._tame_group_members())
        bundles = (structure_bundle_members(self._slot_engrams(), self._active_mod_engrams())
                   if self.options.bundle_structures.value else {})
        skip_bundle_items = ({b for b, m in bundles.items() if not m} if bundles
                             else set(STRUCTURE_BUNDLES))
        keep_item = self._map_filter("items")
        return [name for name, iid in self.item_name_to_id.items()
                if name not in self._filler_names and name not in skip
                and name not in skip_bundle_items and keep_item(iid)]

    def _fit_pool_to_locations(self, headroom: int) -> None:
        """Raise engram grouping until the pool fits the slot's locations.

        A map's locations are its own, but the ~549 "any" items - almost all engrams, craftable
        everywhere - go to EVERY slot. So a smaller map starts with more items than places to put
        them, and previously that was a hard error telling the player to set engrams_per_item
        themselves. Doing it for them is what lets any map be played on its own.

        Only ever tightens, never loosens: a slot that already fits keeps the player's setting."""
        if len(self._build_pool_names()) <= headroom:
            return
        start = self._engrams_per_item()
        for n in range(max(2, start + 1), 5):                # option range tops out at 4
            self._engrams_per_item_override = n
            for c in self._GROUPING_CACHES:
                self.__dict__.pop(c, None)
            if len(self._build_pool_names()) <= headroom:
                return
        # Out of grouping headroom - leave the highest factor set so the error names the real gap.

    def create_items(self) -> None:
        pool = []
        # _nonpool_names() = saddles / starters / tek / inactive-mod / wrong-variant / auto-grant /
        # structure-bundle / curated-mod-group members. Plus count-grouping folds every engram/tame
        # GROUP MEMBER out of the pool (its representative unlocks it - see _engram_groups).
        skip = (self._nonpool_names()
                | self._engram_group_members()
                | self._tame_group_members())
        # give the auto-grant engrams to the player up front (removed from the pool above); the AP
        # server sends precollected items on connect, so the plugin grants them in-game.
        for name in self._auto_grant_names():
            self.multiworld.push_precollected(self.create_item(name))
        bundles = (structure_bundle_members(self._slot_engrams(), self._active_mod_engrams())
                   if self.options.bundle_structures.value else {})
        # bundle items are pooled only when bundling is ON *and* the bundle actually has members -
        # Adobe/Glass only exist in building mods, so a vanilla slot must not carry a dead item.
        skip_bundle_items = ({b for b, m in bundles.items() if not m} if bundles
                             else set(STRUCTURE_BUNDLES))
        # (player-picked starting items: use the standard start_inventory_from_pool yaml option -
        #  AP core precollects them + swaps filler into the pool.)
        # one of each progression item (skip every filler/trap entry + any bundled saddles)
        # An item for a map this slot is not running has nothing to unlock - a Wyvern tame item on
        # an Island server is a dead progression item that eats a location and can gate a rule.
        keep_item = self._map_filter("items")
        for name, iid in self.item_name_to_id.items():
            if name in self._filler_names or name in skip or name in skip_bundle_items:
                continue
            if not keep_item(iid):
                continue
            pool.append(self.create_item(name))
        # pad to location count with a mix of traps and neutral filler (trap_percentage).
        # minus the locations pre_fill will lock a HARD_PLACED engram onto - they are not in the
        # pool, but they do take a slot.
        n_locations = len(self._used_locations()) - len(self._hard_placed_names())
        # The real budget is NOT just pool <= locations. Every EXCLUDED location (holograms, alphas,
        # big grind milestones, high levels, hard notes - see _regions_flat) can only hold
        # non-progression, so the pool must leave that many FILLER slots or AP fails later with
        # "Not enough filler items for excluded locations". Check it here, where we can name the
        # cause and the fix, instead of surfacing as a bare FillError.
        # Count only the excluded names that are ACTUALLY locations in this slot. The set is built
        # from the whole data table, so it lists names for content other maps have - and counting
        # those shrinks the headroom for a map that never had them. Adding two Scorched alphas cost
        # an Island slot two usable slots before this was intersected.
        n_excluded = len(self._excluded_progression_names() & set(self._used_locations()))
        headroom = n_locations - n_excluded
        if len(pool) > headroom:
            mods = ", ".join(d["name"] for d in self._active_mods().values()) or "none"
            raise ValueError(
                f"ARK: {len(pool)} items but only {headroom} usable slots "
                f"({n_locations} locations - {n_excluded} that must hold filler). "
                f"Active mods: {mods}. Engram grouping was already raised automatically to "
                f"{self._engrams_per_item()} and it still does not fit. Shrink the pool further "
                f"(bundle_structures: true, bundle_saddles: true, free_starter_engrams: true, "
                f"tames_per_item: 2), drop a large mod from mod_ids, or ADD locations "
                f"(raise dossier_checks / tame_sanity / food_sanity).")
        traps = [f["ap_name"] for f in self._filler.get("filler", []) if f.get("trap")]
        goods = [f["ap_name"] for f in self._filler.get("filler", []) if not f.get("trap")] or [FILLER_NAME]
        pct = self.options.trap_percentage.value
        while len(pool) < n_locations:
            use_trap = traps and self.random.randint(1, 100) <= pct
            bag = traps if use_trap else goods
            w = [self._filler_weight.get(n, 1) for n in bag]
            pool.append(self.create_item(self.random.choices(bag, weights=w, k=1)[0]))
        self.multiworld.itempool += pool

    # boss-defeat goal: "Broodmother Defeated" etc, derived from the boss location names.
    # A boss tag is "<base>_<difficulty>", and the plugin recovers the base by cutting at the LAST
    # underscore - so "Iceworm_Queen_Gamma" is the boss "Iceworm_Queen". Splitting on the FIRST
    # underscore instead yields "Iceworm", which matches nothing the plugin ever reports.
    @staticmethod
    def _boss_base(tag: str) -> str:
        return tag.rsplit("_", 1)[0] if "_" in tag else tag

    def _boss_events(self) -> Dict[str, str]:
        """Defeat events for bosses THIS SLOT can reach. Unfiltered, a Scorched-only slot would be
        asked to kill the Island's four and the seed is unbeatable."""
        keep = self._map_filter("locations")
        events: Dict[str, str] = {}
        for b in self._locations["location_categories"]["bosses"]["entries"]:
            if not keep(b["id"]):
                continue
            short = b["name"].replace("Boss: ", "").split(" (")[0]   # "Broodmother"
            events[short + " Defeated"] = b["name"]
        return events

    def _boss_base_order(self) -> list:
        """Base tags of this slot's reachable bosses, first-seen order (= location id order)."""
        keep = self._map_filter("locations")
        order, seen = [], set()
        for b in self._locations["location_categories"]["bosses"]["entries"]:
            base = self._boss_base(b["tag"])
            if base not in seen and keep(b["id"]):
                seen.add(base)
                order.append(base)
        return order

    def _goal_event_names(self) -> list:
        """The "X Defeated" events the goal requires - same bosses fill_slot_data sends the plugin,
        so the win condition and what the plugin counts can never disagree."""
        keep = self._map_filter("locations")
        short_of = {}
        for b in self._locations["location_categories"]["bosses"]["entries"]:
            base = self._boss_base(b["tag"])
            if keep(b["id"]) and base not in short_of:
                short_of[base] = b["name"].replace("Boss: ", "").split(" (")[0]
        return [short_of[t] + " Defeated"
                for t in self._goal_boss_tags(self._boss_base_order()) if t in short_of]

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
        # DINO_TIER covers the Island roster. A creature added through the checklist workbook
        # (import_checklist.py) carries its tier on its dinos.json entry instead, so a new map does
        # not need a code edit to place its creatures on the tier ladder.
        base = DINO_TIER.get(short, self._dino_tiers_from_data().get(short, 0))
        if base == 0 and short in self.options.tier0_remove.value:
            return 1
        return base

    def _dino_tiers_from_data(self) -> Dict[str, int]:
        m = getattr(self, "_dino_tiers_cache", None)
        if m is None:
            m = {self._dino_short(d): int(d["tier"])
                 for d in self._dinos.get("dinos", []) if d.get("tier")}
            self._dino_tiers_cache = m
        return m

    # ---- tame/craft ACCESS LOGIC (softlock prevention) ----
    # A "Tamed: X" check requires the engrams X's taming method needs (from data/tame_logic.json,
    # expanded through the recipe graph). Dinos the sheet doesn't cover fall back to DINO_TIER.
    def _tame(self):
        tl = getattr(self, "_tame_cache", "?")
        if tl == "?":
            data = self._tame_logic_data
            if data.get("dino_tame_raw"):
                # Merge ACTIVE mods' engram prerequisites into the recipe graph, so anything that
                # depends on a mod engram also demands its prereq (e.g. Note Tracker needs the GPS).
                extra_alias, extra_recipes = {}, {}
                for mod in self._active_mods().values():
                    for e in mod.get("engrams", []):
                        req = e.get("requires")
                        if not req:
                            continue
                        short = e["ap_name"].replace("Engram: ", "")
                        extra_alias[short] = short
                        extra_recipes[short] = " + ".join(r.replace("Engram: ", "") for r in req)
                        for r in req:
                            extra_alias.setdefault(r.replace("Engram: ", ""),
                                                   r.replace("Engram: ", ""))
                if extra_alias:
                    data = dict(data)
                    data["alias"] = {**data.get("alias", {}), **extra_alias}
                    data["item_recipes"] = {**data.get("item_recipes", {}), **extra_recipes}
                tl = TameLogic(data)
            else:
                tl = None
            self._tame_cache = tl
        return tl

    @staticmethod
    def _dino_short(d: dict) -> str:
        return d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")

    # engram ap_name -> its bundle item name when bundle_structures removes it from the pool
    # (else the rule's has(<engram>) would be unsatisfiable). Cached per world. Also folds in the
    # count-grouping (engrams_per_item): a folded member engram maps to its group representative.
    def _bundle_remap(self):
        m = getattr(self, "_bundle_remap_cache", None)
        if m is None:
            m = {}
            if self.options.bundle_structures.value:
                for bundle, members in structure_bundle_members(
                        self._slot_engrams(), self._active_mod_engrams()).items():
                    for mem in members:
                        m[mem] = bundle
            for rep, members in self._engram_groups().items():   # member engram -> its group rep
                for mem in members:
                    m[mem] = rep
            self._bundle_remap_cache = m
        return lambda short: m.get("Engram: " + short, "Engram: " + short)

    # ---- count-grouping (engrams_per_item / tames_per_item) ----------------------------------
    # Every item name is STATIC in the datapackage; the options only change which get pooled, the
    # per-slot logic remap, and the per-slot slot_data the plugin uses to unlock a group's members.
    # So different players can pick different group sizes without breaking the shared datapackage.

    # item names create_items removes from the pool BEFORE count-grouping (so grouping only ever
    # touches engrams that would actually be pooled as individual unlocks).
    def _nonpool_names(self) -> set:
        skip = (self._bundled_saddle_names() | self._free_starter_names() | self._tek_names
                | self._inactive_mod_names() | self._wrong_variant_names() | self._auto_grant_names()
                | {n for n in HARD_PLACED if n in self.item_name_to_id})   # locked onto early kills
        if self.options.bundle_structures.value:
            for members in structure_bundle_members(
                    self._slot_engrams(), self._active_mod_engrams()).values():
                skip |= members
        for mod in self._active_mods().values():
            for b in mod.get("bundles", []):
                skip |= set(b["members"])
        skip |= self._variant_member_names()   # mod "<X> Plus" folded into vanilla X (variant pairing)
        return skip

    # S+/Super Structures variant pairing: with a building MOD active, its "<Name> Plus" / "<Name>
    # Tek" engram folds into the matching vanilla engram (Campfire Plus -> Campfire, ...), so ONE
    # unlock grants BOTH the vanilla structure and its mod variant. Reuses item_groups (vanilla =
    # representative, mod variant = folded member). Material-bundle structures already pair via
    # bundle_structures; these are the individual utility structures that don't.
    def _variant_pairs(self) -> dict:
        cache = getattr(self, "_variant_pairs_cache", None)
        if cache is None:
            cache = {}
            vanilla = {e["ap_name"] for e in self._engrams["engrams"]}

            def vanilla_name_for(nm):
                """The vanilla engram this mod engram is a variant OF, or None."""
                if nm in VARIANT_ALIAS:                     # curated renames (Smithy Plus -> Anvil Bench)
                    return VARIANT_ALIAS[nm]
                for suf in VARIANT_SUFFIXES:                # "<X> Plus" / " Tek" / " SS" / " SP"
                    if nm.endswith(suf):
                        return nm[: -len(suf)]
                if " Plus " in nm:                          # infix: "Crop Plot Plus Large" -> "Crop Plot Large"
                    return nm.replace(" Plus ", " ", 1)
                return None

            for e in self._active_mod_engrams():
                nm = e["ap_name"]
                base = vanilla_name_for(nm)
                if base and base != nm and base in vanilla and base in self.item_name_to_id:
                    cache.setdefault(base, []).append(nm)
            self._variant_pairs_cache = cache
        return cache

    def _variant_member_names(self) -> set:
        out: set = set()
        for members in self._variant_pairs().values():
            out.update(members)
        return out

    def _engram_ap_names(self) -> set:
        names = {e["ap_name"] for e in self._engrams["engrams"]}
        names |= {e["ap_name"] for e in self._active_mod_engrams()}
        return names

    # rep ap_name -> [folded member ap_names]. Engrams chunked in id order (= dump / tech-tree /
    # progression order), so a group holds progression-adjacent engrams.
    # The grouping factor actually in force. Normally the yaml option, but create_items raises it
    # when a slot has fewer locations than items - see _fit_pool_to_locations.
    def _engrams_per_item(self) -> int:
        return getattr(self, "_engrams_per_item_override", None) or self.options.engrams_per_item.value

    def _engram_groups(self) -> dict:
        cache = getattr(self, "_engram_groups_cache", None)
        if cache is None:
            n = self._engrams_per_item()
            cache = {}
            if n > 1:
                skip = self._nonpool_names()
                loose = [nm for nm in self._engram_ap_names()
                         if nm in self.item_name_to_id and nm not in skip]
                loose.sort(key=lambda nm: self.item_name_to_id[nm])
                for i in range(0, len(loose), n):
                    chunk = loose[i:i + n]
                    if len(chunk) > 1:
                        cache[chunk[0]] = chunk[1:]
            self._engram_groups_cache = cache
        return cache

    # rep ap_name -> [folded member ap_names]. Tames chunked WITHIN a progression tier (DINO_TIER),
    # so a group never mixes a start creature with an endgame one.
    def _tame_groups(self) -> dict:
        cache = getattr(self, "_tame_groups_cache", None)
        if cache is None:
            n = self.options.tames_per_item.value
            cache = {}
            if n > 1:
                buckets: Dict[int, list] = {}
                for nm in self._tame_item_names:
                    if nm not in self.item_name_to_id:
                        continue
                    short = nm[len("Tame: "):] if nm.startswith("Tame: ") else nm
                    buckets.setdefault(self._dino_tier(short), []).append(nm)
                for tier in sorted(buckets):
                    items = sorted(buckets[tier], key=lambda nm: self.item_name_to_id[nm])
                    for i in range(0, len(items), n):
                        chunk = items[i:i + n]
                        if len(chunk) > 1:
                            cache[chunk[0]] = chunk[1:]
            self._tame_groups_cache = cache
        return cache

    def _engram_group_members(self) -> set:
        out: set = set()
        for members in self._engram_groups().values():
            out.update(members)
        return out

    def _tame_group_members(self) -> set:
        out: set = set()
        for members in self._tame_groups().values():
            out.update(members)
        return out

    # group reps that must be progression:
    #   engram rep - a FOLDED member gates a tame/kill/cave (has(member) remaps to has(rep)).
    #   tame rep   - with lock_taming, the rep gates a "Tamed: X" for itself or a folded member; a
    #                rep that happens to be a NO_TAME_LOGIC tame (normally 'useful') would otherwise
    #                leave that Tamed location unreachable in the accessibility check.
    def _group_forced_progression(self) -> set:
        cache = getattr(self, "_group_forced_prog_cache", None)
        if cache is None:
            req = self._tame_required_items()
            cache = {rep for rep, members in self._engram_groups().items()
                     if any(m in req for m in members)}
            if self.options.lock_taming.value:
                no_logic = {"Tame: " + d for d in NO_TAME_LOGIC}
                for rep, members in self._tame_groups().items():
                    if any(t not in no_logic for t in [rep] + members):
                        cache.add(rep)
            self._group_forced_prog_cache = cache
        return cache

    # a tame item name -> its group representative (itself if ungrouped / a rep).
    def _tame_rep_of(self, item: str) -> str:
        m = getattr(self, "_tame_rep_cache", None)
        if m is None:
            m = {}
            for rep, members in self._tame_groups().items():
                for mem in members:
                    m[mem] = rep
            self._tame_rep_cache = m
        return m.get(item, item)

    # {rep_item_id(str): [member_item_ids]} for slot_data - the plugin unlocks a group's members
    # when the representative arrives (they are never pooled, so AP never sends them directly).
    def _item_groups_slotdata(self) -> dict:
        groups: Dict[str, set] = {}   # rep ap_name -> set(member ap_names)
        for rep, members in self._engram_groups().items():
            groups.setdefault(rep, set()).update(members)
        for rep, members in self._tame_groups().items():
            groups.setdefault(rep, set()).update(members)
        # variant pairs fold the mod "<X> Plus" into vanilla X. If vanilla X is itself a folded
        # count-group member, attach the variant to whatever rep actually GRANTS X, so the chain
        # still delivers it (member -> its rep).
        member_to_rep = {m: r for r, ms in groups.items() for m in ms}
        for vanilla, variants in self._variant_pairs().items():
            actual = member_to_rep.get(vanilla, vanilla)
            groups.setdefault(actual, set()).update(variants)
        out: Dict[str, list] = {}
        for rep, members in groups.items():
            if rep in self.item_name_to_id:
                out[str(self.item_name_to_id[rep])] = sorted(
                    self.item_name_to_id[m] for m in members if m in self.item_name_to_id)
        return out

    # {non-pooled item id -> the POOLED item id that unlocks it}, for /hint. AP can only hint items
    # it actually PLACED; anything folded into a bundle/group was removed from the pool, so hinting
    # it errors with "item doesn't exist in the multiworld". This covers EVERY fold the apworld does:
    # count-groups, S+ variant pairs, material structure bundles, curated mod groups, and saddles
    # bundled with their tame. The plugin uses it to redirect the hint to the item you should chase.
    def _tracker_groups_slotdata(self) -> dict:
        """rep item id -> [every member item id it unlocks], for the TRACKER.

        Archipelago sends one item per location, but a single ARK item can unlock many engrams (a
        count-group representative, a bundle_structures material bundle, a mod bundle, or a saddle
        bundled with its tame). PopTracker only sees the one item on the wire, so it needs the full
        expansion to light up the members. `item_groups` covers only count-groups + variants;
        hint_redirect (member -> rep) is the COMPLETE relationship, so invert it - that folds in the
        structure/mod/saddle bundles too, with chains already resolved to the real pooled rep."""
        groups: Dict[str, list] = {}
        for member_id, rep_id in self._hint_redirect_slotdata().items():
            groups.setdefault(str(rep_id), []).append(int(member_id))
        for rep in groups:
            groups[rep] = sorted(set(groups[rep]))
        return groups

    def _hint_redirect_slotdata(self) -> dict:
        out: Dict[str, int] = {}
        ids = self.item_name_to_id

        def add(member_name, rep_name):
            if member_name in ids and rep_name in ids:
                out[str(ids[member_name])] = ids[rep_name]

        for rep_id, members in self._item_groups_slotdata().items():   # count-groups + variants
            for m in members:
                out[str(m)] = int(rep_id)
        if self.options.bundle_structures.value:                       # material structure bundles
            for bundle, members in structure_bundle_members(
                    self._slot_engrams(), self._active_mod_engrams()).items():
                if members:
                    for m in members:
                        add(m, bundle)
        for mod in self._active_mods().values():                       # curated per-mod groups
            for b in mod.get("bundles", []):
                for m in b.get("members", []):
                    add(m, b["ap_name"])
        if self.options.bundle_saddles.value:                          # saddle rides with the tame
            by_class = {d["saddle_class"]: d for d in self._dinos.get("dinos", [])
                        if d.get("saddle_class") and d.get("ap_name")}
            for e in self._engrams["engrams"]:
                d = by_class.get(e["engram_class"])
                if d:
                    add(e["ap_name"], d["ap_name"])
        # follow chains so the target is always a POOLED item (saddle -> Tame: X -> that tame's
        # count-group representative; a structure engram -> its bundle; etc.).
        for _ in range(5):
            changed = False
            for k, v in list(out.items()):
                nxt = out.get(str(v))
                if nxt is not None and nxt != v and str(nxt) != k:
                    out[k] = nxt
                    changed = True
            if not changed:
                break
        return out

    # ap_item_names that are auto-granted (never in the pool) -> tame logic treats has(x) as always
    # true for them, else a requirement on a start engram (e.g. Waterskin) would strand the location.
    def _free_items(self) -> set:
        m = getattr(self, "_free_items_cache", None)
        if m is None:
            m = self._free_starter_names() | self._bundled_saddle_names()
            self._free_items_cache = m
        return m

    def _slot_engrams(self) -> dict:
        """self._engrams narrowed to the engrams this slot's maps actually have.

        Structure bundles are computed from THIS, not the full table. Adobe pieces are real vanilla
        engrams on Scorched Earth, so once they exist in engrams.json the Adobe bundle would stop
        being empty for everyone - and an Island-only slot would pool a bundle item that unlocks
        nothing it can reach. Filtering here keeps that bundle empty where it should be, and
        create_items already refuses to pool an empty bundle."""
        cached = getattr(self, "_slot_engrams_cache", None)
        if cached is None:
            keep = self._map_filter("items")
            cached = dict(self._engrams)
            cached["engrams"] = [e for e in self._engrams["engrams"] if keep(e["id"])]
            self._slot_engrams_cache = cached
        return cached

    def _missing_items(self) -> frozenset:
        """AP item names this slot can never receive because they belong to another map.

        The mirror of _free_items(): free items are always held, these are never held. Both have to
        be known to the compiler, because both turn a has() leaf into a constant - and a leaf left
        as has(<unreachable item>) silently strands whatever it gates."""
        cached = getattr(self, "_missing_items_cache", None)
        if cached is None:
            keep = self._map_filter("items")
            cached = frozenset(n for n, i in self.item_name_to_id.items() if not keep(i))
            self._missing_items_cache = cached
        return cached

    # AST rule for taming a roster dino ('true' = no requirement, 'false' = not on this slot's maps).
    def _tame_ast(self, short: str):
        tl = self._tame()
        if not tl:
            return ("true",)
        return tl.compile(tl.dino_expr(short, self._dino_tier(short)), self._bundle_remap(),
                          self._free_items(), self._direct_nodes(), self._missing_items())

    def _compile_expr(self, expr: str):
        # `direct` must be passed here too: the sheet's CAVE requirements use the same Ride<X> /
        # bare-creature nodes as the kill table (Useful<Cave>Tame mount lists), and this is what
        # compiles caves, bosses, tributes and note-caves.
        tl = self._tame()
        return tl.compile(expr, self._bundle_remap(), self._free_items(),
                          self._direct_nodes(), self._missing_items()) if tl else ("true",)

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

    # ---- Lurch's kill logic: Ride<X> / bare-creature nodes -> exact AP item names --------------
    # The sheet's combat macros are built from Ride<X> ("tame X AND hold X's saddle") and, for the
    # handful of creatures ARK lets you ride bareback, a bare creature name ("just tame X").
    # Resolved here rather than in tame_logic.py because it needs dinos.json (saddle_class) plus the
    # count-grouping remaps, so the rule always names an item the fill can actually place.
    @staticmethod
    def _ride_key(s: str) -> str:
        return re.sub(r"[^a-z]", "", s.lower())

    def _ride_map(self) -> dict:
        cache = getattr(self, "_ride_map_cache", None)
        if cache is not None:
            return cache
        remap = self._bundle_remap()
        # saddle engram class -> its ap_name, so a dino's saddle_class resolves to a real item
        cls_to_engram = {e["engram_class"]: e["ap_name"] for e in self._engrams["engrams"]}
        by_key = {}
        for d in self._dinos.get("dinos", []):
            if not d.get("ap_name"):
                continue                                    # kill-only creature: nothing to ride
            short = self._dino_short(d)
            saddle = cls_to_engram.get(d.get("saddle_class") or "")
            by_key[self._ride_key(short)] = (d["ap_name"], saddle)
        cache = {}
        for key, (tame_item, saddle) in by_key.items():
            tame = self._tame_rep_of(tame_item)             # tames_per_item representative
            cache[key] = [tame]                             # bare name = tame only
            ride = [tame]
            if saddle:
                ride.append(remap(saddle[len("Engram: "):]))   # engrams_per_item representative
            cache["ride" + key] = ride
        self._ride_map_cache = cache
        return cache

    def _direct_nodes(self) -> dict:
        """node name (as written in the sheet) -> AP item names that must ALL be held."""
        cache = getattr(self, "_direct_cache", None)
        if cache is None:
            rm = self._ride_map()
            cache = {}
            for node in self._logic_nodes():
                key = self._ride_key(node)
                if key in rm:
                    cache[node] = rm[key]
            self._direct_cache = cache
        return cache

    # every node name appearing anywhere in the kill/cave/macro expressions (so _direct_nodes only
    # has to resolve names that are actually used).
    def _logic_nodes(self) -> set:
        cache = getattr(self, "_logic_nodes_cache", None)
        if cache is None:
            d = self._tame_logic_data
            cache = set()
            for src in (d.get("kill_reqs", {}), d.get("item_recipes", {}),
                        d.get("cave_reqs", {}), d.get("dino_tame_raw", {})):
                for expr in src.values():
                    for t in re.split(r"[+|()]", str(expr)):
                        t = t.strip()
                        if t:
                            cache.add(t)
            self._logic_nodes_cache = cache
        return cache

    # KILL requirement AST for a roster creature ('true' = no requirement / not in the table).
    def _kill_ast(self, short: str, tag: str = ""):
        tl = self._tame()
        if not tl:
            return ("true",)
        expr = self._kill_expr(short, tag)
        # CAVE DWELLERS: killing one also means getting INTO its cave. Lurch's kill table encodes
        # only the weapon/mount, so a cave creature whose expression allows plain melee (Araneo,
        # Dung Beetle) would otherwise be sphere-0 despite his sheet marking it sphere 2. AND in the
        # same survival floor we already use for taming them.
        floor = self._tame_logic_data.get("cave_tames", {}).get(short, "")
        if floor:
            expr = f"({expr}) + ({floor})" if expr else floor
        if not expr:
            return ("true",)
        return tl.compile(expr, self._bundle_remap(), self._free_items(), self._direct_nodes(),
                          self._missing_items())

    def _kill_expr(self, short: str, tag: str = "") -> str:
        m = getattr(self, "_kill_expr_map", None)
        if m is None:
            m = {}
            for name, expr in self._tame_logic_data.get("kill_reqs", {}).items():
                m[self._ride_key(name)] = expr
            self._kill_expr_map = m
        alias = self._tame_logic_data.get("dino_alias", {}).get(short, "")
        for cand in (short, alias, tag or ""):
            if cand and self._ride_key(cand) in m:
                return m[self._ride_key(cand)]
        return ""

    # cave requirement AST for an artifact short name (e.g. "Hunter").
    def _cave_ast(self, art: str):
        return self._compile_expr(self._tame_logic_data.get("cave_reqs", {}).get(art, ""))

    # boss reachability AST: a boss needs all its artifacts' caves done; Overseer needs the 3
    # island bosses defeated. Boss kills are the goal, gated here so the win requires real prep.
    def _boss_ast(self, boss_short: str):
        arts = self._tame_logic_data.get("boss_artifacts", {}).get(boss_short)
        if arts:
            kids = [k for k in (self._cave_ast(a) for a in arts) if k != ("true",)]
            # Some bosses also demand TRIBUTE items on top of the artifacts, and those come off a
            # creature. The Manticore's portal wants 2/10/20 Fire + Lightning + Poison Talon
            # (gamma/beta/alpha) as well as its three artifacts, and every talon drops from a
            # Wyvern - so "can reach the Manticore" means "can kill a Wyvern", which the artifact
            # caves alone never implied. Uses _tame_ast, not _compile_expr: a creature name is not
            # an item name, and _compile_expr would silently collapse an unknown token to true.
            for dino in self._tame_logic_data.get("boss_tribute_dino", {}).get(boss_short, []):
                k = self._tame_ast(dino)
                if k and k != ("true",):
                    kids.append(k)
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
    # The two UNDERWATER artifact caves. Lurch's cave rule for both offers "| Diplocaulus" as an
    # alternative to full dive gear - reasonable for the cave RUN, since a Diplo supplies oxygen -
    # but far too generous for a NOTE sitting on the sea floor: a Diplocaulus is tamed in shallow
    # water, so the shortcut made every note in those caves look sphere-1 and progression got
    # placed on them (live report: "Dossier: Titanomyrma" holding another player's Narcotic).
    # Notes there additionally require real diving gear.
    _DEEP_WATER_CAVES = {"Brute", "Cunning"}

    def _note_ast(self, key: str):
        if key == "underwater":
            return self._compile_expr("Rifle KO + Scuba Tank")
        if key == "tek":
            return self._boss_ast("Overseer")
        cave = self._cave_ast(key)                     # a land artifact cave
        if key in self._DEEP_WATER_CAVES:
            dive = self._compile_expr("Scuba Tank")
            if dive != ("true",):
                return dive if cave == ("true",) else ("and", [cave, dive])
        return cave

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
                # Lurch's kill table: its Ride<X> nodes name TAME items and SADDLE engrams, so those
                # must be progression too or the accessibility sweep can never satisfy a kill rule.
                asts += [self._kill_ast(self._dino_short(d), d.get("dino_tag") or "")
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
        self._active_mods()          # validate mod_ids HERE so a bad option fails fast and clearly,
                                     # instead of surfacing as a stack trace mid-create_regions
        self._check_maps_can_carry_a_slot()
        self._check_rules_reachable()
        # Fit the pool to this slot's locations BEFORE create_regions runs. The tier gates cache
        # their engram names through _bundle_remap while regions are built, so regrouping later
        # would leave a gate naming an engram that is no longer in the pool - the region then never
        # opens and the fill dies with "no more spots" rather than anything informative.
        used = self._used_locations()
        self._fit_pool_to_locations(len(used) - len(self._excluded_progression_names() & set(used)))
        glob = self.options.station_placement.value == StationPlacement.option_global_early
        target = self.multiworld.early_items if glob else self.multiworld.local_early_items
        for name in self._extra_early_names():
            target[self.player][name] = 1

    # (tiered placement) place each tier gate on a KILL or LEVEL check in the tier it opens FROM
    # (gate i in Tier i: Smithy in T0, Forge in T1, Fabricator in T2). Locking them here keeps them
    # in this world (never wait on a friend) and correctly ordered. Only kills/levels are eligible:
    # notes are tedious to hunt, and TAMES are locked behind a Tame: X item (lock_taming), so a gate
    # on a tame wouldn't be reachable early.
    # HARD_PLACED engrams are locked straight onto a location in pre_fill, so each one CONSUMES a
    # location without ever being in the pool. create_items has to reserve those slots or it pads
    # filler right up to the location count and the fill ends one item over - which is exactly what
    # every seed did: "items 747, locations 746, Unplaced items(1)". Generation still finished, so
    # it read as a warning rather than the off-by-one it was.
    def _hard_placed_names(self) -> set:
        used = self._used_locations()
        if not any(f"Killed: {h}" in used for h in EARLY_KILL_HOSTS):
            return set()                       # no host to lock onto - pre_fill will skip them all
        already_free = self._free_starter_names()
        return {n for n in HARD_PLACED
                if n in self.item_name_to_id and n not in already_free}

    def pre_fill(self) -> None:
        # HARD_PLACED core engrams (Campfire): not free, not a random-pool item - lock each onto an
        # easy early-dino KILL so it's trivially early but still earned in-world. Killed: X on a weak
        # dino is sphere-0 (no gate), so the chains that need it are available from the start.
        used = self._used_locations()
        taken: set = set()
        # free_starter_engrams hands the starter set over at spawn, and Campfire is in BOTH lists.
        # Hard-placing an engram the player already owns wastes a location and shows up in the
        # spoiler as an unlock for something that was never locked - so skip anything already free.
        already_free = self._free_starter_names()
        for name in sorted(HARD_PLACED):
            if name not in self.item_name_to_id or name in already_free:
                continue
            hosts = [f"Killed: {h}" for h in EARLY_KILL_HOSTS
                     if f"Killed: {h}" in used and f"Killed: {h}" not in taken]
            if not hosts:
                continue
            pick = self.random.choice(hosts)
            taken.add(pick)
            self.multiworld.get_location(pick, self.player).place_locked_item(self.create_item(name))
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
        # progression_tiers ON -> DS3-style region chain (Menu -> T0 -> T1 -> T2 -> T3, notes behind
        # T2): sphere depth comes from region topology, so the playthrough orders like a real tech
        # climb. OFF -> single flat region; ordering comes only from the per-location access rules.
        menu = Region("Menu", self.player, self.multiworld)
        regions = [menu]
        if self.options.progression_tiers.value:
            self._regions_tiered(menu, regions)
        else:
            self._regions_flat(menu, regions)
        self.multiworld.regions += regions

    # engram (or its group rep) that gates a crafted "Collect N" check -> must be progression, else
    # the accessibility sweep can't reach that check.
    def _crafted_collect_engrams(self) -> set:
        cache = getattr(self, "_ccg_cache", None)
        if cache is None:
            remap = self._bundle_remap()
            cache = {remap(e[len("Engram: "):]) if e.startswith("Engram: ") else e
                     for e in CRAFTED_COLLECT_ENGRAM.values()}
            self._ccg_cache = cache
        return cache

    # every effective item name any tier-entrance rule requires (for classification: they must be
    # progression or the accessibility sweep can never open T1+).
    def _tier_gate_items(self) -> set:
        cache = getattr(self, "_tier_gate_cache", None)
        if cache is None:
            cache = set()
            for gates in TIER_GATES:
                cache.update(self._gate_items(gates))
            self._tier_gate_cache = cache
        return cache

    # The effective AP item names a tier gate requires. A gate engram may be non-pooled: folded into
    # a count-group (engrams_per_item -> require its REPRESENTATIVE, which is what actually grants
    # it) or free (starter/auto-grant -> no requirement at all). Mirrors what _bundle_remap does for
    # the tame-logic rules, so the entrance rules never demand an item AP can't deliver.
    def _gate_items(self, gates) -> tuple:
        remap = self._bundle_remap()
        free = self._free_items() | self._auto_grant_names() | HARD_PLACED
        out = []
        for g in gates:
            if g in free:
                continue
            out.append(remap(g[len("Engram: "):]) if g.startswith("Engram: ") else g)
        return tuple(out)

    # progression_tiers: 4 regions T0->T1->T2->T3, each gated by the prior station engram(s) -
    # exactly the DS3 pattern (region graph + entrance rules) mapped onto ARK's TECH topology.
    # Every check lives in its tier (explicit data tier / DINO_TIER / level band / boss=3).
    # Explorer notes are pulled behind TIER 2 (needs BOTH T0->T1 and T1->T2 gates transitively -
    # genuinely 2 rounds deep, sphere-2+) so ARK's sphere-0 set = ONLY T0 kills + low levels. A
    # single-hop gate (e.g. "any early tame") isn't deep enough - AP's early-item placement still
    # treats sphere-1 as "early". Sphere-2+ is deep enough that another game's early-forced item
    # (e.g. DS3 early_banner) can only land on a T0 kill/level-up here - never a note, never T1+.
    # The existing tame/kill/cave access rules (set_rules) still apply ON TOP, like DS3 layering
    # location rules (Lift Chamber Key) over region access.
    def _regions_tiered(self, menu: Region, regions: list) -> None:
        tiers = [Region(f"Tier {i}", self.player, self.multiworld) for i in range(4)]
        notes = Region("Explorer Notes", self.player, self.multiworld)
        regions.extend(tiers)
        regions.append(notes)
        menu.connect(tiers[0])
        for i, gates in enumerate(TIER_GATES):             # T0->T1 needs Anvil Bench + Mortar And Pestle, etc.
            req = self._gate_items(gates)
            tiers[i].connect(tiers[i + 1], f"Tier {i} -> Tier {i + 1}",
                             rule=(lambda state, g=req: state.has_all(g, self.player)) if req else None)
        tiers[2].connect(notes, "Tier 2 -> Explorer Notes")   # inherits gates 0 AND 1 transitively
        excluded_progression = self._excluded_progression_names()
        for loc_name, loc_id in self._used_locations().items():
            parent = notes if self._is_note(loc_name) else tiers[self._tier_of(loc_name)]
            loc = ArkLocation(self.player, loc_name, loc_id, parent)
            if loc_name in excluded_progression:
                loc.progress_type = LocationProgressType.EXCLUDED
            parent.locations.append(loc)
        for ev_name in self._boss_events():                # boss events live where bosses do (T3)
            ev = ArkLocation(self.player, ev_name, None, tiers[3])
            ev.place_locked_item(ArkItem(ev_name, ItemClassification.progression, None, self.player))
            tiers[3].locations.append(ev)

    # Locations that may hold ONLY filler/useful, never progression. Shared by _regions_flat
    # (which marks them EXCLUDED) and create_items (which must leave enough filler for them).
    def _excluded_progression_names(self) -> set:
        cache = getattr(self, '_excl_prog_cache', None)
        if cache is not None:
            return cache
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
        # (HLN-A Discovery / Genesis Chronicles were REMOVED as locations entirely - they need the
        # Genesis-DLC-only HLN-A skin to collect - so they no longer need excluding here.)
        HARD_NOTE_PREFIXES = ("Hologram: ", "??? Note")
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
        # ...and the TAME checks themselves: Carcharodontosaurus / Rhyniognatha are end-game tames
        # (playtest: progression landed on Tamed: Carcharodontosaurus, which is an enormous ask).
        # Same reasoning as the alpha kills - too hard to sit key progression behind. The Tame item
        # + check still exist; the check just holds filler.
        excluded_progression |= {"Tamed: Carcharodontosaurus", "Tamed: Rhyniognatha"}
        # KILL checks for the giants that realistically need a bred combat MOUNT to kill - a weapon
        # floor understates them (playtest: Killed: Carcharodontosaurus held progression at sphere 2,
        # but you can't solo a Carcha with a crossbow). Filler-only, same as the alpha kills.
        excluded_progression |= {"Killed: Carcharodontosaurus", "Killed: Giganotosaurus",
                                 "Killed: Titanosaur", "Killed: Rhyniognatha"}
        # Unicorn (a rare wandering spawn you may never find) + Yeti (Gigantopithecus, a nasty
        # snow-cave apex) - too luck/gear-dependent to sit key progression behind.
        excluded_progression |= {"Tamed: Unicorn", "Killed: Unicorn", "Killed: Yeti"}
        for loc_name in self._used_locations():
            if loc_name.startswith("Reach Level "):
                try:
                    if int(loc_name.rsplit(" ", 1)[1]) > 70:
                        excluded_progression.add(loc_name)
                except ValueError:
                    pass
            elif loc_name.startswith(HARD_NOTE_PREFIXES):
                excluded_progression.add(loc_name)
            # (crafted-resource "Collect N" checks are no longer excluded - they're GATED behind their
            #  crafting engram in set_rules, which prevents the self-circular placement safely.)
        self._excl_prog_cache = excluded_progression
        return excluded_progression

    # Single open region. Logical ordering + softlock-safety come from the tame/craft ACCESS RULES
    # (set_rules), not region gating. early_dino_checks is RETIRED (its aggressive EXCLUDE overlay
    # was built for the old tier model and starves the now progression-heavy pool of filler).
    # Boss-defeat events live here; their reachability is gated in set_rules (Crossbow KO floor).
    def _regions_flat(self, menu: Region, regions: list) -> None:
        island = Region("The Island", self.player, self.multiworld)
        regions.append(island)
        excluded_progression = self._excluded_progression_names()
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

    def _goal_boss_tags(self, order: list) -> list:
        """Which boss base-tags this slot must defeat, from the bosses it can actually reach.

        The historical options are cumulative over the Island's four. `all_bosses_all_maps` instead
        means every boss on the maps you enabled - which on an Island-only slot is exactly the same
        four, so the option is safe to pick without knowing your map list."""
        if self.options.goal.value >= Goal.option_all_bosses_all_maps:
            return order
        # Keep the historical order for the cumulative options: Broodmother, Megapithecus, Dragon,
        # Overseer. Sorting `order` by that list keeps them first even on a cluster, so a Scorched
        # cluster with goal=all_bosses still means the Island four, not "the first four found".
        rank = {t: i for i, t in enumerate(
            ["SpiderBoss", "GorillaBoss", "DragonBoss", "Overseer"])}
        ranked = sorted(order, key=lambda t: rank.get(t, 99))
        return ranked[: self._goal_bosses()]

    def set_rules(self) -> None:
        # Every "skip this one" test below reads `excluded`, so fold the map filter into it rather
        # than guarding a dozen get_location() calls individually. The loops here walk the FULL data
        # tables (all dinos, all milestones), not this slot's location set - so without this, a
        # creature belonging to another map raises KeyError: 'Tamed: Griffin' on an Island slot,
        # because the location was correctly never created.
        excluded = self._sanity_excluded() | (set(self.location_name_to_id) -
                                              set(self._used_locations()))
        # Crafted "Collect N <resource>" checks require the engram/station that makes the resource -
        # you can't hold 100 sparkpowder without the Sparkpowder engram. Blocks the self-circular fill
        # (Engram: Sparkpowder landing on Collect Sparkpowder). Remapped for count-grouping.
        remap = self._bundle_remap()
        # An engram that is GIVEN FREE is never placed, so state.has() can never be true for it -
        # requiring one here would strand the location forever. Every other rule route goes through
        # _compile_expr, which collapses free items to 'true'; this one calls state.has directly and
        # so has to do the same check itself. free_starter_engrams: true makes Campfire free, which
        # is exactly how "Collect 100 Charcoal" became unreachable.
        free = self._free_items()
        for loc_name in self._used_locations():
            if not (loc_name.startswith("Collect ") and "Explorer Notes" not in loc_name):
                continue
            for res, eng in CRAFTED_COLLECT_ENGRAM.items():
                if res in loc_name:
                    req = remap(eng[len("Engram: "):]) if eng.startswith("Engram: ") else eng
                    if req not in free:                  # already owned at spawn -> no rule needed
                        add_rule(self.multiworld.get_location(loc_name, self.player),
                                 lambda state, it=req: state.has(it, self.player))
                    break
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
                # tames_per_item folds "Tame: X" into a representative; require whichever unlock
                # actually grants X (itself when ungrouped).
                add_rule(loc, lambda state, it=self._tame_rep_of(item): state.has(it, self.player))
            # "Tame N Species" (distinct) milestones honestly require N tame unlocks. With
            # tames_per_item a single unlock covers several species, so counting individual tame
            # items no longer maps cleanly - those milestones become filler-only instead (see
            # _excluded_progression_names); skip their item rule here.
            if self.options.tames_per_item.value <= 1:
                tame_items = sorted(self._tame_item_names)
                for m in self._locations["location_categories"].get("milestones", {}).get("entries", []):
                    if m.get("tag", "").startswith("milestone_tames_") and m["name"] not in excluded:
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
                ast = self._kill_ast(self._dino_short(d), d.get("dino_tag") or "")
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
            # EXPLORATION: a region only needs the survival gear it physically demands - Fur for
            # the snow, Scuba for the deep-sea caverns. Everything else is a sightseeing check with
            # no rule, which is the point: they are cheap, spread-out locations that widen the fill
            # budget. Compiled through the same recipe graph as the caves, so "Fur" really means
            # the Smithy chain, not a bare item name.
            for r in self._used_explore().values():
                expr = self._EXPLORE_GATE.get(r.get("gate", ""), "")
                name = "Explore: " + r["name"]
                if not expr or name in excluded:
                    continue
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
        # Goal = defeat the bosses this slot can actually reach, in the same set fill_slot_data
        # hands the plugin. Taking "the first N of every boss in the file" made a Scorched-only
        # slot require the Island's four, which is unbeatable by construction.
        events = self._goal_event_names()
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

    # Containers belong to a MAP, and only the maps this slot runs may be dealt into. Two reasons:
    # a container that does not exist on the running map is a dead override line, and - the real
    # problem - dealing the same species across another map's containers as well THINS every biome
    # it does reach. An Island slot has 14 containers; letting Scorched's 8 and Ragnarok's 17 join
    # the round-robin would cut each Island biome's roster by two thirds.
    def _slot_containers(self) -> list:
        active = {self._MAP_KEY.get(m, m) for m in self.options.maps.value} or {"island"}
        return [c for c in self._spawn_containers
                if not c.get("map") or c["map"] in active]

    def _spawn_overrides(self) -> list:
        mode = self.options.randomize_dino_spawns.value
        containers = self._slot_containers()
        if not mode or not self._spawn_classes or not containers:
            return []
        if mode == 2:      # chaos: one big deal across every container, equal weights
            weight = {e["class"]: 1.0 for e in self._spawn_classes}
            pools = {"all": [e["class"] for e in self._spawn_classes]}
            groups = {"all": [c["container"] for c in containers]}
        else:              # grouped: land+air species -> land containers, water -> water
            weight = {e["class"]: self.DANGER_WEIGHT.get(e.get("danger", "docile"), 1.0)
                      for e in self._spawn_classes}
            pools = {
                "land":  [e["class"] for e in self._spawn_classes if e["habitat"] in ("land", "air")],
                "water": [e["class"] for e in self._spawn_classes if e["habitat"] == "water"],
            }
            groups = {"land": [], "water": []}
            for c in containers:
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

    # spoiler: the per-location line can only show the ONE representative item name (item names are
    # the static datapackage), so with engrams_per_item / tames_per_item on, append a section that
    # spells out what each representative ALSO unlocks - the "second item" made visible.
    def write_spoiler(self, spoiler_handle) -> None:
        eg = self._engram_groups()
        tg = self._tame_groups()
        if not eg and not tg:
            return
        name = self.multiworld.player_name[self.player]
        spoiler_handle.write(
            f"\n\nARK grouped unlocks ({name}) - ONE received item unlocks EVERY engram/tame listed:\n")
        for rep, members in eg.items():
            spoiler_handle.write("  " + " + ".join([rep] + list(members)) + "\n")
        for rep, members in tg.items():
            spoiler_handle.write("  " + " + ".join([rep] + list(members)) + "\n")

    # tell the connector which bosses count for the goal + whether saddles are bundled (it relays
    # bundle_saddles to the plugin so the plugin grants the saddle on tame unlock).
    def fill_slot_data(self) -> dict:
        # goal_boss_tags: the boss BASE-TAGS (e.g. "SpiderBoss") for the first N bosses, in order.
        # Boss KILLS are no longer AP check locations (nothing can get stranded behind a hard or
        # near-impossible boss kill). The plugin signals each defeat by base-tag to boss_out.jsonl;
        # the client sends the AP goal once every required tag has appeared.
        # MAP-FILTERED. A boss on a map this slot is not running can never be defeated, so putting
        # it in the goal makes the seed unwinnable. The Island's four are the historical order;
        # Scorched's Manticore only appears here for a slot that actually runs Scorched or
        # Ragnarok (whose arena is the Dragon and Manticore together).
        order = self._boss_base_order()
        return {"goal_bosses": self._goal_bosses(),
                "goal_boss_tags": self._goal_boss_tags(order),
                "bundle_saddles": bool(self.options.bundle_saddles.value),
                "bundle_structures": bool(self.options.bundle_structures.value),
                # Lurch's tracker interfaces with these two: bundle_structures tells it the vanilla
                # structure engrams were collapsed into bundle items; extra_early_items is the exact
                # list the player forced early. OptionSet -> sorted list so it serialises to JSON.
                "extra_early_items": sorted(self.options.extra_early_items.value),
                "free_starter_engrams": bool(self.options.free_starter_engrams.value),
                "death_link": bool(self.options.death_link.value),
                "mod_ids": sorted(self._active_mods()),   # mods this slot enabled (plugin diagnostics)
                "item_groups": self._item_groups_slotdata(),  # rep item id -> folded member ids
                "hint_redirect": self._hint_redirect_slotdata(),  # unpooled item id -> item that unlocks it
                # rep item id -> EVERY member id it unlocks (count-groups + structure/mod/saddle
                # bundles). PopTracker reads this to light up all engrams when one bundle item lands.
                "tracker_groups": self._tracker_groups_slotdata(),
                "engrams_per_item": self.options.engrams_per_item.value,
                "tames_per_item": self.options.tames_per_item.value,
                # Every player-selectable shuffle setting, for the tracker. Toggles -> bool, Range/
                # Choice -> int (.value), OptionSet -> sorted list. Goal/bundle_*/death_link/mods/
                # engrams_per_item/tames_per_item/extra_early_items are already sent above.
                "maps": sorted(self.options.maps.value),
                "goal": self.options.goal.value,
                "lock_taming": bool(self.options.lock_taming.value),
                "lock_supply_crates": bool(self.options.lock_supply_crates.value),
                "trap_percentage": self.options.trap_percentage.value,
                "early_dino_checks": bool(self.options.early_dino_checks.value),
                "progression_tiers": bool(self.options.progression_tiers.value),
                "station_placement": self.options.station_placement.value,
                "tier0_add": sorted(self.options.tier0_add.value),
                "tier0_remove": sorted(self.options.tier0_remove.value),
                "dossier_checks": self.options.dossier_checks.value,
                "food_sanity": self.options.food_sanity.value,
                "tame_sanity": self.options.tame_sanity.value,
                "death_sanity": self.options.death_sanity.value,
                "death_milestones": bool(self.options.death_milestones.value),
                "randomize_dino_spawns": self.options.randomize_dino_spawns.value,
                "npc_replacements": [],           # legacy key (permutation design retired)
                "spawn_additions": [],            # legacy key (additions design superseded)
                "spawn_overrides": self._spawn_overrides()}
