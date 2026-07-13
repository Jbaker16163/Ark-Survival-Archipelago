-- ARK: Survival Evolved (Archipelago) auto-tracking.
-- Marks check tiles as you complete locations, station tiles as you receive them, and counts
-- explorer notes. The AP location/item id -> tile-code map is generated in ap_map.lua.

local AP = AP_MAP   -- global set by scripts/ap_map.lua (loaded first in init.lua)

local function set_toggle(code, on)
    local o = Tracker:FindObjectForCode(code)
    if o then o.Active = on end
end

-- called on (re)connect + on a full refresh; reset everything, then AP replays items/locations.
function onClear(slot_data)
    for _, code in pairs(AP.loc_to_code) do set_toggle(code, false) end
    for _, code in pairs(AP.item_to_code) do set_toggle(code, false) end
end

-- a location was checked: tick its tile (bosses/tames/kills/levels/dossiers all live in loc_to_code).
function onLocation(location_id, location_name)
    local code = AP.loc_to_code[location_id]
    if code then set_toggle(code, true) end
end

-- an item was received: light the station tile if it's one of the 3 gate engrams.
function onItem(index, item_id, item_name, player_number)
    local code = AP.item_to_code[item_id]
    if code then set_toggle(code, true) end
end

if Archipelago then                       -- present when the variant has the "ap" flag
    Archipelago:AddClearHandler("clear", onClear)
    Archipelago:AddItemHandler("item", onItem)
    Archipelago:AddLocationHandler("location", onLocation)
else
    print("ARK pack: no Archipelago back-end (variant missing the 'ap' flag?)")
end
