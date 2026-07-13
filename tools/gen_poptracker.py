#!/usr/bin/env python3
"""Generate the PopTracker pack for ARK: Survival Evolved (Archipelago) from the shared data files.

Emits, under poptracker/:
  items/items.json      - a toggle item per trackable check (bosses/tames/kills/levels/stations) + a
                          dossier counter
  layouts/tracker.json  - tabbed grid layout referencing those item codes
  scripts/ap_map.lua    - AP location-id -> item-code map (+ note range, station item ids) used by
                          scripts/autotracking.lua
  images/*.png          - one placeholder icon per category (swap with real art later, same names)

manifest.json and scripts/autotracking.lua are hand-maintained (not overwritten here).

  python tools/gen_poptracker.py
"""
import json
import os
import re

HERE = os.path.dirname(__file__)
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DATA = os.path.join(ROOT, "apworld", "ark_ase", "data")
PACK = os.path.join(ROOT, "poptracker")

STATIONS = [  # (code slug, display, AP item name) - the 3 tier gate engrams
    ("smithy", "Smithy", "Engram: Anvil Bench"),
    ("forge", "Refining Forge", "Engram: Forge"),
    ("fabricator", "Fabricator", "Engram: Fabricator"),
]
# category -> (icon color RGB, short label prefix on the placeholder tile)
CATEGORIES = {
    "boss": ((150, 40, 40), "B"),
    "tame": ((40, 110, 60), "T"),
    "kill": ((150, 90, 30), "K"),
    "level": ((60, 80, 150), "L"),
    "station": ((120, 80, 150), "S"),
    "crate": ((180, 140, 40), "C"),
    "artifact": ((140, 60, 150), "A"),
    "apex": ((160, 60, 60), "X"),
    "milestone": ((70, 120, 120), "M"),
    "dossier": ((90, 90, 90), "D"),
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def make_icons():
    from PIL import Image, ImageDraw
    os.makedirs(os.path.join(PACK, "images"), exist_ok=True)
    for cat, (rgb, label) in CATEGORIES.items():
        img = Image.new("RGBA", (32, 32), rgb + (255,))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 31, 31], outline=(255, 255, 255, 180))
        d.text((11, 9), label, fill=(255, 255, 255, 255))
        # "off" (undone) = dimmed version
        img.save(os.path.join(PACK, "images", f"{cat}.png"))
        dim = Image.new("RGBA", (32, 32), tuple(int(c * 0.35) for c in rgb) + (255,))
        dd = ImageDraw.Draw(dim)
        dd.rectangle([0, 0, 31, 31], outline=(120, 120, 120, 160))
        dd.text((11, 9), label, fill=(150, 150, 150, 255))
        dim.save(os.path.join(PACK, "images", f"{cat}_off.png"))


