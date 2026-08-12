#!/usr/bin/env python3
"""Assemble all release artifacts into dist/ for a GitHub release.

Produces:
  dist/ark_ase.apworld                 - the Archipelago world (drop in Archipelago/custom_worlds)
  dist/ark.yaml                        - example player yaml (also bundled inside the apworld, but
                                         released standalone too so it's grabbable without unzipping)
  dist/ark_survival_evolved_ap.zip     - the PopTracker pack (drop in PopTracker/packs)
  dist/ArkAP_plugin.zip                - the server plugin: ArkAP.dll + data files + install bat
  dist/ArkServerScripts.zip            - helpers for the ARK dedicated server itself: launch/switch/
                                         transfer/reset .bat scripts + apply_server_config (applies
                                         recommended Game.ini/GameUserSettings.ini settings) - these
                                         live under tools/ in the repo, which release-only
                                         downloaders don't have

The external Python connector (connector/) is NO LONGER RELEASED - the plugin's built-in AP client
(/connect in game chat) replaced it, including the randomize_dino_spawns Game.ini patch via /confirm.
The source stays in the repo for reference/debugging; it just isn't bundled into a release artifact.

Regenerates the apworld + tracker first so everything is current. Run from the repo root:
  python tools/build_release.py
"""
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(__file__)
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DIST = os.path.join(ROOT, "dist")


def run(*args):
    print("  $", " ".join(args))
    subprocess.check_call([sys.executable, *args], cwd=ROOT)


def zip_dir(src_dir, out_zip, arc_root=""):
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fs in os.walk(src_dir):
            if "__pycache__" in dp:
                continue
            for f in fs:
                if f.endswith((".pyc", ".log")):
                    continue
                full = os.path.join(dp, f)
                rel = os.path.join(arc_root, os.path.relpath(full, src_dir))
                z.write(full, rel)


def zip_files(pairs, out_zip):
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in pairs:
            if os.path.exists(src):
                z.write(src, arc)
            else:
                print(f"  ! skip (missing): {src}")


def verify_dll(path):
    """Refuse to ship a DLL that is not a real 64-bit PE.

    A build once produced 2,097,152 bytes of pure zeros - NTFS had extended the file but the data
    never reached disk - and MSBuild still reported success. build_release.py copied the hole into
    dist/ and into the zip, and the server rejected it with 0xc000012f (STATUS_INVALID_IMAGE_NOT_MZ).
    Nothing between the compiler and the player noticed, so check it here."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 100_000:
        raise SystemExit(f"REFUSING TO SHIP: {path} is only {len(data)} bytes - build is broken")
    if data[:2] != b"MZ":
        raise SystemExit(f"REFUSING TO SHIP: {path} has no MZ header "
                         f"({'all zero bytes' if not any(data) else 'not a PE file'}) - rebuild it")
    off = int.from_bytes(data[0x3c:0x40], "little")
    if data[off:off + 4] != b"PE\0\0":
        raise SystemExit(f"REFUSING TO SHIP: {path} has no PE signature - rebuild it")
    machine = int.from_bytes(data[off + 4:off + 6], "little")
    if machine != 0x8664:
        raise SystemExit(f"REFUSING TO SHIP: {path} is machine {machine:#x}, expected x64 (0x8664)")
    print(f"  dll verified: {len(data):,} bytes, x64 PE")


def main():
    os.makedirs(DIST, exist_ok=True)

    print("[1/6] Regenerating data-derived artifacts...")
    run(os.path.join("tools", "build_apworld.py"))
    run(os.path.join("tools", "gen_poptracker.py"))

    print("[2/6] Example player yaml...")
    shutil.copyfile(os.path.join(ROOT, "apworld", "ark_ase", "ark.yaml"),
                     os.path.join(DIST, "ark.yaml"))

    print("[3/6] PopTracker pack zip...")
    zip_dir(os.path.join(ROOT, "poptracker"),
            os.path.join(DIST, "ark_survival_evolved_ap.zip"))

    print("[4/6] Server plugin bundle (DLL + data + install bat)...")
    dll = os.path.join(ROOT, "plugin", "ArkAP", "x64", "Release", "ArkAP.dll")
    verify_dll(dll)
    data = os.path.join(ROOT, "data")
    # the config template ships AS ArkAP.config.json (the name the plugin actually reads -
    # shipping it as *.default.json confused people). install_plugin.bat preserves an existing
    # ArkAP.config.json on upgrade, so live settings are never clobbered.
    pairs = [(dll, "ArkAP/ArkAP.dll"),
             (os.path.join(ROOT, "plugin", "ArkAP", "ArkAP.config.default.json"),
              "ArkAP/ArkAP.config.json"),
             (os.path.join(ROOT, "tools", "install_plugin.bat"), "install_plugin.bat")]
    # maps.json is REQUIRED for map filtering: without it the plugin cannot tell which locations
    # belong to the map it is running, and an Island exploration region fires on Scorched Earth
    # (the polygons are raw world coordinates, so the same X/Y matches on any map).
    for name in ("engrams.json", "dinos.json", "locations.json", "crates.json", "filler.json",
                 "explore_areas.json", "maps.json"):
        pairs.append((os.path.join(data, name), f"ArkAP/{name}"))
    # mod catalog: the plugin loads data/mods/index.json + each listed <modid>.json so it can grant
    # and gate MOD engrams. Without these the apworld would hand out mod items the server ignores.
    mods_dir = os.path.join(data, "mods")
    if os.path.isdir(mods_dir):
        for name in sorted(os.listdir(mods_dir)):
            if name.endswith(".json"):
                pairs.append((os.path.join(mods_dir, name), f"ArkAP/mods/{name}"))
    zip_files(pairs, os.path.join(DIST, "ArkAP_plugin.zip"))
    # also refresh the LOOSE dll. It is what people drop in for a quick upgrade, and it
    # used to be updated by hand - so it silently sat a build behind the zip.
    if os.path.exists(dll):
        shutil.copyfile(dll, os.path.join(DIST, "ArkAP.dll"))

    print("[5/6] ARK server scripts bundle...")
    tools = os.path.join(ROOT, "tools")
    spairs = [(os.path.join(tools, n), n) for n in (
        "start_ase_server.bat", "switch_map.bat", "start_transfer_server.bat",
        "reset_ark_test.bat", "apply_server_config.bat", "apply_server_config.ps1",
    )]
    for n in ("Game.ini.settings", "GameUserSettings.ini.settings"):
        spairs.append((os.path.join(tools, "serverconfig", n), f"serverconfig/{n}"))
    zip_files(spairs, os.path.join(DIST, "ArkServerScripts.zip"))

    print("[6/6] apworld already in dist/ from step 1.")
    print("\nRelease artifacts in dist/:")
    for f in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, f)
        if os.path.isfile(p):
            print(f"  {f:34} {os.path.getsize(p) // 1024:>6} KB")


if __name__ == "__main__":
    main()
