from dataclasses import dataclass

from Options import (Toggle, Range, Choice, DeathLink, OptionSet, PerGameCommonOptions,
                     StartInventoryPool)


class Goal(Choice):
    """Which bosses you must defeat to win (any difficulty - Gamma, Beta, or Alpha).
    Cumulative: each tier adds the next boss.
      broodmother                     - defeat the Broodmother
      broodmother_megapithecus        - + Megapithecus
      broodmother_megapithecus_dragon - + Dragon
      all_bosses                      - + Overseer (all four)
    """
    display_name = "Goal"
    option_broodmother = 0
    option_broodmother_megapithecus = 1
    option_broodmother_megapithecus_dragon = 2
    option_all_bosses = 3
    default = 3


class Maps(OptionSet):
    """Which ARK map(s) your server runs. List more than one for a CLUSTER (multiple maps linked
    by obelisk/transmitter travel under one AP slot). ONLY "the_island" has real location/item data
    right now - other maps are reserved for future support and add nothing yet. Quote each name:
        maps:
          - the_island
          - scorched_earth
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
    """How the 3 tier-gate station engrams (Smithy/Anvil Bench, Refining Forge, Fabricator) are
    placed when progression_tiers is on:
      tiered       - (default) hard-placed on YOUR OWN active checks, staggered by tier: Smithy in
                     Tier 0, Forge in Tier 1, Fabricator in Tier 2. The classic staged ARK climb.
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
    """Turn ARK into a tech-tree progression world instead of pure RNG. When on, checks are split
    into 4 tiers gated by 3 crafting-station engrams. Those gates are placed on your own ACTIVE
    checks (dino kills/tames or level-ups, never explorer notes), so you advance by playing - not by
    hunting a specific note - and never wait on another player:
      Tier 0 (start): weak dinos, explorer notes, Reach Level 5-40
      Tier 1: opens on receiving Engram: Anvil Bench (Smithy) AND Engram: Mortar And Pestle -
              mid dinos, Reach Level 45-80
      Tier 2: opens on receiving Engram: Forge (Refining Forge) - strong/water dinos, Level 85-120
      Tier 3: opens on receiving Engram: Fabricator - apex/deep-ocean dinos, bosses, Level 125-150
    You climb by doing your current tier's checks until one hands you the next station engram.
    ARK's start-reachable (sphere-0) checks are ONLY T0 kills + Reach Level 5-40 (notes are gated
    behind an early tame, tames behind lock_taming), so another game's early item lands only there.
    NOTE: this supersedes early_dino_checks (Tier 0 is already an all-reachable sphere-1 = no lockout)."""
    display_name = "Progression Tiers"
    default = 0


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


class RandomizeDinoSpawns(Choice):
    """Shuffle which wild dino spawns where, seed-deterministically, via Game.ini NPCReplacements
    (the connector writes the lines; one server restart applies them).
      off     - normal spawns
      grouped - shuffle within habitat: land<->land, water<->water, air<->air (safe default)
      chaos   - one big shuffle across everything (Plesiosaurs in the forest, Rexes in the ocean
                drowning... you asked for it)
    Bosses, alphas, and event creatures are never touched. Kill/tame checks follow the actual
    creature, so all checks still work - but where you FIND each species changes completely."""
    display_name = "Randomize Dino Spawns"
    option_off = 0
    option_grouped = 1
    option_chaos = 2
    default = 0


class DossierChecks(Range):
    """How many explorer-note locations (The Island's 240 collectible notes) to include as checks.
    Keep at the maximum unless you know what you're doing: ARK's ~634 pool items need ~400
    non-note locations + most of the notes to fit (generation errors if too low)."""
    display_name = "Dossier Checks"
    range_start = 0
    range_end = 240
    default = 240


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
    bundle_structures: BundleStructures
    randomize_dino_spawns: RandomizeDinoSpawns
