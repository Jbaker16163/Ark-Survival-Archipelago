#!/usr/bin/env python3
"""Turn an ArkAP.DumpEngrams dump (taken on a server WITH mods installed) into mod catalog files.

The plugin gates engrams on the exact UClass GetFullName, so mod data MUST come from a real server
dump - there is no offline way to know what engrams a workshop id contains.

Vanilla engrams live under six known /Game/<root>/ paths; anything else is modded, and mods load
under /Game/Mods/<folder>/. That folder is usually the workshop id, sometimes a name - map the
names with --map.

Usage:
  # see what's in a dump and how it attributes, WITHOUT writing anything
  python gen_mod.py dump.json --report

  # write catalog entries (ids are allocated once and then frozen - see --report first)
  python gen_mod.py dump.json --write \
      --map StructuresPlusMod=731604991 \
      --name 731604991="Structures Plus" --kind 731604991=building

Ids: MOD_ID_BASE + MOD_ID_STRIDE * slot, slot assigned in index.json order. An id that has SHIPPED
must never move - it is baked into every player's datapackage - so existing entries are reused.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
MODS_DIR = os.path.join(ROOT, "apworld", "ark_ase", "data", "mods")
INDEX = os.path.join(MODS_DIR, "index.json")
BASE_ENGRAMS = os.path.join(ROOT, "apworld", "ark_ase", "data", "engrams.json")

MOD_ID_BASE = 8760000
MOD_ID_STRIDE = 10000
# Official content roots. DLC MAPS count as vanilla: a server that has Valguero/Fjordur/etc content
# installed reports their saddles too, and they are base-game engrams, not mod content.
VANILLA_ROOTS = {"PrimalEarth", "Aberration", "Extinction", "Genesis", "Genesis2", "ScorchedEarth",
                 "Valguero", "LostIsland", "Fjordur", "TheCenter", "Ragnarok", "CrystalIsles",
                 "PrimalEarthObsolete"}


# ---------------------------------------------------------------------------------------------
# Curated per-mod ITEM GROUPS. One AP item unlocks every member - the same idea as the material
# structure bundles, but for a mod's repetitive variants and QoL clusters. Reviewed with the mod
# author's user (2026-07-24): repetitive variants + automation/QoL + dino care are grouped;
# crafting stations, teleport/transfer and one-off gadgets stay INDIVIDUAL because they are real,
# distinct unlocks worth finding.
# Patterns: exact name, or "prefix*". Names are ap_names WITHOUT the "Engram: " prefix.
# Ids come from id_base + GROUP_ID_OFFSET so they never collide with the mod's engram ids.
GROUP_ID_OFFSET = 9000

# Craft prerequisites for specific MOD engrams, as base-game/mod item names. Emitted as `requires`
# and merged into the tame-logic recipe graph, so anything that ever depends on the mod engram also
# demands its prerequisite. (Explorer Note Tracker is craftable only once you have the GPS.)
# Workshop ids that ship the SAME content under the SAME /Game/Mods/<folder>/ (a fork that kept the
# parent's content folder). One catalog entry serves them all: listing EITHER id in mod_ids yields
# identical items, and the class paths the plugin matches are byte-identical either way.
MOD_ALIASES = {
    "731604991": ["1999447172"],       # Structures Plus  <- Super Structures (fork, same folder)
}

MOD_ENGRAM_REQUIRES = {
    "1631378184": {"Engram: Note Tracker": ["Engram: GPS"]},
}

# Engrams AUTO-GRANTED (free at start, removed from the pool) when a mod is active. For pure-QoL
# mods that are pointless to hunt for. Names may be base-game engrams too. Explorer Note Tracker is
# a tracker with no checks behind it - just give the tracker + the GPS it needs to craft.
MOD_AUTO_GRANT = {
    "1631378184": ["Engram: Note Tracker", "Engram: GPS"],
}
MOD_GROUPS = {
    "731604991": [                       # Structures Plus
        # -- repetitive variants --
        ("Bundle: S+ Wiring",            ["Internal Wire *", "Wire *"]),
        ("Bundle: S+ Plumbing",          ["Internal Pipe *"]),
        ("Bundle: S+ Turrets",           ["Auto Turret*"]),
        ("Bundle: S+ Platform Saddles",  ["Platform Saddle *"]),
        ("Bundle: S+ Crop Plots",        ["Crop Plot Plus *"]),
        ("Bundle: S+ Taxidermy",         ["Taxidermy Plus *"]),
        ("Bundle: S+ Forcefields",       ["Wall Forcefield", "Large Wall Forcefield",
                                          "XLWall Forcefield"]),
        ("Bundle: S+ Fridges",           ["Fridge Plus", "Fridge Cryo", "Cryo Fridge SS"]),
        ("Bundle: S+ Elevators",         ["Elevator *"]),
        ("Bundle: S+ Trophies",          ["Trophy Base SS", "Trophy Wall SS"]),
        ("Bundle: S+ Loadout Dummies",   ["Loadout Dummy *"]),
        ("Bundle: S+ Beds",              ["Bed Plus", "Bunk Bed Plus"]),
        ("Bundle: S+ Underwater Structures",
                                         ["Underwater Cube", "Underwater Cube Sloped",
                                          "Underwater Moonpool", "Trapdoor Moonpool",
                                          "Door Underwater"]),
        # -- automation / QoL --
        ("Bundle: S+ Automation",        ["Item Collector", "Item Aggregator", "Item Translocator",
                                          "Inventory Assistant", "Dedicated Storage *",
                                          "Vault Plus", "Storage Large", "Storage Small",
                                          "Preserving Bin Plus", "Compost Bin Plus",
                                          "Harvester Plus", "Gardener", "Farmer", "Sheep Herder",
                                          "Gacha Gavager"]),
        # -- dino care --
        ("Bundle: S+ Dino Care",         ["Nanny", "Hatchery", "Incubator Plus", "Egg Incubator SS",
                                          "Animal Tender", "Hitching Post", "Feeding Trough Plus",
                                          "Dino Uplink", "Noglin Nullifier"]),
    ],
}


def _matches(name: str, pattern: str) -> bool:
    return name.startswith(pattern[:-1]) if pattern.endswith("*") else name == pattern


def pretty(entry_name: str) -> str:
    """Same display-name cleanup gen_engrams.py uses, so mod names match base-game style."""
    s = re.sub(r"^EngramEntry_?", "", entry_name)
    s = re.sub(r"_C(_\d+)?$", "", s)      # dump names end "_C_0", not just "_C"
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    return re.sub(r"[_\s]+", " ", s).strip()


def mod_folder(item_class: str) -> str | None:
    """/Game/Mods/<folder>/ -> folder, else None (vanilla or unrecognised)."""
    m = re.search(r"/Game/Mods/([^/]+)/", item_class)
    return m.group(1) if m else None


def is_vanilla(item_class: str) -> bool:
    m = re.search(r"/Game/([^/]+)/", item_class)
    return bool(m) and m.group(1) in VANILLA_ROOTS


def load_index() -> dict:
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as fh:
            return json.load(fh)
    return {"mods": []}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--report", action="store_true", help="show attribution, write nothing")
    ap.add_argument("--compare", metavar="OTHER_DUMP",
                    help="diff this dump's mod content against another dump and exit "
                         "(use to prove two mods, e.g. S+ vs Super Structures, ship identical "
                         "engrams under the same /Game/Mods/<folder>/)")
    ap.add_argument("--write", action="store_true", help="write data/mods/<id>.json + index.json")
    ap.add_argument("--map", action="append", default=[], metavar="FOLDER=MODID",
                    help="map a /Game/Mods/<folder>/ name to a workshop id")
    ap.add_argument("--name", action="append", default=[], metavar="MODID=Display Name")
    ap.add_argument("--kind", action="append", default=[], metavar="MODID=building|utility|tracker")
    ap.add_argument("--variant", action="append", default=[], metavar="MODID=dump.json",
                    help="tag engrams with WHICH workshop id provides them. Use when several ids "
                         "share one /Game/Mods/<folder>/ (S+ vs Super Structures): pass one dump per "
                         "id and each engram records the variants that actually contain it, so a "
                         "slot only pools engrams its own mod ships.")
    a = ap.parse_args()

    def kv(pairs):
        out = {}
        for p in pairs:
            k, _, v = p.partition("=")
            out[k.strip()] = v.strip().strip('"')
        return out
    folder_map, names, kinds = kv(a.map), kv(a.name), kv(a.kind)
    # variant id -> set of class strings that variant's dump contains
    variant_classes: dict[str, set] = {}
    for mid, path in kv(a.variant).items():
        with open(path, encoding="utf-8") as fh:
            variant_classes[mid] = {e.get("item_class", "") for e in json.load(fh)}

    with open(a.dump, encoding="utf-8") as fh:
        dump = json.load(fh)

    if a.compare:
        with open(a.compare, encoding="utf-8") as fh:
            other = json.load(fh)

        def modmap(d):
            out = {}
            for e in d:
                cls = e.get("item_class", "")
                f = mod_folder(cls)
                if f:
                    out.setdefault(f, set()).add(cls)
            return out
        A, B = modmap(dump), modmap(other)
        na, nb = os.path.basename(a.dump), os.path.basename(a.compare)
        print(f"A = {na}")
        print(f"B = {nb}")
        print("")
        print(f"{'folder':34} {'A':>6} {'B':>6}  verdict")
        for f in sorted(set(A) | set(B)):
            sa, sb = A.get(f, set()), B.get(f, set())
            if not sa:      v = "ONLY IN B"
            elif not sb:    v = "ONLY IN A"
            elif sa == sb:  v = "IDENTICAL"
            else:           v = f"DIFFER (+{len(sb - sa)} in B, +{len(sa - sb)} in A)"
            print(f"  {f:32} {len(sa):6} {len(sb):6}  {v}")
        shared = [f for f in set(A) & set(B) if A[f] != B[f]]
        for f in shared:
            print("")
            print(f"--- {f}: sample of the difference ---")
            for c in sorted(B[f] - A[f])[:5]: print("   only in B:", c[-95:])
            for c in sorted(A[f] - B[f])[:5]: print("   only in A:", c[-95:])
        same = set(A) == set(B) and all(A[f] == B[f] for f in A)
        print("")
        print("VERDICT: " + ("same folder + same classes -> ONE catalog entry with an alias is "
                             "correct." if same else
                             "content differs -> they need separate catalog entries (or a union)."))
        return 0

    with open(BASE_ENGRAMS, encoding="utf-8") as fh:
        _base = json.load(fh)["engrams"]
        base_classes = {e["engram_class"] for e in _base}
        base_names = {e["ap_name"] for e in _base}

    # bucket every non-vanilla, non-already-shipped engram by its mod folder
    buckets: dict[str, list] = {}
    unattributed = []
    for e in dump:
        cls = e.get("item_class", "")
        if not cls or is_vanilla(cls) or cls in base_classes:
            continue
        folder = mod_folder(cls)
        if folder is None:
            unattributed.append(e)
            continue
        buckets.setdefault(folder, []).append(e)

    print(f"dump entries: {len(dump)}   non-vanilla: {sum(len(v) for v in buckets.values())}")
    print("\n=== attribution by /Game/Mods/<folder>/ ===")
    for folder, entries in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        mod_id = folder_map.get(folder, folder if folder.isdigit() else "?")
        flag = "" if mod_id != "?" else "   <-- NEEDS --map " + folder + "=<modid>"
        print(f"  {folder:34} {len(entries):4} engrams  -> mod_id {mod_id}{flag}")
        for e in entries[:3]:
            print(f"        e.g. {pretty(e['entry_name'])}")
    if unattributed:
        print(f"\n[warn] {len(unattributed)} engrams are neither vanilla nor under /Game/Mods/ - "
              f"cannot attribute. Dump incrementally (install a few mods, dump, add the rest, dump) "
              f"and diff, or inspect these paths:")
        for e in unattributed[:5]:
            print("   ", e.get("item_class"))

    if not a.write:
        print("\n(report only - re-run with --write to create catalog files)")
        return 0

    unresolved = [f for f in buckets if folder_map.get(f, f if f.isdigit() else "?") == "?"]
    if unresolved:
        print(f"\nERROR: no workshop id for: {', '.join(unresolved)}. Pass --map FOLDER=MODID.")
        return 1

    index = load_index()
    existing = {str(m["mod_id"]): m for m in index.get("mods", [])}
    os.makedirs(MODS_DIR, exist_ok=True)

    for folder, entries in sorted(buckets.items()):
        mod_id = str(folder_map.get(folder, folder))
        prev = existing.get(mod_id)
        if prev:
            id_base = prev["id_base"]                       # NEVER move a shipped id block
        else:
            used = {m["id_base"] for m in index["mods"]}
            slot = 0
            while MOD_ID_BASE + MOD_ID_STRIDE * slot in used:
                slot += 1
            id_base = MOD_ID_BASE + MOD_ID_STRIDE * slot
        # keep already-assigned engram ids stable across re-dumps (mod updates add/remove engrams)
        old_ids = {}
        path = os.path.join(MODS_DIR, f"{mod_id}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for e in json.load(fh).get("engrams", []):
                    old_ids[e["engram_class"]] = e["id"]
        # GROUP BY DISPLAY NAME. A mod can register several classes that render to the same name
        # (S+ ships 857 engrams under only 472 names). ap_name is the item-table KEY, so emitting
        # one row per class would silently overwrite - the dupes become unreachable ids. One item
        # therefore owns a LIST of classes and unlocks all of them.
        # Names that clash with a BASE-GAME engram get a mod tag: item_name_to_id is class-level, so
        # an unsuffixed clash would replace the vanilla id for EVERY player, mod active or not.
        tag = re.sub(r"[^A-Za-z0-9]", "", names.get(mod_id, prev["name"] if prev else mod_id))[:12]               or mod_id
        grouped: dict[str, list] = {}
        for e in entries:
            nm = "Engram: " + pretty(e["entry_name"])
            if nm in base_names:
                nm = f"{nm} ({tag})"
            grouped.setdefault(nm, []).append(e["item_class"])
        engrams, next_id = [], id_base
        taken = set(old_ids.values())
        for nm in sorted(grouped):
            classes = sorted(set(grouped[nm]))
            key = classes[0]
            if key in old_ids:
                eid = old_ids[key]
            else:
                while next_id in taken:
                    next_id += 1
                eid = next_id
                taken.add(eid)
            rec = {"id": eid, "ap_name": nm,
                   "engram_class": key,          # primary (back-compat)
                   "engram_classes": classes}    # ALL classes this item unlocks
            if variant_classes:
                # which workshop ids actually ship this engram (union catalogue, per-variant pooling)
                vs = sorted(v for v, cs in variant_classes.items()
                            if any(c in cs for c in classes))
                if vs:
                    rec["variants"] = vs
            req = MOD_ENGRAM_REQUIRES.get(mod_id, {}).get(nm)
            if req:
                rec["requires"] = req
            engrams.append(rec)
        # curated groups: one item unlocks every member (see MOD_GROUPS). Members stay in the
        # datapackage (their ids are real) but leave the POOL when the group is active.
        bundles, gid = [], id_base + GROUP_ID_OFFSET
        claimed: set[str] = set()
        for gname, patterns in MOD_GROUPS.get(mod_id, []):
            short = {e["ap_name"]: e["ap_name"].replace("Engram: ", "") for e in engrams}
            members = sorted(ap for ap, nm in short.items()
                             if ap not in claimed and any(_matches(nm, p) for p in patterns))
            if not members:
                print(f"  [warn] group {gname!r} matched nothing - patterns stale?")
                continue
            claimed |= set(members)
            bundles.append({"id": gid, "ap_name": gname, "members": members})
            gid += 1
        if bundles:
            saved = sum(len(b["members"]) for b in bundles) - len(bundles)
            print(f"  {len(bundles)} groups covering "
                  f"{sum(len(b['members']) for b in bundles)} engrams (pool -{saved})")
        body = {"_comment": f"Generated by tools/gen_mod.py from {os.path.basename(a.dump)}. "
                            f"engram_class must equal the in-game UClass GetFullName.",
                "mod_id": mod_id, "id_base": id_base,
                "auto_grant": MOD_AUTO_GRANT.get(mod_id, []),
                "engrams": engrams, "bundles": bundles, "dinos": []}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2)
        entry = {"mod_id": mod_id,
                 "aliases": MOD_ALIASES.get(mod_id, []),
                 "name": names.get(mod_id, prev["name"] if prev else mod_id),
                 "kind": kinds.get(mod_id, prev["kind"] if prev else "utility"),
                 "id_base": id_base, "file": f"{mod_id}.json"}
        if prev:
            index["mods"] = [entry if str(m["mod_id"]) == mod_id else m for m in index["mods"]]
        else:
            index["mods"].append(entry)
        print(f"wrote {path}  ({len(engrams)} engrams, ids {id_base}..{id_base + len(engrams) - 1})")

    with open(INDEX, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
    print(f"updated {INDEX}")
    print("\nNOTE: copy data/mods/ to apworld/ark_ase/data/mods/ if you keep a separate data dir, "
          "then rebuild the apworld.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
