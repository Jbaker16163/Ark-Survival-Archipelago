from dataclasses import dataclass

from Options import (Toggle, Range, Choice, DeathLink, OptionSet, PerGameCommonOptions,
                     StartInventoryPool)


class Goal(Choice):
    """Which bosses you must defeat to win (any difficulty - Gamma, Beta, or Alpha).

    The first four are cumulative over THE ISLAND's bosses, and mean the same thing whatever maps
    you run:
      broodmother                     - defeat the Broodmother
      broodmother_megapithecus        - + Megapithecus
      broodmother_megapithecus_dragon - + Dragon
      all_bosses                      - + Overseer (all four)

    The last one scales with your maps instead:
      all_bosses_all_maps             - every boss on the maps you enabled

    On an Island-only slot that final option is identical to all_bosses. Add Scorched Earth and it
    also wants the Manticore; Ragnarok's arena is the Dragon and Manticore together, so it counts
    for both.

    A boss on a map you are not running is never required - it could not be reached. On a map
    without the Island's four, the cumulative options simply mean that map's own bosses, so
    Scorched Earth alone is a Manticore run.
    """
    display_name = "Goal"
    option_broodmother = 0
    option_broodmother_megapithecus = 1
    option_broodmother_megapithecus_dragon = 2
    option_all_bosses = 3
    option_all_bosses_all_maps = 4
    default = 3


class Maps(OptionSet):
    """Which ARK map(s) your server runs. Any supported map can be played ON ITS OWN - you are
    never forced to pair maps.

    List more than one for a CLUSTER (maps linked by obelisk/transmitter travel under a single AP
    slot), which pools all their content together:
        maps:
          - the_island
          - scorched_earth

    Anything not listed is filtered out, so a check you cannot reach never appears - and the goal
    only ever asks for bosses your maps actually have.

    Smaller maps have fewer locations than the Island but receive the same craftable-everywhere
    engrams, so engram grouping is raised automatically when a slot needs it (as if you had set
    engrams_per_item yourself). Supported today: the_island, scorched_earth, ragnarok.
    """
    display_name = "Maps"
    valid_keys = {
        "the_island", "scorched_earth", "aberration", "extinction", "genesis_part_1",
        "genesis_part_2", "lost_colony", "the_center", "ragnarok", "valguero",
        "crystal_isles", "lost_island", "fjordur", "astraeos",
    }
    default = frozenset({"the_island"})


class LockTaming(Toggle):
    """Lock dino taming behind an Archipelago item."""
    display_name = "Lock Taming"
    default = 1


class LockSupplyCrates(Toggle):
    """Lock supply crate (care package) access behind an Archipelago item."""
    display_name = "Lock Supply Crates"
    default = 1


class FreeStarterEngrams(Toggle):
    """Early-help option: grant the basic starter engrams (campfire, cloth armor, spear,
    thatch building, storage, etc.) for FREE at the start, and remove them from the item
    pool so no one finds them in the multiworld. The set is engrams.json 'starter_engrams'."""
    display_name = "Free Starter Engrams"
    default = 0


class TrapPercentage(Range):
    """Of the filler items, what percent are traps (e.g. spawning wild dinos near you)
    vs neutral 'Bonus Resources'. 0 = no traps. Only affects filler slots, not real items."""
    display_name = "Trap Percentage"
    range_start = 0
    range_end = 100
    default = 25


class BundleSaddles(Toggle):
    """Bundle each rideable dino's saddle with its tame unlock. When on, unlocking
    'Tame: X' also grants X's saddle engram, and those saddle engrams are removed
    from the item pool (the freed slots become filler). Off = saddles are separate items."""
    display_name = "Bundle Saddles"
    default = 0


class EarlyDinoChecks(Toggle):
    """Cross-game starter help / lockout prevention. When on, the ONLY sphere-1 (start-reachable)
    ARK checks are the 8 weak early-dino first-kills (Dodo, Parasaur, Trike, Dilo, Phiomia,
    Lystrosaurus, Compy, Dimorphodon, marked PRIORITY) plus low levels (Reach Level 5-40). A
    global-early item (e.g. Dark Souls III 'early_banner: early_global') can therefore only land
    there -> no player gets locked out. Explorer notes still hold later progression but are moved to
    a sphere-2 region gated behind receiving any early-dino tame, so they never catch the early item;
    every other slow/late check (other kills, tames, Reach Level 45+, bosses) is EXCLUDED to filler.
    Keep dossier_checks at/above default."""
    display_name = "Early Dino Checks"
    default = 0


