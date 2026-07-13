#!/usr/bin/env python3
"""Generate data/locations.json - The Island rebalance.

Explorer notes come from a hardcoded ISLAND whitelist (indices harvested from
https://ark.wiki.gg/wiki/Explorer_Notes/Locations), NOT the global 1232-entry game table -
notes from other maps can never be collected on The Island and would strand items.
Any note the plugin logs as "NOTE idx=N (not mapped)" in-game is a whitelist miss: add it here.

Also emits: bosses x3 difficulties, alpha-predator kills, count milestones
(tame/kill species + notes), and "Reach Level N" for every level 2..150.

  python tools/gen_locations.py            (no harvest file needed anymore)
"""
import json
import os

HERE = os.path.dirname(__file__)
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "locations.json"))
BUNDLED = os.path.normpath(os.path.join(HERE, "..", "apworld", "ark_ase", "data", "locations.json"))

NOTE_ID_BASE = 8740000      # 8740000 + note_index (stable: same scheme as before)
BOSS_ID_BASE = 8750000
MILESTONE_ID_BASE = 8751000
LEVEL_ID_BASE = 8754000
ALPHA_ID_BASE = 8756000
INV_ID_BASE = 8757000       # "hold X in inventory" checks (artifacts + apex-drop gathers)
WORLD_ITEM_ID_BASE = 8739100

# ---------------- The Island explorer notes (index -> display name) ----------------
DOSSIERS = {  # creature dossiers collectible on The Island
    141: "Achatina", 6: "Allosaurus", 233: "Ammonite", 7: "Anglerfish", 8: "Ankylosaurus",
    64: "Araneo", 9: "Archaeopteryx", 10: "Argentavis", 11: "Arthropleura", 174: "Baryonyx",
    175: "Basilosaurus", 281: "Giant Bee", 14: "Beelzebufo", 16: "Brontosaurus", 74: "Carbonemys",
    1230: "Carcharodontosaurus", 17: "Carnotaurus", 12: "Castoroides", 18: "Chalicotherium",
    160: "Cnidaria", 19: "Coelacanth", 20: "Compy", 282: "Daeodon", 0: "Dilophosaur",
    21: "Dimetrodon", 22: "Dimorphodon", 24: "Diplocaulus", 23: "Diplodocus", 25: "Dire Bear",
    26: "Direwolf", 27: "Dodo", 28: "Doedicurus", 13: "Dung Beetle", 30: "Dunkleosteus",
    234: "Electrophorus", 237: "Equus", 31: "Eurypterid", 32: "Gallimimus", 33: "Giganotosaurus",
    15: "Gigantopithecus", 302: "Hesperornis", 298: "Hyaenodon", 239: "Ichthyornis",
    34: "Ichthyosaurus", 240: "Iguanodon", 35: "Kairuku", 36: "Kaprosuchus", 283: "Kentrosaurus",
    37: "Leech", 238: "Leedsichthys", 284: "Liopleurodon", 38: "Lystrosaurus", 39: "Mammoth",
    40: "Manta", 299: "Megalania", 41: "Megaloceros", 42: "Megalodon", 43: "Megalosaurus",
    29: "Meganeura", 301: "Megatherium", 44: "Mesopithecus", 235: "Microraptor", 45: "Mosasaurus",
    142: "Moschops", 46: "Onyc", 355: "Otter", 47: "Oviraptor", 173: "Ovis", 48: "Pachy",
    143: "Pachyrhinosaurus", 49: "Paraceratherium", 50: "Parasaur", 163: "Pegomastax",
    51: "Pelagornis", 52: "Phiomia", 53: "Piranha", 54: "Plesiosaur", 55: "Procoptodon",
    56: "Pteranodon", 63: "Pulmonoscorpius", 176: "Purlovia", 57: "Quetzal", 58: "Raptor",
    71: "Rex", 1231: "Rhyniognatha", 60: "Sabertooth", 61: "Sabertooth Salmon", 62: "Sarco",
    65: "Spino", 66: "Stegosaurus", 67: "Tapejara", 68: "Terror Bird", 164: "Therizinosaur",
    236: "Thylacoleo", 70: "Titanoboa", 1: "Titanomyrma", 69: "Titanosaur", 72: "Triceratops",
    73: "Trilobite", 161: "Troodon", 162: "Tusoteuthis", 59: "Woolly Rhino", 300: "Yutyrannus",
}
HELENA = ([75, 76, 77, 78, 123, 127, 128, 145, 153, 166] + list(range(185, 193))
          + list(range(249, 257)) + [285, 286, 287, 352])   # 352 = Helena #30, Tek Cave ascension area
ROCKWELL = ([2, 79, 80, 81, 118, 137, 138, 147, 155, 168] + list(range(177, 185))
            + list(range(241, 249)) + [296, 297, 351])
