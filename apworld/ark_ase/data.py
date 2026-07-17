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