class StationPlacement(Choice):
    """How the 3 tier-gate station engrams (Refining Forge, Smithy/Anvil Bench, Fabricator) are
    placed when progression_tiers is on:
      tiered       - (default) hard-placed on YOUR OWN active checks, staggered by tier: Forge in
                     Tier 0, Smithy in Tier 1, Fabricator in Tier 2. The classic staged ARK climb.
      local_early  - forced into an early sphere of YOUR world (like DS3 'early_local'). All three
                     surface quickly on your own early checks, so tiers open sooner.
      global_early - forced into an early sphere ANYWHERE in the multiworld (like DS3 'early_global').
                     A friend may find your stations for you. Only meaningful in a multiworld.
    Ignored when progression_tiers is off."""
    display_name = "Station Engram Placement"
    option_tiered = 0
    option_local_early = 1
    option_global_early = 2
    default = 0


class Tier0Add(OptionSet):
    """(progression_tiers only) Creatures to FORCE into Tier 0. Their kill + tame checks become
    sphere-0, so they join the pool that can host the T0 station gates. Use exact creature names,
    quoted, e.g.:
        tier0_add:
          - Carno
          - Sabertooth
    Names that don't match a creature are ignored."""
    display_name = "Tier 0 Add"


class Tier0Remove(OptionSet):
    """(progression_tiers only) Creatures to REMOVE from Tier 0 (bumped to Tier 1), so their kills
    are no longer sphere-0 / eligible to host the T0 station gates. Only affects creatures that are
    Tier 0 by default. Use exact creature names, quoted."""
    display_name = "Tier 0 Remove"


class ProgressionTiers(Toggle):
    """Tech-tree progression (RECOMMENDED - default ON). Splits every check into 4 tier REGIONS you
    open in order: Menu -> Tier 0 -> Tier 1 -> Tier 2 -> Tier 3, each unlocked by receiving the prior
    tier's crafting-station engram(s). This is what gives the seed a real sphere-by-sphere climb;
    with it OFF almost everything is reachable from the start and the playthrough collapses into one
    or two spheres.
      Tier 0 (start): weak dinos, Reach Level <= 40
      Tier 1: opens on Engram: Forge + Engram: Mortar And Pestle - mid dinos, Level 45-80
      Tier 2: opens on Engram: Anvil Bench (the Smithy - it costs metal ingots, so it needs the
              Forge first) - strong/water dinos, Level 85-120; explorer NOTES open here
      Tier 3: opens on Engram: Fabricator - apex/deep-ocean dinos, bosses, Level 125-150
    The gate engrams are ordinary pool items classified PROGRESSION, so the fill guarantees each is
    reachable before its tier opens (no hard-placement, no waiting on another player). Explorer notes
    sit behind Tier 2 (two gates deep), so ARK's sphere-0 set is just Tier 0 kills + low levels -
    another game's early-forced item can only land there. Supersedes early_dino_checks. The
    tame/kill/cave access rules still apply ON TOP, within each tier."""
    display_name = "Progression Tiers"
    default = 1


class ExtraEarlyItems(OptionSet):
    """Force specific items early, the same way station_placement handles the tier gates. List exact
    item names (engrams like "Engram: Bow", or tames like "Tame: Rex"), quoted:
        extra_early_items:
          - "Engram: Bow"
          - "Tame: Raptor"
    Routing follows station_placement: global_early -> forced early ANYWHERE in the multiworld;
    anything else (local_early / tiered) -> forced early in YOUR world. Works with or without
    progression_tiers. Names that aren't real items are ignored."""
    display_name = "Extra Early Items"