MEIYIN = ([3, 4, 82, 83, 124, 125, 126, 144, 152, 165] + list(range(193, 201))
          + list(range(257, 265)) + [288, 289, 290, 291, 354])
NERVA = ([5, 84, 85, 86, 121, 131, 132, 146, 154, 167] + list(range(201, 209))
         + list(range(265, 273)) + [292, 293, 294, 295])
MISC = {  # ??? notes, boss holograms, HLN-A, Genesis chronicles present on The Island
    # ??? notes carry their raw wiki note index (ark.wiki.gg Explorer_Notes/Locations) so players
    # can actually look them up - our #N alone matches nothing on the wiki.
    508: "??? Note #1 (idx 508)", 511: "??? Note #2 (idx 511)", 514: "??? Note #3 (idx 514)",
    517: "??? Note #4 (idx 517)", 520: "??? Note #5 (idx 520)",
    87: "Hologram: Broodmother", 88: "Hologram: Megapithecus", 89: "Hologram: Dragon",
    353: "Hologram: Overseer",
    688: "HLN-A Discovery #1", 689: "HLN-A Discovery #2", 690: "HLN-A Discovery #3",
    853: "Genesis Chronicles #1", 854: "Genesis Chronicles #2", 855: "Genesis Chronicles #3",
    856: "Genesis Chronicles #4", 857: "Genesis Chronicles #5",
}

# ---------------- other categories ----------------
LEVELS = list(range(2, 106))          # every level 2..105 (max base player level)
BOSS_DIFFS = [("Gamma", 0), ("Beta", 10), ("Alpha", 20)]   # id offset per difficulty
BOSS_BASES = [  # (id offset within difficulty block, short name, base tag)
    (0, "Broodmother", "SpiderBoss"),
    (1, "Megapithecus", "GorillaBoss"),
    (2, "Dragon", "DragonBoss"),
    (3, "Overseer", "Overseer"),
]
ALPHAS = [  # class-name fragments; verify in-game via "KILL tag=" / "BOSS-DEATH unmatched" logs
    (0, "Alpha Raptor", "MegaRaptor"),
    (1, "Alpha Carno", "MegaCarno"),
    (2, "Alpha Rex", "MegaRex"),
    (3, "Alpha Megalodon", "MegaMegalodon"),   # MegaMegalodon_Character_BP_C (confirmed in-game)
    (4, "Alpha Leedsichthys", "Alpha_Leedsichthys"),
    (5, "Alpha Mosasaur", "Mosa_Character_BP_Mega"),
    (6, "Alpha Tusoteuthis", "Mega_Tusoteuthis"),
]
# inventory "hold X" checks. item_class = substring matched against the item's GetFullName.
# (short, class-substring) - hold 1 fires the check. Tier 3 (deep caves).
ARTIFACTS_INV = [
    ("Clever", "PrimalItemArtifact_05"), ("Hunter", "PrimalItemArtifact_01"),
    ("Massive", "PrimalItemArtifact_03"), ("Cunning", "PrimalItemArtifact_11"),
    ("Immune", "PrimalItemArtifact_08"), ("Skylord", "PrimalItemArtifact_06"),
    ("Strong", "PrimalItemArtifact_09"), ("Brute", "PrimalItemArtifact_12"),
    ("Devourer", "PrimalItemArtifact_07"), ("Pack", "PrimalItemArtifact_02"),
]
# (item name, class-substring, base qty, tier) - each makes TWO checks: base and 2x base.
APEX_INV = [
    ("Argentavis Talon", "PrimalItemResource_ApexDrop_Argentavis", 5, 2),
    ("Sarcosuchus Skin", "PrimalItemResource_ApexDrop_Sarco", 5, 2),
    ("Sauropod Vertebra", "PrimalItemResource_ApexDrop_Sauro", 5, 2),
    ("Titanoboa Venom", "PrimalItemResource_ApexDrop_Boa", 5, 2),
    ("Megalania Toxin", "PrimalItemResource_ApexDrop_Megalania", 5, 2),
    ("Megalodon Tooth", "PrimalItemResource_ApexDrop_Megalodon", 5, 2),
    ("Spinosaurus Sail", "PrimalItemResource_ApexDrop_Spino", 5, 3),
    ("Therizino Claws", "PrimalItemResource_ApexDrop_Theriz", 5, 2),
    ("Thylacoleo Hook-Claw", "PrimalItemResource_ApexDrop_Thylaco", 5, 2),
    ("Allosaurus Brain", "PrimalItemResource_ApexDrop_Allo", 5, 2),
    ("Basilosaurus Blubber", "PrimalItemResource_ApexDrop_Basilo", 5, 2),
    ("Giganotosaurus Heart", "PrimalItemResource_ApexDrop_Giga", 1, 3),
    ("Tusoteuthis Tentacle", "PrimalItemResource_ApexDrop_Tuso", 5, 3),
    ("Tyrannosaurus Arm", "PrimalItemResource_ApexDrop_Rex", 5, 3),
    ("Yutyrannus Lungs", "PrimalItemResource_ApexDrop_Yuty", 5, 3),
]

