#!/usr/bin/env python3
"""Seed data/tame_logic.json from the 'Ark IDs.xlsx' dependency sheet.

Produces the AP access-logic graph:
  - item recipes: craftable/macro -> requirement expression over other nodes ('+'=AND, '|'=OR)
  - dino tame requirements: dino -> KO/tame method expression
  - ALIAS: every graph node that is a REAL AP-gated engram -> our engrams.json ap_name.
    Nodes with no alias are macros/consumables/resources: they carry no engram of their own and
    just flatten through their recipe requirements (or to nothing = freely available).

Then it FLATTENS each dino's tame requirement to the set of engram ap_names actually needed, and
validates that every referenced engram exists. Run: python tools/gen_tame_logic.py [path-to-xlsx]

This is a SEED tool - data/tame_logic.json is the maintained source afterwards. Re-run only to
re-import from a changed spreadsheet.
"""
import json, os, re, sys

HERE = os.path.dirname(__file__)
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XLSX = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\justi\Downloads\Ark IDs.xlsx"

# sheet token -> our engrams.json ap_name (WITHOUT the "Engram: " prefix). These are the nodes
# that are real AP-gated engrams. CONFIRMED = matches verified against engrams.json name list.
# "??" comment marks a mapping that still needs the user's confirmation (ARK-name ambiguity).
ALIAS = {
    "Campfire": "Campfire", "Cooking Pot": "Cooking Pot", "Bow": "Bow", "Club": "Stone Club",
    "Slingshot": "Slingshot", "Waterskin": "Waterskin", "Refining Forge": "Forge", "Forge": "Forge",
    "Mortar & Pestle": "Mortar And Pestle", "Narcotic": "Narcotic", "Sparkpowder": "Sparkpowder",
    "Gunpowder": "Gunpowder", "Crossbow": "Crossbow", "Fabricator": "Fabricator",
    "Stimulant": "Stimulant", "Bug Repellent": "Bug Repel", "Water Jar": "Water Jar",
    "Tranq Dart": "Tranq Dart", "Electronics": "Electronics", "Polymer": "Polymer",
    "Beer Barrel": "Beer Barrel", "Industrial Forge": "Industrial Forge",
    "Air Conditioner": "Air Conditioner", "Egg Incubator": "Egg Incubator",
    "Stone Arrow": "Arrow Stone", "Tranq Arrow": "Arrow Tranq", "Smithy": "Anvil Bench",
    "Metal Pick": "Metal Pick", "Metal Hatchet": "Metal Hatchet",   # metal tools (key harvest gear)
    "Bola": "Bola", "Preserving Bin": "Preserving Bin",
    "Greenhouse Wall": "Greenhouse Wall", "Greenhouse Ceiling": "Greenhouse Ceiling",
    "Greenhouse Door": "Greenhouse Door", "Greenhouse Doorframe": "Greenhouse Door",
    "Large Crop Plot": "Crop Plot Large", "Medium Crop Plot": "Crop Plot Medium",
    "Basic Rifle Ammo": "Simple Rifle Bullet",     # ?? Longneck ammo == Simple Rifle Bullet
    "Longneck Rifle": "Simple Rifle",              # ?? Longneck == Simple Rifle (vs Machined=assault)
    "Metal Gate Frame": "Metal Gateway",           # ?? gate FRAME == Gateway (Metal Gate = the door)
    "Large Metal Bear Trap": "Bear Trap Large",    # ?? == Bear Trap Large
    "Scuba Mask": "Scuba Helmet Goggles",          # ?? mask == Helmet Goggles
    "Scuba Flippers": "Scuba Boots Flippers",      # ?? == Boots Flippers
    "Scuba Tank": "Scuba Shirt Suit With Tank",    # ?? tank == Shirt Suit With Tank
    "Tree Tap": "Tree Sap Tap",                    # ?? == Tree Sap Tap
    "Electrical Outlet": "Power Outlet",           # ??
    "Electrical Generator": "Power Generator",     # ??
    "Straight Electrical Cable": "Power Cable Straight",   # ??
    "Vertical Electrical Cable": "Power Cable Vertical",   # ??
    # NOTE: Refrigerator / Chemistry Bench are NOT in our engrams.json (excluded) and NO dino tame
    # path needs them (deep tier-7 only), so they carry no engram here. Revisit if the craft-graph
    # scope later needs them.
}
# ---- BOSS / CAVE / TRIBUTE logic (NOT from the tier-gates sheet; best-effort, review) ----
# Extra gear engrams the cave requirements reference (token -> our engram ap_name).
GEAR_ALIAS = {
    "Gas Mask": "Gas Mask", "Ghillie": "Ghillie Shirt", "Fur": "Fur Shirt",
    # "Scuba Tank" alias already in ALIAS above (-> Scuba Shirt Suit With Tank)
}
# their crafting station chain (so "has the engram" also needs the station to make it - avoids a
# softlock where you have the Gas Mask engram but no Fabricator to craft it).
GEAR_RECIPES = {"Gas Mask": "Fabricator", "Ghillie": "Smithy", "Fur": "Smithy"}
# metal tools are crafted from Metal Ingots (Forge) around the smithy/metal-age point - so requiring
# one also requires the Forge, landing them at the right progression tier (not sphere 0).
METAL_TOOL_RECIPES = {"Metal Pick": "Forge", "Metal Hatchet": "Forge"}

