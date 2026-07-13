# Running a real multiworld (ARK + friends)

Everything in code is ready: the apworld generates **736 items across 1249 locations**
(engrams, tames, beacons/crates, dossiers, artifacts), and the connector bridges the
in-game plugin to an AP server. This is the process to play a real game with friends.

```
                generate (one person)                       play
  yamls  ->  ArchipelagoGenerate  ->  AP_xxxx.zip  ->  host  ->  everyone connects
  (ARK + each friend's game)                                     friends: their AP client
                                                                 ARK: the connector (Server PC)
```

---

## Roles
- **One "generator"** (can be you, on your main PC): collects everyone's yaml, runs Generate, hosts.
- **Each friend**: supplies a yaml for *their* game, then connects their game's AP client.
- **You (ARK)**: supply the ARK yaml, run the dedicated server + plugin + connector.

---

## 1. Generator setup (one machine)
1. Install Archipelago: https://github.com/ArchipelagoMW/Archipelago/releases (Windows setup).
   It has `custom_worlds/`, `Players/`, `ArchipelagoLauncher.exe`.
2. Install **our apworld**: copy `dist/ark_ase.apworld` into Archipelago's `custom_worlds/`
   (or Launcher → "Install APWorld").
3. Install **each friend's apworld** too, if their game isn't built-in.

## 2. Collect yamls
- Put `apworld/ark_ase/ark.yaml` (rename per player) in `Players/`. Set `name:` to
  your ARK slot (e.g. `Ghios`).
- Each friend drops their game's yaml in `Players/` with a unique `name:`.
- One yaml per player. Names must be unique.

## 3. Generate
- Launcher → **Generate** (or run `ArchipelagoGenerate.exe`).
- Output `AP_xxxxx.zip` lands in `output/`. If it errors, the message names the bad yaml.

## 4. Host
Pick one:
- **archipelago.gg (easiest, works over the internet):** go to https://archipelago.gg/uploads,
  upload the `AP_xxxxx.zip`, "Host Game" → it gives a room URL + `host:port`
  (e.g. `archipelago.gg:38281`). Share that with friends.
- **Self-host:** Launcher → **Host**, pick the zip. It serves on `:38281`. Friends need your
  public IP + a forwarded port (38281). Simpler to use archipelago.gg.

## 5. Everyone connects
- **Friends:** open their game's AP client, point it at `host:port` with their slot `name`.
- **You (ARK):** on the **Server PC**, run the connector against the same room:
  ```
  cd connector
  pip install -r requirements.txt
  python ark_ap_connector.py --server <host:port> --slot Ghios \
      --ipc-dir "E:\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins\ArkAP\ipc"
  ```
  `--slot` must equal the ARK yaml `name:`. On connect it prints
  `connected as 'Ghios' (N locations remaining)`.
- Then launch ARK normally and join your dedicated server (LAN IP + query port `:27015`).

---

## Start each game CLEAN
A generated game is a fresh seed — clear old progress first, or stale unlocks/checks leak in:

On the **Server PC**, before hosting a new seed:
- Delete the plugin's `ipc\checks_out.jsonl`, `ipc\items_in.jsonl`, `state.json`, `seed.json`.
- Delete `note_queue.jsonl`, `dino_queue.jsonl`, `crate_queue.jsonl` (harvest leftovers).
- Reset the world save so notes/artifacts re-lock:
  `ShooterGame\Saved\SavedArks\TheIsland.ark` (back it up first).
- `tools\reset_ark_test.bat` does most of this — check its paths.

The connector keeps no state (a restart re-delivers everything AP resends; the plugin dedups),
so you only need to clear the plugin + world.

---

## What's gated this game
- **Items AP can give you:** 614 engrams, 100 tames (`Tame: X`), 20 crate gates
  (6 beacons + cave/deep-sea), 2 legacy specials = 736 real items.
- **Locations you check (release items to anyone):** explorer notes/dossiers (count set by
  `dossier_checks`, default 730 = near-zero filler), 4 bosses, 2 milestones.
- **Goal (`goal:` in yaml):** defeat the bosses — `broodmother`, `broodmother_megapithecus`,
  `broodmother_megapithecus_dragon`, or `all_bosses` (default). Any boss difficulty counts.
- **DeathLink:** on by default for ARK; friends enable `death_link` in their yaml to link.

---

## Sanity check before inviting friends
Do a solo generate+host with just the ARK yaml first (see `docs/CONNECTOR_SETUP.md`) and
confirm: `/send Ghios Engram: Spear` unlocks in-game, and collecting a dossier registers a
check. If that round-trips, the multiworld will too.