# collective counts (same species counts each time) - (count, tier). Plugin runs live counters.
TAME_TOTALS = [(5, 0), (10, 1), (20, 1), (50, 2), (100, 3)]
KILL_TOTALS = [(5, 0), (10, 0), (20, 1), (50, 2), (100, 3)]
# distinct-species counts - (count, tier). One per unique creature tamed/killed (the "Tamed: X"/
# "Killed: X" checks). Tames capped at 50 (only ~96 tameable species).
TAME_SPECIES = [(5, 1), (10, 2), (20, 2), (50, 3)]
KILL_SPECIES = [(5, 0), (10, 1), (20, 1), (50, 2), (100, 3)]
NOTE_COUNTS = list(range(25, 251, 25))       # Collect 25..250 notes

# additional "collect N of resource" inventory checks (name, class-substring, qty, tier).
RESOURCE_INV = [
    ("Collect 1000 Hide", "PrimalItemResource_Hide", 1000, 1),
    ("Collect 5 Woolly Rhino Horn", "PrimalItemResource_Horn", 5, 2),
    ("Collect 250 Silica Pearls", "PrimalItemResource_Silicon", 250, 2),
    ("Collect 250 Oil", "PrimalItemResource_Oil", 250, 2),
    ("Collect 100 Obsidian", "PrimalItemResource_Obsidian", 100, 2),
    ("Collect 100 Crystal", "PrimalItemResource_Crystal", 100, 2),
]

# food "hold N" inventory checks (ark.wiki.gg/wiki/Food). The food_sanity yaml option includes a
# percentage of these. Marked "food": true so the apworld can tell them apart from the checks above;
# the plugin treats them like any other inventory check. Substrings chosen to not cross-match:
# "CookedMeat_C" can't match CookedMeat_Fish_C / CookedMeat_Jerky_C (the _C anchors the class end).
FOOD_INV = [
    ("Citronal x10", "Veggie_Citronal", 10, 1),
    ("Longrass x10", "Veggie_Longrass", 10, 1),
    ("Rockarrot x10", "Veggie_Rockarrot", 10, 1),
    ("Savoroot x10", "Veggie_Savoroot", 10, 1),
    ("Cooked Meat x20", "PrimalItemConsumable_CookedMeat_C", 20, 0),
    ("Cooked Meat Jerky x5", "CookedMeat_Jerky", 5, 2),
    ("Cooked Prime Meat x20", "CookedPrimeMeat_C", 20, 1),
    ("Prime Meat Jerky x5", "CookedPrimeMeat_Jerky", 5, 2),
    ("Cooked Fish Meat x20", "CookedMeat_Fish", 20, 0),
    ("Cooked Prime Fish Meat x20", "CookedPrimeMeat_Fish", 20, 1),
    ("Giant Bee Honey x3", "PrimalItemConsumable_Honey", 3, 1),
    ("Rare Flower x50", "PrimalItemResource_RareFlower", 50, 0),
    ("Rare Mushroom x50", "PrimalItemResource_RareMushroom", 50, 0),
    ("Plant Species X Seed x20", "Seed_PlantSpeciesX", 20, 1),
]

# breeding milestones - every mating event counts (fertilized egg laid OR gestation started),
# same species repeatable. Plugin counts via the DoMate hook. (count, tier).
BREED_TOTALS = [(5, 1), (10, 2), (20, 2)]