class ModIds(OptionSet):
    """Steam Workshop mod IDs to include. Their engrams become AP items. ONLY the mods below are
    supported (an unknown ID is a generation error - the apworld can't know an arbitrary mod's
    engrams). Supported:
        731604991 / 1999447172  Structures Plus / Super Structures  (building - needs bundle_structures: true)
        1565015734              Kraken's Better Dinos
        821530042               Upgrade Station
        2594067220              Super Spyglass Plus
        1609138312              Dino Storage v2
        889745138               Awesome Teleporters
        1631378184              Explorer Note Tracker
        1404697612              Awesome SpyGlass
        1967741708              Lethal's Reusables
    Structures Plus and Super Structures are forks of one mod - list whichever you run, not both.
    Accepts a yaml list OR a comma-separated string, quotes optional:
        mod_ids: 731604991, 1631378184, 2594067220, 821530042, 1609138312, 1565015734, 1404697612, 889745138
        mod_ids: [1609138312, 889745138]"""
    display_name = "Mod IDs"

    # Accept every form people reach for:
    #   - a yaml LIST, quoted or not:  mod_ids: ["123", 456]  /  - 123 \n - "456"
    #   - a single COMMA STRING:       mod_ids: "123, 456"  (or bare 123, 456)
    #   - one bare number:             mod_ids: 123
    # Mod ids look numeric, so an unquoted entry arrives as int; AP's spoiler writer then does
    # ", ".join(value) and dies with "expected str instance, int found" - but only at OUTPUT, after
    # a full generation. Coercing to str on parse avoids that late crash for all shapes.
    @classmethod
    def _split(cls, v):
        return [p.strip() for p in str(v).replace(",", " ").split() if p.strip()]

    @classmethod
    def from_any(cls, data) -> "ModIds":
        if isinstance(data, (list, set, tuple, frozenset)):
            out: set = set()
            for v in data:
                out.update(cls._split(v))          # each entry may itself be "123, 456"
            return cls(out)
        return cls(set(cls._split(data)))          # a lone int or a comma/space string


class FoodSanity(Choice):
    """Percent of the food 'hold N in inventory' checks (Citronal, Cooked Meat, Jerky, Honey,
    Rare Flower, etc - 14 total) included as locations. Which ones are picked is random per seed.
    0 = no food checks."""
    display_name = "Food Sanity"
    option_0 = 0
    option_25 = 25
    option_50 = 50
    option_75 = 75
    option_100 = 100
    default = 100


class DeathSanity(Choice):
    """Percent of the cause-of-death checks included as locations - the 10 'Die to a carnivore /
    to cold / to drowning / from fall damage / to starvation ...' checks. Which ones are picked is
    random per seed. 0 = no death checks at all, for anyone who would rather not be nudged into
    dying on purpose.

    Note these checks CAN hold progression - every cause is reachable (you can always starve or
    drown), so nothing gets stranded, but it does mean a seed may want you to die deliberately. Set
    this to 0 if that is not for you."""
    display_name = "Death Sanity"
    option_0 = 0
    option_25 = 25
    option_50 = 50
    option_75 = 75
    option_100 = 100
    default = 100


class DeathMilestones(Toggle):
    """Include the cumulative death-count milestones (die once, 5, 10, 25, 40 times).

    Separate from death_sanity because they are a different kind of ask: the cause-of-death checks
    are things that happen while you play, whereas the count milestones reward dying repeatedly.
    Turning this off leaves the individual death checks alone."""
    display_name = "Death Milestones"
    default = 1


class TameSanity(Choice):
    """Percent of the per-species 'Tamed: X' checks included as locations - lower = fewer tames
    REQUIRED to finish. Which species are picked is random per seed. Every 'Tame: X' unlock item
    stays in the pool regardless, so everything remains tameable (with lock_taming).
    NOTE: low values remove many locations; if generation errors about item count, raise this,
    or turn on bundle_structures / bundle_saddles to shrink the item pool to match."""
    display_name = "Tame Sanity"
    option_25 = 25
    option_50 = 50
    option_75 = 75
    option_100 = 100
    default = 100


class BundleStructures(Toggle):
    """Bundle building-structure engrams by material: ALL Wood structures unlock from one
    'Bundle: Wood Structures' item (same for Stone, Metal, Greenhouse). Tools/weapons like the
    Metal Pick stay individual. Shrinks the item pool by ~100 items (backfilled with filler)."""
    display_name = "Bundle Structures"
    default = 0


