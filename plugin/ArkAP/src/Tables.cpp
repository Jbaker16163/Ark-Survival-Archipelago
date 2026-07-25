// Tables::Load - parse the shared data/*.json into lookup maps.
#include "ArkAP.hpp"

namespace ArkAP {

bool Tables::Load(const fs::path& engrams_json, const fs::path& locations_json,
                  const fs::path& mods_dir) {
    try {
        json e; std::ifstream(engrams_json) >> e;
        for (auto& g : e["engrams"]) {
            int id = g["id"]; std::string cls = g["engram_class"];
            item_name[id] = g["ap_name"];
            engram_class_to_item[cls] = id;
            item_to_engram_class[id] = cls;
            item_to_engram_classes[id] = { cls };
        }
        for (auto& s : e.value("special_items", json::array())) {
            int id = s["id"]; std::string kind = s["kind"];
            item_name[id] = s["ap_name"];
            if (kind == "taming")       taming_item = id;
            else if (kind == "supply_crate") supply_item = id;
        }

        json l; std::ifstream(locations_json) >> l;
        auto& cats = l["location_categories"];
        for (auto& d : cats["dossiers"]["entries"]) {
            int id = d["id"]; note_index_to_loc[d["note_index"]] = id; all_locations.push_back(id);
        }
        for (auto& b : cats["bosses"]["entries"]) {
            int id = b["id"]; boss_tag_to_loc[b["tag"]] = id; all_locations.push_back(id);
        }
        for (auto& m : cats["milestones"]["entries"]) {
            int id = m["id"]; milestone_tag_to_loc[m["tag"]] = id; all_locations.push_back(id);
        }
        if (cats.contains("levels")) for (auto& lv : cats["levels"]["entries"]) {
            int id = lv["id"]; level_to_loc[lv["level"]] = id; all_locations.push_back(id);
        }
        for (auto& w : l["world_items"]["entries"]) {
            int id = w["id"]; std::string kind = w["kind"];
            item_name[id] = w["name"];
            if (kind == "boss_access") boss_access[id] = w["tag"];
            else if (kind == "map_access") map_access[id] = w["tag"];
        }

        // ---- MOD catalog (optional) ------------------------------------------------------
        // Index-driven, mirroring the apworld: data/mods/index.json lists every supported mod.
        // We load ALL of them - the plugin has no yaml, and gating an engram the player's slot
        // never enabled is harmless (that class simply never gets granted). Failures here must
        // NOT break base-game loading, so each file is guarded independently.
        if (!mods_dir.empty()) {
            std::error_code ec;
            fs::path idx = mods_dir / "index.json";
            if (fs::exists(idx, ec)) {
                json mi;
                try { std::ifstream(idx) >> mi; } catch (const std::exception&) { mi = json::object(); }
                for (auto& entry : mi.value("mods", json::array())) {
                    std::string file = entry.value("file", "");
                    if (file.empty()) continue;
                    fs::path mp = mods_dir / file;
                    if (!fs::exists(mp, ec)) continue;
                    try {
                        json m; std::ifstream(mp) >> m;
                        // name-grouped engrams: one item id may own SEVERAL blueprint classes
                        for (auto& g : m.value("engrams", json::array())) {
                            int id = g["id"];
                            item_name[id] = g["ap_name"];
                            std::vector<std::string> classes;
                            if (g.contains("engram_classes"))
                                for (auto& c : g["engram_classes"]) classes.push_back(c.get<std::string>());
                            else if (g.contains("engram_class"))
                                classes.push_back(g["engram_class"].get<std::string>());
                            for (const auto& c : classes) engram_class_to_item[c] = id;
                            if (!classes.empty()) item_to_engram_class[id] = classes.front();
                            item_to_engram_classes[id] = classes;
                            item_to_mod[id] = entry.value("mod_id", "");
                        }
                        // curated group item -> member item ids (granting it grants them all)
                        for (auto& b : m.value("bundles", json::array())) {
                            int bid = b["id"];
                            item_name[bid] = b["ap_name"];
                            std::vector<int> members;
                            for (auto& mem : b.value("members", json::array())) {
                                // members are ap_names; resolve to ids via this mod's engrams
                                std::string nm = mem.get<std::string>();
                                for (auto& g : m.value("engrams", json::array()))
                                    if (g["ap_name"] == nm) { members.push_back(g["id"]); break; }
                            }
                            mod_bundles[bid] = members;
                            item_to_mod[bid] = entry.value("mod_id", "");
                        }
                    } catch (const std::exception&) { /* skip a broken mod file, keep the rest */ }
                }
            }
        }
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

} // namespace ArkAP