def main():
    dinos = load("dinos.json")["dinos"]
    locs = load("locations.json")["location_categories"]

    items = []
    layout_tabs = {}   # tab name -> list of item codes (grid)
    loc_to_code = {}   # AP location id -> item code
    item_to_code = {}  # AP item id -> item code (for received stations)

    def add_toggle(code, name, cat):
        items.append({
            "name": name, "type": "toggle", "codes": code,
            "img": f"images/{cat}.png", "disabled_img": f"images/{cat}_off.png",
        })

    # (Stations + Bosses tabs intentionally omitted - not tracked here.)

    # tames + kills (per dino, from dinos.json)
    #   Tames Unlocked = you RECEIVED the "Tame: X" item (can now tame it) -> item tracking
    #   Tames To Do    = you COMPLETED the "Tamed: X" check (actually tamed it) -> location tracking
    layout_tabs["Tames Unlocked"] = []
    layout_tabs["Tames To Do"] = []
    layout_tabs["Kills"] = []
    for d in dinos:
        short = d["name"] if d.get("name") else d["ap_name"].replace("Tame: ", "")
        if d.get("ap_name") and d.get("id") is not None:      # unlock item (received)
            code = f"unlocked_{slug(short)}"
            add_toggle(code, short, "tame")
            layout_tabs["Tames Unlocked"].append(code)
            item_to_code[d["id"]] = code
        if d.get("tame_loc"):                                 # tame check (done in-game)
            code = f"tame_{slug(short)}"
            add_toggle(code, short, "tame")
            layout_tabs["Tames To Do"].append(code)
            loc_to_code[d["tame_loc"]] = code
        if d.get("kill_loc"):
            code = f"kill_{slug(short)}"
            add_toggle(code, short, "kill")
            layout_tabs["Kills"].append(code)
            loc_to_code[d["kill_loc"]] = code

    # alpha-predator kills -> appended to the Kills tab
    for a in locs.get("alpha_kills", {}).get("entries", []):
        short = a["name"].replace("Killed: ", "")
        code = f"kill_{slug(short)}"
        add_toggle(code, short, "kill")
        layout_tabs["Kills"].append(code)
        loc_to_code[a["id"]] = code

    # level milestones (locations)
    layout_tabs["Levels"] = []
    for e in locs.get("levels", {}).get("entries", []):
        code = f"level_{slug(e['name'])}"
        add_toggle(code, e["name"].replace("Reach ", ""), "level")
        layout_tabs["Levels"].append(code)
        loc_to_code[e["id"]] = code

    # count milestones (Tame/Kill N Creatures + Species, Collect N Notes)
    layout_tabs["Milestones"] = []
    for e in locs.get("milestones", {}).get("entries", []):
        code = f"ms_{slug(e['name'])}"
        add_toggle(code, e["name"], "milestone")
        layout_tabs["Milestones"].append(code)
        loc_to_code[e["id"]] = code

    # inventory "hold N" checks (artifacts + apex drops + resources) -> one "Collecting" tab.
    inv = locs.get("inventory_checks", {}).get("entries", [])
    if inv:
        layout_tabs["Collecting"] = []
        for e in inv:
            cat = "artifact" if e["name"].startswith("Artifact:") else "apex"
            code = f"inv_{slug(e['name'])}"
            add_toggle(code, e["name"], cat)
            layout_tabs["Collecting"].append(code)
            loc_to_code[e["id"]] = code

    # supply drops -> crate ACCESS items (Beacon: X / Cave Crate: X). Tracked by received item.
    crates = load("crates.json").get("crate_items", [])
    if crates:
        layout_tabs["Supply Drops"] = []
        for c in crates:
            code = f"crate_{slug(c['ap_name'])}"
            add_toggle(code, c["ap_name"], "crate")
            layout_tabs["Supply Drops"].append(code)
            item_to_code[c["id"]] = code

    # dossiers -> one toggle tile per explorer note (the full pool; only the notes this slot uses
    # will ever light, the rest stay off).
    layout_tabs["Dossiers"] = []
    dossiers = locs.get("dossiers", {}).get("entries", [])
    for e in dossiers:
        code = f"dossier_{slug(e['name'])}"
        add_toggle(code, e["name"], "dossier")
        layout_tabs["Dossiers"].append(code)
        loc_to_code[e["id"]] = code

    # ---- write items.json ----
    os.makedirs(os.path.join(PACK, "items"), exist_ok=True)
    with open(os.path.join(PACK, "items", "items.json"), "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=1)

    # ---- write layout: a tabbed set of grids ----
    def grid(codes, per_row):
        rows = [codes[i:i + per_row] for i in range(0, len(codes), per_row)]
        return {"type": "array", "orientation": "vertical", "content": [
            {"type": "array", "orientation": "horizontal",
             "content": [{"type": "item", "item": c} for c in row]} for row in rows]}

    tabs = []
    for tab, codes in layout_tabs.items():
        if tab == "Dossiers":
            # PopTracker doesn't scroll a tab; 1231 tiles overflow -> split into sub-tab pages that
            # each fit on screen (30 wide x 10 tall = 300 per page).
            per_row, page = 30, 300
            pages = []
            for i in range(0, len(codes), page):
                chunk = codes[i:i + page]
                pages.append({"title": f"{i + 1}-{i + len(chunk)}", "content": grid(chunk, per_row)})
            tabs.append({"title": tab, "content": {"type": "tabbed", "tabs": pages}})
        else:
            tabs.append({"title": tab, "content": grid(codes, 12)})
    layout = {
        "tracker_default": {
            "type": "tabbed", "tabs": tabs,
        }
    }
    os.makedirs(os.path.join(PACK, "layouts"), exist_ok=True)
    with open(os.path.join(PACK, "layouts", "tracker.json"), "w", encoding="utf-8") as fh:
        json.dump(layout, fh, indent=1)

    # ---- write ap_map.lua (consumed by autotracking.lua) ----
    os.makedirs(os.path.join(PACK, "scripts"), exist_ok=True)
    lines = ["-- GENERATED by tools/gen_poptracker.py - do not edit by hand.",
             "-- sets the global AP_MAP (loaded via ScriptHost:LoadScript, so no require needed).",
             "AP_MAP = {"]
    lines.append("  loc_to_code = {")
    for lid in sorted(loc_to_code):
        lines.append(f"    [{lid}] = \"{loc_to_code[lid]}\",")
    lines.append("  },")
    lines.append("  item_to_code = {")
    for iid in sorted(item_to_code):
        lines.append(f"    [{iid}] = \"{item_to_code[iid]}\",")
    lines.append("  },")
    lines.append("}")
    with open(os.path.join(PACK, "scripts", "ap_map.lua"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    make_icons()
    print(f"pack written: {len(items)} items, {len(loc_to_code)} tracked locations "
          f"({len(dossiers)} dossiers), {len(layout_tabs)} tabs")


if __name__ == "__main__":
    main()