class EngramsPerItem(Range):
    """Group engram unlocks so one AP item unlocks SEVERAL engrams at once.
      1 = off (one item = one engram, the classic behavior)
      2-4 = fold engrams into groups of that size, in PROGRESSION order (early engrams grouped
            with early, late with late), so the pool carries far fewer engram items (the freed
            slots become filler). The tame-logic access rules follow automatically, and the plugin
            unlocks every engram in a received group. Handy for shrinking a huge engram pool so it
            fits the location count. Structure-bundled / starter / auto-granted engrams are never
            grouped (they're already handled)."""
    display_name = "Engrams Per Item"
    range_start = 1
    range_end = 4
    default = 1


class TamesPerItem(Range):
    """Group taming unlocks so one AP item unlocks SEVERAL 'Tame: X' at once.
      1 = off
      2-4 = fold tames into groups of that size WITHIN each progression tier (never mixing an
            early creature with an endgame one), so receiving one item unlocks the ability to tame
            several species. Shrinks the item pool; with lock_taming the group item gates every
            species it covers. 'Tame N Species' milestones become filler-only when this is above 1
            (you still tame the species in-game; they just stop hosting progression)."""
    display_name = "Tames Per Item"
    range_start = 1
    range_end = 4
    default = 1


class RandomizeDinoSpawns(Choice):
    """FULLY randomize which species live in which biome: every species is dealt across the
    map's spawn zones, and each biome's spawn roster is completely REPLACED by its seeded hand
    (via Game.ini spawn-container overrides the connector writes; one server start applies).
    Every species is guaranteed to spawn SOMEWHERE, so all checks stay obtainable.
      off     - normal spawns
      grouped - land+air species dealt across land biomes, water species across water zones,
                with predators down-weighted (apex rare, mid uncommon) so zones stay livable
      chaos   - everything dealt across everything at EQUAL weight (beached mosas, ocean rexes,
                predator-saturated beaches... the full experience)
    Bosses, alphas, tek variants, cave interiors, and specialty spawners (Giga, Quetz, beaver
    dams...) are never touched."""
    display_name = "Randomize Dino Spawns"
    option_off = 0
    option_grouped = 1
    option_chaos = 2
    default = 0


class DossierChecks(Range):
    """How many explorer-note locations to include as checks.

    Counted per SLOT, from the notes your maps actually have - the Island has 232, Scorched Earth
    adds 137, and a value above your total harmlessly caps there. Ragnarok has none of its own.
    Keep near the maximum unless you know what you're doing: ARK's big pool needs most of the notes
    plus the other locations to fit, and generation fails if too low."""
    display_name = "Dossier Checks"
    range_start = 0
    # The cap is applied AFTER the map filter, so the default simply means "every note my maps
    # have": an Island slot still gets its 232, a cluster gets all of them. Note-count milestones
    # scale off the real count, not this number.
    range_end = 400
    default = 400


@dataclass
class ArkASAOptions(PerGameCommonOptions):
    # start_inventory that also REMOVES the items from the pool (replaced with filler), so
    # nobody finds a copy of something you already started with. AP core handles the swap.
    start_inventory_from_pool: StartInventoryPool
    maps: Maps
    goal: Goal
    death_link: DeathLink
    lock_taming: LockTaming
    lock_supply_crates: LockSupplyCrates
    bundle_saddles: BundleSaddles
    free_starter_engrams: FreeStarterEngrams
    trap_percentage: TrapPercentage
    early_dino_checks: EarlyDinoChecks
    progression_tiers: ProgressionTiers
    station_placement: StationPlacement
    extra_early_items: ExtraEarlyItems
    tier0_add: Tier0Add
    tier0_remove: Tier0Remove
    dossier_checks: DossierChecks
    food_sanity: FoodSanity
    tame_sanity: TameSanity
    death_sanity: DeathSanity
    death_milestones: DeathMilestones
    bundle_structures: BundleStructures
    engrams_per_item: EngramsPerItem
    tames_per_item: TamesPerItem
    randomize_dino_spawns: RandomizeDinoSpawns
    mod_ids: ModIds
