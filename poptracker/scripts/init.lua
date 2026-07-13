-- Pack entry point: PopTracker runs this on load. Registers items + layout, then wires
-- Archipelago auto-tracking.
Tracker:AddItems("items/items.json")
Tracker:AddLayouts("layouts/tracker.json")

-- load the generated AP id->code map (sets global AP_MAP), then the autotracking handlers.
ScriptHost:LoadScript("scripts/ap_map.lua")
ScriptHost:LoadScript("scripts/autotracking.lua")
