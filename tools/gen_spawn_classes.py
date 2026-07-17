#!/usr/bin/env python3
"""Rebuild data/spawn_classes.json from REAL harvested dino class names.

The randomize_dino_spawns shuffle uses Game.ini NPCReplacements, which match on the exact
Character_BP class name. The original list was wiki-guessed and mostly wrong (empty spawns).
The plugin (v73+) harvests the real names: run '/dumpdinos' in-game around spawn zones and/or
'cheat DestroyWildDinos', which writes each real class to ArkAP_dino_classes.jsonl.

  python tools/gen_spawn_classes.py <ArkAP_dino_classes.jsonl> [more.jsonl ...]

Feed that harvest file here. This classifies each class into a habitat group (for 'grouped'
mode) and drops non-wild / boss / alpha / tek / special classes, then writes both copies of
spawn_classes.json. Review the output - unknowns default to 'land' and are listed so you can
correct WATER_KEYS / AIR_KEYS / EXCLUDE below.
"""
import json
import os
import sys

HERE = os.path.dirname(__file__)
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "spawn_classes.json"))
BUNDLED = os.path.normpath(os.path.join(HERE, "..", "apworld", "ark_ase", "data", "spawn_classes.json"))

# class-name substrings that mark a class as NOT a normal wild spawn to shuffle.
# alphas are the Mega*/Alpha_* predators + _Mega variants; Bionic* = tek-skin spawns;
# bosses/minions/event variants excluded. (All verified against a real Island harvest.)
EXCLUDE = (
    "Boss", "Tek", "Bionic", "Minion", "Corrupt", "Eden", "Gauntlet", "Escort", "_VR",
    "Character_BP_Aggressive",
    "MegaRaptor", "MegaCarno", "MegaRex", "MegaMegalodon", "Mega_", "_Mega", "Alpha",
    "Retrieve", "Summon", "Baby", "Gen2", "Bog", "STA_", "Race", "Hunt", "Mission",
    "Titanosaur", "Bee_Queen", "Bee_Character",  # map-limited mega spawn / hive mechanic
)

# The Island's cave roster - kept OUT of the shuffle entirely (their spawns stay vanilla).
# Cave spawn zones are tight/dark/artifact-guarding; a shuffled Paracer in a crouch-tunnel
# gets stuck, and cave-only species vanishing from caves breaks their kill checks.
CAVE_CLASSES = (
    "Bat_Character_BP_C",            # Onyc
    "SpiderS_Character_BP_C",        # Araneo
    "Scorpion_Character_BP_C",       # Pulmonoscorpius
    "Arthro_Character_BP_C",         # Arthropleura
    "Megalosaurus_Character_BP_C",
    "BoaFrill_Character_BP_C",       # Titanoboa
    "DungBeetle_Character_BP_C",
    "Dragonfly_Character_BP_C",      # Meganeura
    "Megalania_Character_BP_C",
    "Yeti_Character_BP_C",
)

# danger classification (grouped mode down-weights predators so zones aren't predator-saturated:
# apex EntryWeight 0.2, mid 0.5, docile 1.0 - the weights themselves live in the apworld).
APEX_STEMS = ("Rex_", "Gigant_", "Carcha_", "Spino_", "Allo_", "Yutyrannus", "Therizino",
              "Mosa_", "Plesiosaur", "Tusoteuthis", "Rhynio", "Leeds", "Liopleurodon")
MID_STEMS = ("Carno_", "Raptor_", "Sarco_", "Kaprosuchus", "Baryonyx", "Direbear", "Direwolf",
             "Thylacoleo", "Purlovia", "TerrorBird", "Saber_", "Hyaenodon", "Daeodon", "Troodon",
             "Bigfoot", "Chalico", "Argent_", "Quetz", "Dimorph", "Microraptor", "FlyingAnt",
             "Megalodon", "Dunkle", "Angler", "Eel_", "Cnidaria", "Piranha", "Manta",
             "Euryp")


def danger(cls: str) -> str:
    if any(s in cls for s in APEX_STEMS):
        return "apex"
    if any(s in cls for s in MID_STEMS):
        return "mid"
    return "docile"


