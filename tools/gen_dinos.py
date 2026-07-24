#!/usr/bin/env python3
"""Generate data/dinos.json from harvested DinoNameTags.

The plugin's taming gate keys on the runtime DinoNameTag (what DinoNameTagField()
returns). Those exact strings are harvested by the TameDino hook: every tame logs
`TAME tag=X` to ArkAP_debug.log and appends the tag to dino_queue.jsonl. Feed that
file here to turn the seen tags into "Tame: X" AP items.

Usage:
  python gen_dinos.py <dino_queue.jsonl> [more.jsonl ...]
    Accepts one or more harvest files (e.g. one per map). Tags are de-duped.
    A friendly-name table maps known internal tags to readable names; unknown
    tags fall back to a CamelCase split of the tag itself.
"""
import json
import os
import re
import sys

ID_BASE = 8732001        # tame items occupy 8732001..8732xxx (between engrams 8730xxx and specials 8739xxx)
TAME_LOC_BASE = 8753000  # per-dino "tame this species" CHECK locations
KILL_LOC_BASE = 8755000  # per-dino "first kill of this species" CHECK locations

# tags that cannot be tamed in this server's config -> no Tame item, no tame check.
NO_TAME = {"Ammonite", "Cnidaria", "Cow", "Dragonfly", "Euryp", "Trilobite", "Piranha", "Coel",
           "Leech", "Salmon", "Ant"}

# tags that are NOT real ASE creatures (harvested from a modded/cross-version server by mistake) -
# dropped entirely: no tame item, no kill check. "Acro" (Acrocanthosaurus) isn't in ASE.
NOT_IN_ASE = {"Acro"}

# untameable species we STILL want a first-kill CHECK for (kill-only: no tame item/loc/saddle).
# (name, DinoNameTag[, explicit kill_loc]). kill_loc defaults to KILL_ONLY_LOC_BASE + index; use an
# explicit id for late additions so existing shipped ids never shift. "Cow" omitted - no wild
# Cow exists in ASE. Tags are best-known; verify against ArkAP_debug.log "KILL-UNMAPPED tag=" lines.
UNTAMEABLE_KILLS = [
    ("Coelacanth", "Coel"), ("Trilobite", "Trilobite"), ("Piranha", "Piranha"),
    ("Meganeura", "Dragonfly"), ("Ammonite", "Ammonite"), ("Eurypterid", "Euryp"),
    ("Jellyfish", "Cnidaria"), ("Leech", "Leech"), ("Sabertooth Salmon", "Salmon"),
    ("Titanomyrma", "Ant"),
    ("Yeti", "Yeti", 8755115),  # explicit: formula slot (8755110) collides with Onyc's forced kill id
]
KILL_ONLY_LOC_BASE = 8755100   # untameable kill checks (after the tameable 8755000..8755091 block)

# harvested tags that turned out to be UNtameable (no Tame item/loc emitted, kill check kept).
# Kept inside the main enumeration so every OTHER harvested dino's ids stay exactly stable.
KILL_ONLY_OVERRIDE = {"Leedsichthys"}

# tameable species to ALWAYS include even if not present in the tame harvest (name, DinoNameTag,
# saddle_class-or-None). Ids assigned in a high block so they never collide with the harvest set.
# 3rd field = saddle ENGRAM ap_name (resolved to its engram_class below) or None for no saddle.
FORCED_TAMEABLE = [
    ("Onyc", "Bat", None), ("Giant Bee", "Bee", None),
    ("Rhyniognatha", "Rhynio", "Engram: Saddle Rhynio"),
    ("Carcharodontosaurus", "Carcha", "Engram: Saddle Carcha"),
    ("Unicorn", "Unicorn", "Engram: Saddle Equus"),   # no dedicated saddle - reuses Equus's
]
FORCED_ID_BASE = 8732100       # tame item ids for FORCED_TAMEABLE
FORCED_TAME_LOC_BASE = 8753100
FORCED_KILL_LOC_BASE = 8755110  # matches shipped ids (Onyc 8755110 .. Unicorn 8755114) - do not shift

HERE = os.path.dirname(__file__)
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "dinos.json"))
BUNDLED = os.path.normpath(os.path.join(HERE, "..", "apworld", "ark_ase", "data", "dinos.json"))

