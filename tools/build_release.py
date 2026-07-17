#!/usr/bin/env python3
"""Assemble all release artifacts into dist/ for a GitHub release.

Produces:
  dist/ark_ase.apworld                 - the Archipelago world (drop in Archipelago/custom_worlds)
  dist/ark.yaml                        - example player yaml (also bundled inside the apworld, but
                                         released standalone too so it's grabbable without unzipping)
  dist/ark_survival_evolved_ap.zip     - the PopTracker pack (drop in PopTracker/packs)
  dist/ArkAP_plugin.zip                - the server plugin: ArkAP.dll + data files + install bat
  dist/ArkConnector.zip                - the Python connector + connector.ini + run bat
                                         (if connector/dist/ArkConnector.exe exists, it's included)
  dist/ArkServerScripts.zip            - helpers for the ARK dedicated server itself: launch/switch/
                                         transfer/reset .bat scripts + apply_server_config (applies
                                         recommended Game.ini/GameUserSettings.ini settings) - these
                                         live under tools/ in the repo, which release-only
                                         downloaders don't have

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


def main():
    os.makedirs(DIST, exist_ok=True)

    print("[1/7] Regenerating data-derived artifacts...")
    run(os.path.join("tools", "build_apworld.py"))
    run(os.path.join("tools", "gen_poptracker.py"))

    print("[2/7] Example player yaml...")
    shutil.copyfile(os.path.join(ROOT, "apworld", "ark_ase", "ark.yaml"),
                     os.path.join(DIST, "ark.yaml"))

    print("[3/7] PopTracker pack zip...")
    zip_dir(os.path.join(ROOT, "poptracker"),
            os.path.join(DIST, "ark_survival_evolved_ap.zip"))

    print("[4/7] Server plugin bundle (DLL + data + install bat)...")
    dll = os.path.join(ROOT, "plugin", "ArkAP", "x64", "Release", "ArkAP.dll")
    data = os.path.join(ROOT, "data")
    # the config template ships AS ArkAP.config.json (the name the plugin actually reads -
    # shipping it as *.default.json confused people). install_plugin.bat preserves an existing
    # ArkAP.config.json on upgrade, so live settings are never clobbered.
    pairs = [(dll, "ArkAP/ArkAP.dll"),
             (os.path.join(ROOT, "plugin", "ArkAP", "ArkAP.config.default.json"),
              "ArkAP/ArkAP.config.json"),
             (os.path.join(ROOT, "tools", "install_plugin.bat"), "install_plugin.bat")]
    for name in ("engrams.json", "dinos.json", "locations.json", "crates.json", "filler.json"):
        pairs.append((os.path.join(data, name), f"ArkAP/{name}"))
    zip_files(pairs, os.path.join(DIST, "ArkAP_plugin.zip"))

    print("[5/7] Connector bundle...")
    conn = os.path.join(ROOT, "connector")
    cpairs = [(os.path.join(conn, "ark_ap_connector.py"), "ark_ap_connector.py"),
              (os.path.join(conn, "connector.ini"), "connector.ini"),
              (os.path.join(conn, "run_connector.bat"), "run_connector.bat")]
    exe = os.path.join(conn, "dist", "ArkConnector.exe")
    if os.path.exists(exe):
        cpairs.append((exe, "ArkConnector.exe"))
    else:
        print("  (no ArkConnector.exe - run connector/build_exe.bat to include it)")
    zip_files(cpairs, os.path.join(DIST, "ArkConnector.zip"))

    print("[6/7] ARK server scripts bundle...")
    tools = os.path.join(ROOT, "tools")
    spairs = [(os.path.join(tools, n), n) for n in (
        "start_ase_server.bat", "switch_map.bat", "start_transfer_server.bat",
        "reset_ark_test.bat", "apply_server_config.bat", "apply_server_config.ps1",
    )]
    for n in ("Game.ini.settings", "GameUserSettings.ini.settings"):
        spairs.append((os.path.join(tools, "serverconfig", n), f"serverconfig/{n}"))
    zip_files(spairs, os.path.join(DIST, "ArkServerScripts.zip"))

    print("[7/7] apworld already in dist/ from step 1.")
    print("\nRelease artifacts in dist/:")
    for f in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, f)
        if os.path.isfile(p):
            print(f"  {f:34} {os.path.getsize(p) // 1024:>6} KB")


if __name__ == "__main__":
    main()