# habitat classification by class-name substring. Everything not matched = land.
WATER_KEYS = (
    "Coel", "Dolphin", "Ichthyosaur", "Megalodon", "Mosa", "Plesiosaur", "Angler", "Manta",
    "Dunkle", "Basilo", "Leech", "Leeds", "Trilobite", "Eel", "Electrophorus", "Cnidaria", "Jellyfish",
    "Euryp", "Salmon", "Piranha", "Tuso", "Liopleurodon", "Diplocaulus", "Ammonite", "Otter",
    "Hesperornis", "Sabertooth_Salmon", "Lamprey",
)
AIR_KEYS = (
    "Ptero", "Argent", "Pela", "Dimorph", "Bat", "Ichthyornis", "Quetz", "Tapejara", "Rhynio",
    "Vulture", "Microraptor", "Archa", "FlyingAnt",   # Microraptor/Archa glide - treat as air-ish
)

# rare/cave/deep spawns that a normal harvest pass often misses, but whose class names are
# verified against ark.wiki.gg blueprint paths (2026-07-14 audit). Appended if not harvested.
WIKI_CONFIRMED_EXTRA = (
    ("Quetzal",          "Quetz_Character_BP_C",        "air"),
    ("Giganotosaurus",   "Gigant_Character_BP_C",       "land"),
    ("Megalania",        "Megalania_Character_BP_C",    "land"),
    ("Megalosaurus",     "Megalosaurus_Character_BP_C", "land"),
    ("Castoroides",      "Beaver_Character_BP_C",       "land"),
    ("Rhyniognatha",     "Rhynio_Character_BP_C",       "air"),
    ("Carcharodontosaurus", "Carcha_Character_BP_C",    "land"),
    ("Liopleurodon",     "Liopleurodon_Character_BP_C", "water"),
    ("Ammonite",         "Ammonite_Character_C",        "water"),
    ("Eurypterid",       "Euryp_Character_C",           "water"),
    ("Trilobite",        "Trilobite_Character_C",       "water"),
    ("Leech",            "Leech_Character_C",           "water"),
)


def habitat(cls: str) -> str:
    low = cls.lower()
    if any(k.lower() in low for k in WATER_KEYS):
        return "water"
    if any(k.lower() in low for k in AIR_KEYS):
        return "air"
    return "land"


def pretty(cls: str) -> str:
    # "Raptor_Character_BP_C" -> "Raptor"; "Purlovia_Character_BP_Polar_C" -> "Purlovia Polar"
    name = cls
    if name.endswith("_C"):
        name = name[:-2]
    name = name.replace("_Character_BP", "").replace("_Character", "")
    return name.replace("_", " ").strip()


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: gen_spawn_classes.py <ArkAP_dino_classes.jsonl> [more.jsonl ...]")

    classes = []
    seen = set()
    for src in sys.argv[1:]:
        with open(src, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    cls = json.loads(line)["class"]
                except Exception:
                    continue
                if cls and cls not in seen:
                    seen.add(cls)
                    classes.append(cls)

    kept, dropped = [], []
    for cls in sorted(classes):
        # note: some real classes have no _BP (Ammonite_Character_C etc) - accept _Character too
        if any(x in cls for x in EXCLUDE) or cls in CAVE_CLASSES or "_Character" not in cls:
            dropped.append(cls)
            continue
        kept.append({"name": pretty(cls), "class": cls, "habitat": habitat(cls),
                     "danger": danger(cls)})

    harvested = {e["class"] for e in kept}
    extras = [{"name": n, "class": c, "habitat": h, "danger": danger(c)}
              for n, c, h in WIKI_CONFIRMED_EXTRA
              if c not in harvested and c not in CAVE_CLASSES]
    kept += extras

    by_hab = {"land": 0, "water": 0, "air": 0}
    for e in kept:
        by_hab[e["habitat"]] += 1

    out = {
        "_comment": f"Generated by gen_spawn_classes.py from {len(classes)} harvested classes "
                    f"({len(kept)} kept / {len(dropped)} excluded). habitat groups keep 'grouped' "
                    f"mode sane (land<->land, water<->water, air<->air). Class names are HARVESTED "
                    f"from the live server (ArkAP_dino_classes.jsonl), not guessed.",
        "spawn_classes": kept,
    }
    for path in (OUT, BUNDLED):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"wrote {len(kept)} classes ({by_hab}) -> {path}")
    print(f"\nExcluded {len(dropped)}:")
    for c in dropped:
        print("  ", c)
    print("\nReview 'land' classifications for anything aquatic/flying that slipped through, "
          "and adjust WATER_KEYS / AIR_KEYS / EXCLUDE at the top of this script if needed.")


if __name__ == "__main__":
    main()
