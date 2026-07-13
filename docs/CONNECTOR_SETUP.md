# Connector + Archipelago setup (Server PC)

Bridges the in-game plugin (file IPC) to an Archipelago server. Everything here runs
on the **Server PC** (same machine as the dedicated server + plugin).

```
plugin (ipc/checks_out.jsonl, items_in.jsonl)  <->  connector  <->  AP server (websocket)
```

## 1. Install Archipelago
Download the latest Archipelago from https://github.com/ArchipelagoMW/Archipelago/releases
(the Windows setup, or the source). Note its folder (has `worlds/`, `Players/`,
`ArchipelagoLauncher.exe`).

## 2. Install our apworld
Two ways:
- **Dev:** copy `apworld/ark_ase` into Archipelago's `worlds/` folder, OR
- **Packaged:** use `dist/ark_ase.apworld`
  (so `__init__.py` is at the zip root) and double-click it / use the launcher's
  "Install APWorld".

Make sure `engrams.json`, `locations.json`, `dinos.json`, `crates.json` are inside
`ark_ase/data/` (they are in the repo / in `dist/ark_ase.apworld`).

## 3. Generate a game
1. Copy `apworld/ark_ase/ark.yaml` into Archipelago's `Players/` folder.
2. Run `ArchipelagoGenerate.exe` (or launcher -> Generate).
3. Output zip appears in `output/`. For a solo test this game is just ARK
   (736 items across 1249 locations: engrams, tames, crate gates + dossiers,
   bosses, milestones, artifacts).

## 4. Host it
- Launcher -> **Host**, pick the generated zip. It prints a port (default 38281).
- Local host = `localhost:38281`.

## 5. Run the connector
```
cd connector
pip install -r requirements.txt
python ark_ap_connector.py --server localhost:38281 --slot Ghios \
    --ipc-dir "E:\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins\ArkAP\ipc"
```
- `--slot` must match the `name:` in the yaml (Ghios).
- `--ipc-dir` = the plugin's `ipc` folder (it creates checks_out/items_in there).
- On connect it prints `[connector] connected as 'Ghios' (N locations remaining)`.

## 6. Test the full loop
- In-game, collect **explorer note 59** (mapped to "Explorer Note 59 (test)").
  -> plugin writes checks_out.jsonl -> connector sends LocationCheck -> AP marks it,
  releases whatever item is placed there (to you or a friend).
- When AP sends YOU an engram item -> connector writes items_in.jsonl -> plugin
  grants that engram (it becomes learnable in-game).

## Notes
- The connector is solo-test grade: no auto-reconnect yet.
- For the real multiworld with friends, generate with everyone's yamls together and
  host once; each player runs their own client/connector against that server.
