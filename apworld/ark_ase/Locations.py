from typing import Any, Dict

from BaseClasses import Location

GAME = "ARK Survival Evolved"


class ArkLocation(Location):
    game = GAME


def build_location_table(location_data: Dict[str, Any],
                         dino_data: Dict[str, Any] | None = None,
                         mod_catalog: Dict[str, Any] | None = None,
                         explore: Dict[str, Any] | None = None) -> Dict[str, int]:
    """name -> id from locations.json (dossiers + bosses + milestones + level checks) plus
    per-dino tame checks from dinos.json.

    These ids are exactly what the plugin reports (ReportLocation), so a generated
    game lines up with in-game checks. (Artifacts are NOT checks - they auto-fire at
    world-load on a dedicated server, so they were dropped.)
    """
    table: Dict[str, int] = {}
    cats = location_data["location_categories"]
    # "bosses" is deliberately excluded - boss kills are the goal (signalled via boss_out.jsonl),
    # not AP check locations. Keeping them out of the datapackage means the plugin never reports a
    # boss loc id that isn't a real location (which would break other players' clients).
    for key in ("dossiers", "milestones", "levels", "alpha_kills", "inventory_checks",
                "deaths"):
        for entry in cats.get(key, {}).get("entries", []):
            table[entry["name"]] = entry["id"]
    for d in (dino_data or {}).get("dinos", []):
        short = d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")
        if d.get("tame_loc"):
            table["Tamed: " + short] = d["tame_loc"]
        if d.get("kill_loc"):
            table["Killed: " + short] = d["kill_loc"]
    # mod creature checks - ALWAYS in the datapackage (see build_item_table); mod_ids only decides
    # which are actually used by a slot (_used_locations).
    for mod in (mod_catalog or {}).values():
        for d in mod.get("dinos", []):
            short = d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")
            if d.get("tame_loc"):
                table["Tamed: " + short] = d["tame_loc"]
            if d.get("kill_loc"):
                table["Killed: " + short] = d["kill_loc"]
    # exploration checks (data/explore_areas.json). ALWAYS in the datapackage, for every map that
    # has been measured - the "maps" option only decides which a slot actually uses, exactly like
    # mod creature checks above. Filtering here would break a multiworld where two players run
    # different maps, since the datapackage has to be identical for everyone.
    for r in (explore or {}).values():
        table["Explore: " + r["name"]] = r["id"]
    return table
