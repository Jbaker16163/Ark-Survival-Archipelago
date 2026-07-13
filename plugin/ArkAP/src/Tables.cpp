// Tables::Load - parse the shared data/*.json into lookup maps.
#include "ArkAP.hpp"

namespace ArkAP {

bool Tables::Load(const fs::path& engrams_json, const fs::path& locations_json) {
    try {
        json e; std::ifstream(engrams_json) >> e;
        for (auto& g : e["engrams"]) {
            int id = g["id"]; std::string cls = g["engram_class"];
            item_name[id] = g["ap_name"];
            engram_class_to_item[cls] = id;
            item_to_engram_class[id] = cls;
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
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

} // namespace ArkAP