# Known DinoNameTag -> friendly name. Internal tags often differ from the common
# name (Bronto's tag is "Sauropod", etc.). Unknown tags fall back to a CamelCase
# split. Extend as harvest reveals real tags.
FRIENDLY = {
    "Sauropod": "Brontosaurus",
    "Spider": "Araneo",
    "Bigfoot": "Gigantopithecus",
    "Toad": "Beelzebufo",
    "Beaver": "Castoroides",
    "Turtle": "Carbonemys",
    "Bat": "Onyc",
    "Monkey": "Mesopithecus",
    "Dolphin": "Ichthyosaurus",
    "Sheep": "Ovis",
    "Kangaroo": "Procoptodon",
    "Penguin": "Kairuku",
    "Squid": "Tusoteuthis",
    "Scorpion": "Pulmonoscorpius",
    "Stag": "Megaloceros",
    "Rhino": "Woolly Rhino",
    "SpiderL": "Broodmother",
    "Boa": "Titanoboa",
    "Chalico": "Chalicotherium",
    # abbreviated / codename tags that don't read cleanly on their own
    "Mega": "Megalodon",
    "Titan": "Titanosaur",
    "TitanBoa": "Titanoboa",
    "Eel": "Electrophorus",
    "Cnidaria": "Jellyfish (Cnidaria)",
    "Euryp": "Eurypterid",
    "Dragonfly": "Meganeura",
    "Bettle": "Dung Beetle",
    "Anky": "Ankylosaurus",
    "Ptera": "Pteranodon",
    "Quetz": "Quetzal",
    "Galli": "Gallimimus",
    "Doed": "Doedicurus",
    "Para": "Parasaur",
    "Dilo": "Dilophosaur",
    "Dimetro": "Dimetrodon",
    "Dimorph": "Dimorphodon",
    "Lystro": "Lystrosaurus",
    "Sarco": "Sarcosuchus",
    "Stego": "Stegosaurus",
    "Trike": "Triceratops",
    "Kentro": "Kentrosaurus",
    "Pela": "Pelagornis",
    "Paracer": "Paraceratherium",
    "Archa": "Archaeopteryx",
    "Arthro": "Arthropleura",
    "Argent": "Argentavis",
    "Coel": "Coelacanth",
    "Compy": "Compsognathus",
    "Diplo": "Diplodocus",
    "Gigant": "Giganotosaurus",
    "Ovi": "Oviraptor",
    "Pachy": "Pachycephalosaurus",
    "Kairu": "Kairuku",
    "Acro": "Acrocanthosaurus",
    "Allo": "Allosaurus",
}


def pretty(tag: str) -> str:
    if tag in FRIENDLY:
        return FRIENDLY[tag]
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tag)   # split CamelCase
    return s.strip()


# --- saddle bundling: dino_tag -> saddle engram (for the bundle_saddles yaml option) ---
# Overrides where the tag/name doesn't match the saddle wording in engrams.json.
SADDLE_OVERRIDE = {
    "Bronto": "Engram: Saddle Sauro", "Ptera": "Engram: Saddle Ptero", "Galli": "Engram: Saddle Galli",
    "Plesiosaur": "Engram: Saddle Plesia", "Titan": "Engram: Saddle Titano Platform",
    "Kangaroo": "Engram: Saddle Procop", "Mega": "Engram: Saddle Megalodon", "Anky": "Engram: Saddle Ankylo",
    "Basilosaurus": "Engram: Saddle Basilosaurus", "Diplo": "Engram: Saddle Diplodocus",
    "Dunkle": "Engram: Saddle Dunkle", "Chalicotherium": "Engram: Saddle Chalico",
    "Sabertooth": "Engram: Saddle Saber", "Doed": "Engram: Saddle Doed",
    "Pachyrhinosaurus": "Engram: Saddle Pachy Rhino", "Therizinosaurus": "Engram: Saddle Therizino",
    "Thylacoleo": "Engram: Saddle Thylaco", "Tusoteuthis": "Engram: Saddle Tuso", "Mosasaur": "Engram: Saddle Mosa",
    "Dolphin": "Engram: Saddle Dolphin", "TerrorBird": "Engram: Saddle Terror Bird",
    "Argent": "Engram: Saddle Argentavis", "Paracer": "Engram: Saddle Paracer", "Sarco": "Engram: Saddle Sarco",
    "Yutyrannus": "Engram: Saddle Yuty", "Spider": "Engram: Saddle Spider", "Turtle": "Engram: Saddle Turtle",
    "Toad": "Engram: Saddle Toad", "Stag": "Engram: Saddle Stag", "Beaver": "Engram: Saddle Beaver",
    "Rhino": "Engram: Saddle Rhino", "Arthro": "Engram: Saddle Arthro", "Scorpion": "Engram: Saddle Scorpion",
}
# dinos with no saddle (shoulder pets, fish, small/pack mounts, bareback) -> no bundle.
NO_SADDLE = {
    "Achatina", "Acro", "Ammonite", "Angler", "Archa", "Bettle", "Bigfoot", "Cnidaria", "Coel", "Compy",
    "Cow", "Dilo", "Dimetro", "Dimorph", "Diplocaulus", "Direwolf", "Dodo", "Dragonfly", "Eel", "Euryp",
    "Hesperornis", "Ichthyornis", "Kairu", "Kentro", "Leedsichthys", "Liopleurodon", "Lystro",
    "Microraptor", "Monkey", "Moschops", "Otter", "Ovi", "Pegomastax", "Piranha", "Purlovia", "Sheep",
    "TitanBoa", "Trilobite", "Troodon",
}

