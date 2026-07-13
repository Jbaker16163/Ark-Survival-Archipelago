from typing import Any, Dict

from BaseClasses import Item

GAME = "ARK Survival Evolved"

# A single filler item used to balance item count against location count.
FILLER_NAME = "Bonus Resources"
FILLER_ID = 8739500

# bundle_structures option: one item unlocks EVERY structure engram of that material
# (engram_class contains PrimalItemStructure_ AND the material appears as a word in the ap_name).
# Always in the datapackage (the table is class-level/static); pooled only when the option is on.
# The plugin hardcodes the same ids + classification rule - keep them in sync.
STRUCTURE_BUNDLES = {
    "Bundle: Wood Structures": (8738001, "Wood"),
    "Bundle: Stone Structures": (8738002, "Stone"),
    "Bundle: Metal Structures": (8738003, "Metal"),
    "Bundle: Greenhouse Structures": (8738004, "Greenhouse"),
}


def structure_bundle_members(engram_data: Dict[str, Any]) -> Dict[str, set]:
    """bundle ap_name -> set of member engram ap_names."""
    out: Dict[str, set] = {}
    for bundle, (_id, material) in STRUCTURE_BUNDLES.items():
        out[bundle] = {e["ap_name"] for e in engram_data["engrams"]
                       if "PrimalItemStructure_" in e["engram_class"]
                       and material in e["ap_name"].replace("Engram:", "").split()}
    return out


class ArkItem(Item):
    game = GAME


def build_item_table(engram_data: Dict[str, Any],
                     dino_data: Dict[str, Any] | None = None,
                     crate_data: Dict[str, Any] | None = None,
                     filler_data: Dict[str, Any] | None = None) -> Dict[str, int]:
    """name -> id for progression items: engrams + tame items + crate access + specials,
    plus the filler/trap items.

    (World-access / boss-access items from locations.json are intentionally left out
    for now - their in-game gates aren't implemented yet, and excluding them keeps
    item count <= location count for solo generation.)
    """
    table: Dict[str, int] = {}
    for e in engram_data["engrams"]:
        table[e["ap_name"]] = e["id"]
    for d in (dino_data or {}).get("dinos", []):
        if d.get("ap_name") and d.get("id") is not None:
            table[d["ap_name"]] = d["id"]        # "Tame: X" -> per-dino taming unlock
        # untameable kill-only dinos have no tame item (name-only, kill_loc only) -> skip
    for c in (crate_data or {}).get("crate_items", []):
        table[c["ap_name"]] = c["id"]            # "Beacon: X" / "Cave Crate: X" -> crate access
    for s in engram_data.get("special_items", []):
        table[s["ap_name"]] = s["id"]
    for f in (filler_data or {}).get("filler", []):
        table[f["ap_name"]] = f["id"]            # neutral filler + traps
    for b, (bid, _mat) in STRUCTURE_BUNDLES.items():
        table[b] = bid                           # structure bundles (pooled only if option on)
    table.setdefault(FILLER_NAME, FILLER_ID)
    return table