# Each Island artifact's CAVE requirement (keyed by the "Artifact: X" short name). BEST-EFFORT
# from general ARK Island knowledge - REVIEW: combat floor + environment gear. Swamp caves need a
# gas mask (user-confirmed); the underwater cave needs scuba (a water mount is implied by it).
CAVE_REQS = {
    "Hunter":   "Crossbow KO",                          # Lower South (easy land)
    "Massive":  "Crossbow KO",                          # central land
    "Clever":   "Crossbow KO + Scuba Tank",             # central cave has deep water
    "Pack":     "Crossbow KO + Fur",                    # cold cave
    "Brute":    "Rifle KO",                             # lava/tougher land
    "Devourer": "Rifle KO + Gas Mask + Ghillie",        # Swamp Cave
    "Immune":   "Rifle KO + Gas Mask + Ghillie",        # Swamp Cave (deep)
    "Skylord":  "Rifle KO + Fur",                       # snow cave
    "Strong":   "Rifle KO + Fur",                       # ice/snow cave
    "Cunning":  "Rifle KO + Scuba Tank",                # underwater cave (water mount implied)
}
# artifacts each boss's summon needs (Gamma = artifacts only; goal is any-difficulty).
BOSS_ARTIFACTS = {
    "Broodmother": ["Clever", "Hunter", "Massive"],
    "Megapithecus": ["Pack", "Brute", "Devourer"],
    "Dragon": ["Cunning", "Skylord", "Strong", "Immune"],
}
OVERSEER_BOSSES = ["Broodmother", "Megapithecus", "Dragon"]   # Overseer needs the 3 island bosses

# CAVE-DWELLING tames: taming these means surviving their cave, so their requirement OVERRIDES the
# combat method with a cave floor. Keeps foundational engrams (Mortar, Forge...) from being stranded
# behind a hard cave tame - the fill routes around the dependency.
#
# PASSIVE vs KO matters (review by Lurch9229, 2026-07-23): several of these are PASSIVE tames, so a
# tranq weapon is the wrong gate entirely - you approach unnoticed with Ghillie or Bug Repellent.
# Requiring "Crossbow KO" for them both over-gated (demanded the whole Anvil+Forge+Crossbow chain to
# tame a Dung Beetle) and under-gated (never asked for the gear that is the real barrier).
CAVE_TAMES = {
    # passive - approach gear, NOT a tranq weapon
    "Dung Beetle": "Ghillie | Bug Repellent",
    "Araneo": "Ghillie | Bug Repellent",
    "Onyc": "Ghillie | Bug Repellent",
    "Arthropleura": "Ghillie | Bug Repellent",
    # knockout tames (reviewer raised no objection to these)
    "Pulmonoscorpius": "Crossbow KO",
    "Megalania": "Crossbow KO",
    "Megalosaurus": "Rifle KO",
    # Titanoboa is NOT here on purpose: it's a passive tame that needs a FERTILIZED EGG (breeding,
    # which we don't model) and it's more easily found in the open swamp than in a cave. Listing a
    # combat floor for it was simply wrong. Its tame is instead excluded from carrying progression
    # via NO_TAME_LOGIC in __init__.py, so nothing can be stranded behind an unmodelled breeding
    # requirement (same reasoning as the breed-count milestones).
}

