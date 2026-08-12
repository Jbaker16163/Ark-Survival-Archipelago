"""Loads the shared data files bundled with the apworld.

Must work when the world is imported from a zipped .apworld, where os.path/open
can't see inside the archive - so use pkgutil.get_data (zip-safe).
"""
import json
import pkgutil
from typing import Any, Dict


def _load(name: str) -> Dict[str, Any]:
    raw = pkgutil.get_data(__package__, "data/" + name)
    if raw is None:
        raise FileNotFoundError(f"bundled data/{name} not found in apworld")
    return json.loads(raw.decode("utf-8"))


def load_engram_data() -> Dict[str, Any]:
    return _load("engrams.json")


def load_location_data() -> Dict[str, Any]:
    return _load("locations.json")


def load_dino_data() -> Dict[str, Any]:
    """Tame items. Optional - returns an empty set if dinos.json isn't bundled yet."""
    try:
        return _load("dinos.json")
    except FileNotFoundError:
        return {"dinos": []}


def load_crate_data() -> Dict[str, Any]:
    """Crate access items + artifact checks. Optional - empty if crates.json isn't bundled yet."""
    try:
        return _load("crates.json")
    except FileNotFoundError:
        return {"crate_items": [], "artifact_locations": []}


def load_tek_data() -> Dict[str, Any]:
    """Tek engram -> boss grant split. Those engrams stay OUT of the AP pool (the plugin grants
    them on boss kills). Optional - empty means all engrams stay in the pool."""
    try:
        return _load("tek_grants.json")
    except FileNotFoundError:
        return {"grants": {}}


def load_spawn_class_data() -> Dict[str, Any]:
    """Wild spawn Character_BP classes (+ habitat group) for randomize_dino_spawns.
    Optional - empty disables the option silently."""
    try:
        return _load("spawn_classes.json")
    except FileNotFoundError:
        return {"spawn_classes": []}


def load_spawn_container_data() -> Dict[str, Any]:
    """The Island's biome spawn containers (+ habitat) for randomize_dino_spawns (additions
    design). Optional - empty disables the option silently."""
    try:
        return _load("spawn_containers.json")
    except FileNotFoundError:
        return {"spawn_containers": []}


def load_filler_data() -> Dict[str, Any]:
    """Filler + trap items. Falls back to a single neutral filler if filler.json isn't bundled."""
    try:
        return _load("filler.json")
    except FileNotFoundError:
        return {"filler": [{"id": 8739500, "ap_name": "Bonus Resources", "trap": False,
                            "effect": {"kind": "none"}}]}


# ---- mod support -------------------------------------------------------------------------------
# Every supported mod's content is ALWAYS in the datapackage (item/location tables are class-level
# and must be identical for every player in the multiworld); the yaml's mod_ids only chooses which
# ones are ACTIVE for that slot - exactly like bundle_structures / dossier_checks subset today.
MOD_ID_BASE = 8760000        # base game occupies 8730000-8756000
MOD_ID_STRIDE = 10000        # reserved id block per mod


def load_mod_index() -> Dict[str, Any]:
    """The mod catalog index. Optional - empty means mod support is present but no mods shipped."""
    try:
        return _load("mods/index.json")
    except FileNotFoundError:
        return {"mods": []}


def load_mod_catalog() -> Dict[str, Dict[str, Any]]:
    """mod_id (str) -> {mod_id, name, kind, id_base, engrams: [...], dinos: [...]}.

    Index-driven because pkgutil.get_data cannot enumerate a directory inside a zipped .apworld.
    A listed-but-missing file is skipped rather than raising: a broken catalog entry must not stop
    the base game from generating.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for entry in load_mod_index().get("mods", []):
        mod_id = str(entry.get("mod_id", "")).strip()
        if not mod_id:
            continue
        try:
            body = _load("mods/" + entry["file"])
        except (FileNotFoundError, KeyError):
            continue
        aliases = [str(a).strip() for a in entry.get("aliases", []) if str(a).strip()]
        out[mod_id] = {"mod_id": mod_id,
                       "aliases": aliases,
                       "name": entry.get("name", mod_id),
                       "kind": entry.get("kind", "utility"),
                       "id_base": entry.get("id_base"),
                       "auto_grant": body.get("auto_grant", []),
                       "engrams": body.get("engrams", []),
                       "bundles": body.get("bundles", []),   # curated group items
                       "dinos": body.get("dinos", [])}
    return out


def load_tame_logic_data() -> Dict[str, Any]:
    """Tame/craft dependency graph (item recipes + dino tame reqs + engram aliases) for the
    softlock-preventing access rules. Optional - empty disables the tame-logic rules silently."""
    try:
        return _load("tame_logic.json")
    except FileNotFoundError:
        return {}


def load_map_data() -> Dict[str, Any]:
    """Map registry + which maps each id belongs to (data/maps.json).

    Membership is many-to-many: most creatures and every engram exist on several maps, so an id
    appears under each. "any" is not a map - it marks content not tied to one and is never
    filtered out. Missing file = no map filtering at all, which is the pre-map behaviour."""
    try:
        return _load("maps.json")
    except FileNotFoundError:
        return {}


def load_explore_data() -> Dict[str, Any]:
    """Exploration areas measured in-game with /dumppos (data/explore_areas.json).

    Optional and map-scoped: every region carries its own "map" key, so the datapackage always
    holds every mapped region while a slot only USES the ones for the maps it enabled. Missing
    file = no exploration checks, silently."""
    try:
        return _load("explore_areas.json")
    except FileNotFoundError:
        return {}