ENGRAMS = os.path.normpath(os.path.join(HERE, "..", "data", "engrams.json"))


def saddle_class_for(tag: str, friendly: str, by_apname: dict) -> str | None:
    if tag in NO_SADDLE:
        return None
    if tag in SADDLE_OVERRIDE:
        return by_apname.get(SADDLE_OVERRIDE[tag])
    # exact match of friendly/tag against a saddle engram's core name
    def norm(s): return re.sub(r"[^a-z]", "", s.lower())
    for ap, cls in by_apname.items():
        if "Saddle" not in ap or "Platform" in ap or "Tek" in ap:
            continue
        core = norm(ap.replace("Engram:", "").replace("Saddle", ""))
        if core and (core == norm(friendly) or core == norm(tag)):
            return cls
    return None


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: gen_dinos.py <dino_queue.jsonl> [more.jsonl ...]")

    tags = []
    seen = set()
    for src in sys.argv[1:]:
        with open(src, encoding="utf-8") as fh:
            for line in fh:
                t = line.strip().strip('"')
                if t and t not in seen and t not in NOT_IN_ASE:
                    seen.add(t)
                    tags.append(t)
    forced_tags = {t for _, t, _ in FORCED_TAMEABLE}
    tags = [t for t in sorted(tags)                        # drop untameable + forced (added below)
            if t not in NO_TAME and t not in forced_tags]

    by_apname = {}
    try:
        eng = json.load(open(ENGRAMS, encoding="utf-8"))
        by_apname = {g["ap_name"]: g["engram_class"] for g in eng["engrams"]}
    except Exception as ex:
        print("warning: could not load engrams.json for saddle map:", ex)

    dinos = []
    for i, t in enumerate(tags):
        fr = pretty(t)
        if t in KILL_ONLY_OVERRIDE:   # untameable after all: kill check only, same slot -> ids stable
            dinos.append({"name": fr, "dino_tag": t, "kill_loc": KILL_LOC_BASE + i, "tameable": False})
            continue
        dinos.append({"id": ID_BASE + i, "ap_name": "Tame: " + fr, "dino_tag": t,
                      "saddle_class": saddle_class_for(t, fr, by_apname),
                      "tame_loc": TAME_LOC_BASE + i,    # check fired when you tame this species
                      "kill_loc": KILL_LOC_BASE + i})   # check fired on first kill of this species
    # append untameable kill-only entries (no tame item/loc/saddle -> just a first-kill check).
    for j, entry in enumerate(UNTAMEABLE_KILLS):
        name, tag = entry[0], entry[1]
        loc = entry[2] if len(entry) > 2 else KILL_ONLY_LOC_BASE + j
        dinos.append({"name": name, "dino_tag": tag, "kill_loc": loc, "tameable": False})
    # append always-included tameable species (missed by the harvest). saddle = engram ap_name -> class.
    for j, (name, tag, saddle) in enumerate(FORCED_TAMEABLE):
        dinos.append({"id": FORCED_ID_BASE + j, "ap_name": "Tame: " + name, "dino_tag": tag,
                      "saddle_class": by_apname.get(saddle) if saddle else None,
                      "tame_loc": FORCED_TAME_LOC_BASE + j, "kill_loc": FORCED_KILL_LOC_BASE + j})
    # no two entries may share an item id / tame_loc / kill_loc (shipped ids are load-bearing:
    # they're frozen into every generated multidata's datapackage).
    for key in ("id", "tame_loc", "kill_loc"):
        vals = [d[key] for d in dinos if key in d]
        dupes = {v for v in vals if vals.count(v) > 1}
        assert not dupes, f"duplicate {key} in generated dinos: {sorted(dupes)}"

    with_saddle = sum(1 for d in dinos if d.get("saddle_class"))

    out = {
        "_comment": f"Generated by gen_dinos.py from {len(tags)} harvested DinoNameTags "
                    f"({with_saddle} with a saddle for bundle_saddles). "
                    f"dino_tag = runtime DinoNameTagField() string, matched by the plugin's taming gate.",
        "_id_base": ID_BASE,
        "dinos": dinos,
    }
    for path in (OUT, BUNDLED):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"wrote {len(dinos)} dinos -> {path}")


if __name__ == "__main__":
    main()