# Explorer notes / dossiers physically inside caves - AUTHORITATIVE from ark.wiki.gg ASE map data
# (Data:Maps/Exploration/The Island/ASE marker groups "dossier cave" / "explorer-note cave"), each
# assigned to its artifact cave. Value = the artifact whose cave it's in -> reuses cave_reqs, so the
# underwater (Cunning) cave notes require scuba automatically. Prevents stranding progression on a
# cave note. Regenerate via the Explorer Map DataMaps API if the wiki updates.
NOTE_CAVES = {
    "Dossier: Allosaurus": "Skylord",
    "Dossier: Araneo": "Clever",
    "Dossier: Carbonemys": "Strong",
    "Dossier: Cnidaria": "Cunning",
    "Dossier: Dilophosaur": "Brute",
    "Dossier: Kaprosuchus": "Hunter",
    "Dossier: Leech": "Hunter",
    "Dossier: Lystrosaurus": "Pack",
    "Dossier: Mammoth": "Pack",
    "Dossier: Manta": "Massive",
    "Dossier: Megaloceros": "Massive",
    "Dossier: Megalodon": "Clever",
    "Dossier: Megalosaurus": "Devourer",
    "Dossier: Mesopithecus": "Devourer",
    "Dossier: Mosasaurus": "Devourer",
    "Dossier: Rex": "Cunning",
    "Dossier: Tapejara": "Immune",
    "Dossier: Terror Bird": "Immune",
    "Dossier: Titanoboa": "Cunning",
    "Dossier: Titanomyrma": "Brute",
    "Dossier: Titanosaur": "Immune",
    "Dossier: Triceratops": "Massive",
    "Dossier: Trilobite": "Skylord",
    "Dossier: Tusoteuthis": "Brute",
    "Helena Note #30": "Devourer",
    "Rockwell Note #29": "Devourer",
    "Mei Yin Note #31": "Devourer",
    "??? Note #1 (idx 508)": "Devourer",
    "Helena Note #1": "Devourer",
    "Helena Note #2": "Hunter",
    "Helena Note #3": "Hunter",
    "Helena Note #4": "Brute",
    "Helena Note #9": "Cunning",
    "Helena Note #11": "Pack",
    "Helena Note #12": "Hunter",
    "Nerva Note #9": "Brute",
    "Mei Yin Note #9": "Cunning",
    "Rockwell Note #18": "Massive",
    "Rockwell Note #17": "Clever",
    "Rockwell Note #6": "Cunning",
    "Rockwell Note #9": "Brute",
}

# tribute organ (inventory-check name prefix, before " xN") -> the roster dino you kill for it.
TRIBUTE_DINO = {
    "Argentavis Talon": "Argentavis", "Sarcosuchus Skin": "Sarcosuchus",
    "Sauropod Vertebra": "Bronto", "Titanoboa Venom": "Titanoboa", "Megalania Toxin": "Megalania",
    "Megalodon Tooth": "Megalodon", "Spinosaurus Sail": "Spino", "Therizino Claws": "Therizinosaurus",
    "Thylacoleo Hook-Claw": "Thylacoleo", "Allosaurus Brain": "Allosaurus",
    "Basilosaurus Blubber": "Basilosaurus", "Giganotosaurus Heart": "Giganotosaurus",
    "Tusoteuthis Tentacle": "Tusoteuthis", "Tyrannosaurus Arm": "Rex", "Yutyrannus Lungs": "Yutyrannus",
}

# nodes that are macros/consumables/resources with NO engram of their own (flatten via recipe).
# Listed so the validator doesn't flag them as "unmapped". Crops/cooked food are free once their
# station requirement (crop plot / campfire) is met - captured by their recipe rows.
FLATTEN_ONLY = {
    "Crossbow KO", "Bow KO", "Rifle KO", "Deep Dive", "Deep Tame", "Deep Caves", "Use Electricity",
    "Cementing Paste", "Gasoline", "Sweet Veggie Cake", "Cooked Meat", "Cooked Prime Meat",
    "Cooked Fish Meat", "Cooked Prime Fish Meat", "Cooked Meat Jerky", "Cooked Prime Meat Jerky",
    "Citronal", "Savoroot", "Longrass", "Rockarrot",
    "Thatch Foundation", "Stone Foundation", "Wood Foundation", "Greenhouse Foundation",
}


# our roster short-name (dinos.json "Tame: X") -> sheet dino name, where they differ. Roster
# dinos NOT here and not spelled identically fall back to DINO_TIER-derived reqs in the apworld.
DINO_ALIAS = {
    "Compsognathus": "Compy", "Triceratops": "Trike", "Stegosaurus": "Stego",
    "Sarcosuchus": "Sarco", "Woolly Rhino": "Wooly Rhino", "Arthropleura": "Arthropluera",
    "Quetzal": "Quetzl", "Yutyrannus": "Yutyranus", "Thylacoleo": "Thylacolio",
    "Procoptodon": "Procoptrodon", "Pulmonoscorpius": "Pulmonoscorpus",
    "Lystrosaurus": "Lystosaurus", "Mesopithecus": "Mesopithicus",
    "Gigantopithecus": "Gigantopithicus", "Carcharodontosaurus": "Carchardontosaurus",
    "Direbear": "Dire bear", "Giant Bee": "Giant Queen Bee", "Therizinosaurus": "Therizino",
}


def load_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["tier gates (Base)"]
    rows = list(ws.iter_rows(values_only=True))
    items, dinos = {}, {}
    for r in rows[1:]:
        it, ireq, itier = r[0], r[1], r[2]
        dn, dreq, dtier = r[4], r[5], r[6]
        if it:
            items[str(it).strip()] = _norm(ireq)
        if dn:
            dinos[str(dn).strip()] = _norm(dreq)
    return items, dinos


