# Setup — ARK: Survival Evolved + Archipelago

Overview of the whole stack. ARK is solo; friends play other games in the same multiworld.

> History: this started on ARK: Survival **Ascended** with UE4SS — both were dropped
> (UE4SS CDO hang / no AOBs). Current stack is ASE Pre-Aquatica + an ArkServerApi C++ plugin.

---

## Pieces
- **ARK: Survival Evolved**, pinned to the **Pre-Aquatica** beta (`-beta preaquatica`, v358.24) —
  the mod-compatible branch. Dedicated server on a Server PC; you join over LAN.
- **ArkServerApi** + our plugin **`plugin/ArkAP`** (`ArkAP.dll`) inside the dedicated server.
  Gates engrams / taming / supply crates and reports dossier + artifact checks.
- **apworld `ark_ase`** — the AP world definition (items/locations). Packaged as
  `dist/ark_ase.apworld`.
- **connector** (`connector/ark_ap_connector.py`) — bridges the plugin's file IPC to the AP
  server. Runs on the Server PC.

## Topology
```
Server PC:  ShooterGameServer.exe + ArkApi + ArkAP.dll  <-(ipc files)->  connector  <-(ws)->  AP server
Your PC:    ARK game client  ---- LAN ---->  dedicated server
Friends:    their game + their AP client  ---- internet ---->  AP server
```

## Deploy loop (plugin)
1. Build `plugin/ArkAP` in Visual Studio → Release / x64 (v145).
2. Copy `x64\Release\ArkAP.dll` to
   `...\ArkApi\Plugins\ArkAP\`. Confirm `ArkAP_loaded.txt` shows the expected build marker.
3. Copy the data files (`data\engrams.json`, `locations.json`, `dinos.json`, `crates.json`)
   next to the dll.
4. Restart the dedicated server. Check `ArkAP_debug.log` `LOAD` line for the registry counts.

## Networking (solved)
Join the LAN server via **LAN IP + query port** in Steam (`192.168.50.104:27015`), NOT the
public IP or game port 7777 (router hairpin). Launch the client with `-NoBattlEye`.
Server preaquatica 358.24 + client preaquatica — Wildcard's intended pairing.

---

## Where to go next
- Bridge + solo test: **`docs/CONNECTOR_SETUP.md`**
- Real game with friends: **`docs/MULTIWORLD.md`**
- Regenerate data files: `tools/gen_engrams.py`, `gen_locations.py`, `gen_dinos.py`, `gen_crates.py`
