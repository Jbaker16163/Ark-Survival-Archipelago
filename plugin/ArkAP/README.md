# ArkAP — ArkServerApi plugin

Server-side ARK: Survival Evolved plugin (ArkServerApi, Pre-Aquatica) that bridges
the game to Archipelago: gates engrams / taming / supply crates / world / bosses,
and reports checks (dossiers, bosses, milestones).

> Status: **skeleton**. IPC / state / config logic is real. The ARK hook
> registrations are stubs — exact preaquatica function names are TODO (read them
> from the ArkServerApi SDK once installed).

## Runs where
Server PC, inside the dedicated server process via ArkServerApi:
`...\ArkSEServer\ShooterGame\Binaries\Win64\ArkApi\Plugins\ArkAP\ArkAP.dll`

The Python **connector** runs on the SAME PC (reads/writes the IPC files), and it
talks the Archipelago websocket protocol to the AP server.

## Data flow
```
game event (learn engram / tame / open crate / pickup dossier / kill boss)
  -> hook handler (Hooks.cpp)
       - GATE: allow only if AP granted the matching item (State.received)
       - REPORT: append location id to ipc/checks_out.jsonl
connector -> AP server (LocationChecks)
AP server -> connector -> ipc/items_in.jsonl
  -> plugin polls, updates State.received, unlocks apply
```

## Two modes (set in ArkAP.config.json)
- **ap** (default): randomization lives in the Archipelago generator. Plugin just
  reports locations and applies received items. Use for the real multiworld.
- **offline**: NO AP server. On first start the plugin rolls a LOCAL seed
  (`seed.json`) mapping each location -> an item, saves it, and self-grants on
  check. For solo testing the hooks before the multiworld exists.

## Static data (must match the apworld)
Loaded from the repo `data/` files (copied next to the dll at deploy):
- `engrams.json`    — engram unlock items + special items
- `locations.json`  — dossiers / bosses / milestones + world/boss-access items

## Persisted state (Server PC, next to dll)
- `state.json` — checked location ids + received item ids (survives restart)
- `seed.json`  — offline-mode local placement (offline only)
- `ipc/`       — `checks_out.jsonl`, `items_in.jsonl` (shared with connector)

## Build (TODO once ArkServerApi SDK present)
1. Install ArkServerApi on the Server PC; note its SDK include path.
2. Open in Visual Studio (v143 toolset), set include/lib to the ArkServerApi SDK.
3. Build x64 Release -> `ArkAP.dll` -> drop in `ArkApi\Plugins\ArkAP\`.
See `CMakeLists.txt` for the include points to wire up.

## TODO (need preaquatica SDK)
- Real hook targets for: LearnEngram, Tame-complete, SupplyCrate-open, Dossier-acquired, Boss-defeated.
- Map our `tag` values in data/*.json to real ARK classes/blueprint paths.
- Engram apply: call the engram-grant function; gating: cancel the learn if not granted.