def main() -> None:
    notes = []
    for idx, creature in sorted(DOSSIERS.items()):
        notes.append({"id": NOTE_ID_BASE + idx, "name": f"Dossier: {creature}", "note_index": idx})
    for label, seq in (("Helena", HELENA), ("Rockwell", ROCKWELL), ("Mei Yin", MEIYIN), ("Nerva", NERVA)):
        for k, idx in enumerate(seq, 1):
            notes.append({"id": NOTE_ID_BASE + idx, "name": f"{label} Note #{k}", "note_index": idx})
    for idx, name in sorted(MISC.items()):
        notes.append({"id": NOTE_ID_BASE + idx, "name": name, "note_index": idx})
    assert len({n["note_index"] for n in notes}) == len(notes), "duplicate note index in whitelist"
    notes.sort(key=lambda n: n["note_index"])

    bosses = []
    for diff, doff in BOSS_DIFFS:
        for boff, short, tag in BOSS_BASES:
            bosses.append({"id": BOSS_ID_BASE + doff + boff,
                           "name": f"Boss: {short} ({diff})", "tag": f"{tag}_{diff}"})

    alphas = [{"id": ALPHA_ID_BASE + off, "name": f"Killed: {name}", "class_frag": frag}
              for off, name, frag in ALPHAS]

    milestones = [{"id": MILESTONE_ID_BASE + 0, "name": "Tame your first dino",
                   "tag": "milestone_first_tame", "tier": 0}]
    mid = 10
    for n, tier in TAME_TOTALS:
        milestones.append({"id": MILESTONE_ID_BASE + mid, "name": f"Tame {n} Creatures",
                           "tag": f"milestone_tametotal_{n}", "tier": tier}); mid += 1
    for n, tier in KILL_TOTALS:
        milestones.append({"id": MILESTONE_ID_BASE + mid, "name": f"Kill {n} Creatures",
                           "tag": f"milestone_killtotal_{n}", "tier": tier}); mid += 1
    for n, tier in TAME_SPECIES:
        milestones.append({"id": MILESTONE_ID_BASE + mid, "name": f"Tame {n} Species",
                           "tag": f"milestone_tames_{n}", "tier": tier}); mid += 1
    for n, tier in KILL_SPECIES:
        milestones.append({"id": MILESTONE_ID_BASE + mid, "name": f"Kill {n} Species",
                           "tag": f"milestone_kills_{n}", "tier": tier}); mid += 1
    for n in NOTE_COUNTS:
        tier = 0 if n <= 50 else 1 if n <= 150 else 2
        milestones.append({"id": MILESTONE_ID_BASE + mid, "name": f"Collect {n} Explorer Notes",
                           "tag": f"milestone_notes_{n}", "tier": tier}); mid += 1
    # breeding: appended AFTER every existing block so earlier milestone ids never shift.
    milestones.append({"id": MILESTONE_ID_BASE + mid, "name": "Breed your first dino",
                       "tag": "milestone_first_breed", "tier": 1}); mid += 1
    for n, tier in BREED_TOTALS:
        milestones.append({"id": MILESTONE_ID_BASE + mid, "name": f"Breed {n} Dinos",
                           "tag": f"milestone_breedtotal_{n}", "tier": tier}); mid += 1

    levels = [{"id": LEVEL_ID_BASE + i, "name": f"Reach Level {n}", "level": n}
              for i, n in enumerate(LEVELS)]

    inv = []
    for i, (short, cls) in enumerate(ARTIFACTS_INV):
        inv.append({"id": INV_ID_BASE + i, "name": f"Artifact: {short}",
                    "item_class": cls, "qty": 1, "tier": 3})
    for j, (name, cls, base, tier) in enumerate(APEX_INV):
        for k, q in enumerate((base, base * 2)):
            inv.append({"id": INV_ID_BASE + 100 + j * 2 + k, "name": f"{name} x{q}",
                        "item_class": cls, "qty": q, "tier": tier})
    for i, (name, cls, qty, tier) in enumerate(RESOURCE_INV):
        inv.append({"id": INV_ID_BASE + 200 + i, "name": name,
                    "item_class": cls, "qty": qty, "tier": tier})
    for i, (name, cls, qty, tier) in enumerate(FOOD_INV):
        inv.append({"id": INV_ID_BASE + 300 + i, "name": name,
                    "item_class": cls, "qty": qty, "tier": tier, "food": True})
    assert len({e["id"] for e in inv}) == len(inv), "duplicate inventory-check id"

    world_items = [{"id": WORLD_ITEM_ID_BASE + i, "name": f"Boss Access: {short}",
                    "kind": "boss_access", "tag": tag} for i, (_, short, tag) in enumerate(BOSS_BASES)]

    out = {
        "_comment": f"Generated by gen_locations.py (Island whitelist). {len(notes)} island notes + "
                    f"{len(bosses)} boss checks + {len(alphas)} alpha kills + {len(milestones)} milestones + "
                    f"{len(levels)} levels. note id = 8740000 + ExplorerNoteIndex.",
        "_loc_id_base": NOTE_ID_BASE,
        "location_categories": {
            "dossiers":    {"_note": "The Island collectible notes only (whitelist from ark.wiki.gg)",
                            "map": "TheIsland", "entries": notes},
            "bosses":      {"entries": bosses},
            "alpha_kills": {"entries": alphas},
            "milestones":  {"entries": milestones},
            "levels":      {"entries": levels},
            "inventory_checks": {"_note": "hold N of item_class -> fires (persists after you use them)",
                                 "entries": inv},
        },
        "_world_item_id_base": WORLD_ITEM_ID_BASE,
        "world_items": {"entries": world_items},
    }
    for path in (OUT, BUNDLED):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"wrote {len(notes)} notes + {len(bosses)} bosses + {len(alphas)} alphas + "
              f"{len(milestones)} milestones + {len(levels)} levels + {len(inv)} inventory -> {path}")


if __name__ == "__main__":
    main()
