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
    # Tek + Thatch were building tiers with no bundle: 47 Tek and 8 Thatch engrams stayed
    # individual, wasting ~55 item slots. Bundling them also auto-captures a building MOD's
    # Tek/Thatch pieces (same material-word rule), which is where the pool headroom comes from.
    "Bundle: Tek Structures": (8738005, "Tek"),
    "Bundle: Thatch Structures": (8738006, "Thatch"),
    # Building-MOD material tiers with no vanilla equivalent (S+/Super Structures add Adobe and
    # Glass building sets: 78 + 59 engrams in S+ alone). Empty for a vanilla-only slot, and an
    # empty bundle is never pooled - see create_items.
    "Bundle: Adobe Structures": (8738007, "Adobe"),
    "Bundle: Glass Structures": (8738008, "Glass"),
}


def structure_bundle_members(engram_data: Dict[str, Any],
                             mod_engrams: Any = None) -> Dict[str, set]:
    """bundle ap_name -> set of member engram ap_names.

    `mod_engrams` = engrams from the slot's ACTIVE mods. They must be included or a building mod's
    structures never fold into the bundles, stay individual items, and blow the pool past the
    location count (that is the whole reason bundling makes room for mod support).
    """
    engrams = list(engram_data["engrams"]) + list(mod_engrams or [])
    out: Dict[str, set] = {}
    for bundle, (_id, material) in STRUCTURE_BUNDLES.items():
        out[bundle] = {e["ap_name"] for e in engrams
                       if "PrimalItemStructure_" in e["engram_class"]
                       and material in e["ap_name"].replace("Engram:", "").split()}
    return out


class ArkItem(Item):
    game = GAME


def build_item_table(engram_data: Dict[str, Any],
                     dino_data: Dict[str, Any] | None = None,
                     crate_data: Dict[str, Any] | None = None,
                     filler_data: Dict[str, Any] | None = None,
                     mod_catalog: Dict[str, Any] | None = None) -> Dict[str, int]:
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
    # EVERY supported mod's items, always - the datapackage must be identical for all players in
    # the multiworld. The yaml's mod_ids only decides which are POOLED (see create_items).
    for mod in (mod_catalog or {}).values():
        for e in mod.get("engrams", []):
            table[e["ap_name"]] = e["id"]
        for b in mod.get("bundles", []):         # curated group items (one unlocks many engrams)
            table[b["ap_name"]] = b["id"]
        for d in mod.get("dinos", []):
            if d.get("ap_name") and d.get("id") is not None:
                table[d["ap_name"]] = d["id"]
    table.setdefault(FILLER_NAME, FILLER_ID)
    return table