def _norm(expr):
    """Normalize a requirement expression: expand the '/'-style OR shorthands, drop 'None'."""
    if not expr or str(expr).strip().lower() in ("none", ""):
        return ""
    s = str(expr).strip()
    s = s.replace("Medium/Large Crop Plot", "(Medium Crop Plot | Large Crop Plot)")
    s = s.replace("Thatch/Wood/Stone Foundation",
                  "(Thatch Foundation | Wood Foundation | Stone Foundation)")
    return s


def tokens(expr):
    return [t.strip() for t in re.split(r"[+|()]", expr) if t.strip()]


def flatten(node, recipes, seen=None):
    """Return the set of engram ap_names required to obtain/use `node`, recursing recipes.
    A node contributes its own engram (if aliased) AND everything its recipe needs."""
    seen = seen or set()
    if node in seen:                       # cycle guard
        return set()
    seen = seen | {node}
    out = set()
    if node in ALIAS:
        out.add(ALIAS[node])
    for dep in tokens(recipes.get(node, "")):
        out |= flatten(dep, recipes, seen)
    return out


def main():
    items, dinos = load_xlsx(XLSX)
    eng = json.load(open(os.path.join(ROOT, "data", "engrams.json"), encoding="utf-8"))
    engset = {e["ap_name"].replace("Engram: ", "") for e in eng["engrams"]}

    # Bow + Tranq Arrow is a valid EARLIER tranq method (crafts in inventory - no Smithy/Forge) for
    # anything a plain Crossbow KO tames. Treat a bare "Crossbow KO" as "Bow KO | Crossbow KO" so the
    # Bow is a real early method AND classifies as progression (never stranded on a late-only check).
    # Only the bare method is relaxed; "Crossbow KO + <gear>" and "Rifle KO" stay as-is (need more).
    def _allow_bow(expr):
        return "Bow KO | Crossbow KO" if expr and expr.strip() == "Crossbow KO" else expr
    dinos = {k: _allow_bow(v) for k, v in dinos.items()}
    cave_tames = {k: _allow_bow(v) for k, v in CAVE_TAMES.items()}

    # sanity: every ALIAS target (incl. gear) must be a real engram
    bad_alias = {k: v for k, v in {**ALIAS, **GEAR_ALIAS}.items() if v not in engset}
    # every referenced token must be aliased, flatten-only, or a recipe node
    all_tokens = set()
    for req in list(items.values()) + list(dinos.values()):
        all_tokens |= set(tokens(req))
    known = set(ALIAS) | FLATTEN_ONLY | set(items)
    unknown = sorted(all_tokens - known)

    # flatten each dino to its engram requirement set
    dino_reqs = {d: sorted(flatten_dino(req, items)) for d, req in dinos.items()}

    print("=== ALIAS targets missing from engrams.json (MUST FIX) ===")
    for k, v in bad_alias.items():
        print(f"  {k!r} -> {v!r}  (not an engram)")
    if not bad_alias:
        print("  (none)")
    print("\n=== tokens referenced but neither aliased nor flatten-only nor a recipe node ===")
    for t in unknown:
        print("  ", t)
    if not unknown:
        print("  (none)")

    print("\n=== dino tame -> flattened engram requirements ===")
    for d in sorted(dino_reqs):
        print(f"  {d:20} {dino_reqs[d]}")

    out = {"_comment": "Seeded from Ark IDs.xlsx by tools/gen_tame_logic.py. AP tame/craft logic. "
                       "item_recipes + dino_tame_raw + alias are the SOURCE. The apworld compiles "
                       "dino_tame_raw into boolean AP rules (AND='+', OR='|'), expanding macros via "
                       "item_recipes and mapping engram nodes via alias.",
           "item_recipes": {**items, **GEAR_RECIPES, **METAL_TOOL_RECIPES},
           "dino_tame_raw": dinos, "alias": {**ALIAS, **GEAR_ALIAS},
           "dino_alias": DINO_ALIAS,
           "cave_reqs": CAVE_REQS, "boss_artifacts": BOSS_ARTIFACTS,
           "overseer_bosses": OVERSEER_BOSSES, "tribute_dino": TRIBUTE_DINO,
           "cave_tames": cave_tames, "note_caves": NOTE_CAVES,
           "_dino_tame_engrams_ORasAND": "CONSERVATIVE approximation only (OR flattened to AND, so "
                       "it OVER-requires): use for a quick eyeball / validation, NOT as the rule.",
           "dino_tame_engrams_conservative": dino_reqs}
    dst = os.path.join(ROOT, "data", "tame_logic.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), indent=2)
    print(f"\nwrote {dst}")


def flatten_dino(req, recipes):
    out = set()
    for t in tokens(req):
        out |= flatten(t, recipes)
    return out


if __name__ == "__main__":
    main()
