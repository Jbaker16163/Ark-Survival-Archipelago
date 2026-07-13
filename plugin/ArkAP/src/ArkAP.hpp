// ArkAP.hpp - shared types, config, state, file IPC.
// Header-only for the non-ARK logic so it can be unit-tested off the server.
#pragma once

#include <cstdint>
#include <string>
#include <set>
#include <map>
#include <vector>
#include <fstream>
#include <mutex>
#include <random>
#include <filesystem>

#include "json.hpp"  // nlohmann::json (ships with ArkServerApi)

namespace ArkAP {

namespace fs = std::filesystem;
using json = nlohmann::json;

enum class Mode { AP, Offline };

// ------------------------------------------------------------------ Tables
// Static data loaded from data/engrams.json + data/locations.json.
// These IDs must match the apworld exactly.
struct Tables {
    // item_id -> human name; and the maps needed to apply/gate effects
    std::map<int, std::string>  item_name;          // any item id -> name
    std::map<std::string, int>  engram_class_to_item; // "EngramEntry_Bow_C" -> item id
    std::map<int, std::string>  item_to_engram_class; // reverse
    int taming_item = 0;
    int supply_item = 0;
    std::map<int, std::string>  boss_access;         // item id -> boss tag
    std::map<int, std::string>  map_access;          // item id -> map tag

    // location tag -> loc id (per category), plus a flat set of all loc ids
    std::map<int, int>          note_index_to_loc;   // ExplorerNoteIndex -> loc id (dossiers)
    std::map<std::string, int>  boss_tag_to_loc;
    std::map<std::string, int>  milestone_tag_to_loc;
    std::map<int, int>          level_to_loc;        // player level -> "Reach Level N" loc id
    std::vector<int>            all_locations;

    bool Load(const fs::path& engrams_json, const fs::path& locations_json);
};

// ------------------------------------------------------------------ Ipc
// Append-only JSONL mailbox shared with the Python connector.
// Multiplayer: each AP slot gets its own subfolder (ipc/<CharacterName>) served by its own
// connector instance; route "" = the root ipc folder (solo / shared).
class Ipc {
public:
    explicit Ipc(const fs::path& ipc_dir) : dir_(ipc_dir) {
        fs::create_directories(dir_);
        checks_out_ = dir_ / "checks_out.jsonl";
        items_in_   = dir_ / "items_in.jsonl";
    }

    // the mailbox folder for a route ("" = root). Created on demand.
    fs::path DirFor(const std::string& route) const {
        if (route.empty()) return dir_;
        fs::path d = dir_ / route;
        std::error_code ec; fs::create_directories(d, ec);
        return d;
    }
    const fs::path& Root() const { return dir_; }

    // diagnostics: the exact items_in path + whether it exists + its size
    std::string DebugInfo() const {
        std::error_code ec;
        bool ex = fs::exists(items_in_, ec);
        auto sz = ex ? fs::file_size(items_in_, ec) : 0ull;
        return items_in_.string() + " exists=" + (ex ? "1" : "0") +
               " size=" + std::to_string((unsigned long long)sz) +
               " pos=" + std::to_string((long long)items_pos_);
    }

    // plugin -> connector: a location was checked (game thread only - no mutex)
    void ReportCheck(const std::string& route, int loc_id) {
        std::ofstream f(DirFor(route) / "checks_out.jsonl", std::ios::app);
        if (f) f << "{\"loc_id\": " << loc_id << "}\n";
    }

    struct InItem { int id; std::string from; };

    // connector -> plugin: read newly appended received items (by file offset).
    // Lines look like {"item_id": 8730001, "from": "PlayerName"} ("from" optional).
    std::vector<InItem> PollItems() {              // unused (plugin reads items_in directly); no mutex
        std::vector<InItem> out;
        std::ifstream f(items_in_);
        if (!f) return out;
        f.seekg(0, std::ios::end);
        std::streamoff size = f.tellg();
        if (size < items_pos_) items_pos_ = 0;   // file deleted/recreated -> re-read from start
        f.clear();
        f.seekg(items_pos_);
        std::string line;
        while (std::getline(f, line)) {
            auto p = line.find("\"item_id\"");
            if (p == std::string::npos) continue;
            InItem it{};
            try { it.id = std::stoi(line.substr(line.find(':', p) + 1)); }
            catch (...) { continue; }
            auto fp = line.find("\"from\"");
            if (fp != std::string::npos) {
                auto q1 = line.find('"', line.find(':', fp) + 1);
                auto q2 = (q1 == std::string::npos) ? std::string::npos : line.find('"', q1 + 1);
                if (q2 != std::string::npos) it.from = line.substr(q1 + 1, q2 - q1 - 1);
            }
            out.push_back(it);
        }
        items_pos_ = f.tellg() < 0 ? items_pos_ : static_cast<std::streamoff>(f.tellg());
        return out;
    }

private:
    fs::path dir_, checks_out_, items_in_;
    std::streamoff items_pos_ = 0;
};

// ------------------------------------------------------------------ State
// Persisted progress + optional offline seed. PER-PLAYER: every set is keyed by a "route"
// (the survivor character name in multiplayer, "" in solo/shared). HasItem falls back to the
// "" shared bucket, which doubles as the legacy-format migration target AND the home of
// global unlocks (tek grants, crate access when shared).
class State {
public:
    State(const fs::path& dir, Mode mode) : dir_(dir), mode_(mode) {
        state_path_ = dir_ / "state.json";
        seed_path_  = dir_ / "seed.json";
    }

    void Load() {  // called once at startup (single-threaded) - no lock (avoids DllMain loader-lock issues)
        if (!fs::exists(state_path_)) return;
        try {                          // tolerate a corrupt/half-written state file
            json j; std::ifstream(state_path_) >> j;
            // legacy flat format -> the "" shared bucket
            for (int v : j.value("checked", json::array()))  checked_[""].insert(v);
            for (int v : j.value("received", json::array())) received_[""].insert(v);
            for (auto& [name, p] : j.value("players", json::object()).items()) {
                for (int v : p.value("checked", json::array()))  checked_[name].insert(v);
                for (int v : p.value("received", json::array())) received_[name].insert(v);
            }
        } catch (...) { checked_.clear(); received_.clear(); }
    }

    void Save() const {
        json players = json::object();
        std::set<std::string> names;
        for (auto& [n, _] : checked_)  names.insert(n);
        for (auto& [n, _] : received_) names.insert(n);
        for (auto& n : names) {
            json p;
            auto ci = checked_.find(n);
            auto ri = received_.find(n);
            p["checked"]  = ci != checked_.end()  ? std::vector<int>(ci->second.begin(), ci->second.end())  : std::vector<int>{};
            p["received"] = ri != received_.end() ? std::vector<int>(ri->second.begin(), ri->second.end()) : std::vector<int>{};
            players[n] = p;
        }
        json j; j["players"] = players;
        std::ofstream(state_path_) << j.dump(2);
    }

    // No internal mutex (it caused faults). Hooks no longer touch State on the network
    // thread (they queue to the game thread); the only network-thread reader is the gate's
    // HasItem, a brief read - acceptable.
    bool HasItem(const std::string& p, int item_id) const {
        auto it = received_.find(p);
        if (it != received_.end() && it->second.count(item_id)) return true;
        if (!p.empty()) {                       // shared bucket = global unlocks + legacy state
            auto sh = received_.find("");
            if (sh != received_.end() && sh->second.count(item_id)) return true;
        }
        return false;
    }
    bool HasItemAny(int item_id) const {        // crate gate: unlocked if ANY player has it
        for (auto& [_, s] : received_) if (s.count(item_id)) return true;
        return false;
    }
    bool AddItem(const std::string& p, int item_id) {
        bool n = received_[p].insert(item_id).second; if (n) Save(); return n;
    }
    bool AlreadyChecked(const std::string& p, int loc_id) const {
        auto it = checked_.find(p);
        return it != checked_.end() && it->second.count(loc_id) > 0;
    }
    bool MarkChecked(const std::string& p, int loc_id) {
        bool n = checked_[p].insert(loc_id).second; if (n) Save(); return n;
    }
    std::vector<std::string> Players() const {  // every route ever seen (incl. "")
        std::set<std::string> names;
        for (auto& [n, _] : checked_)  names.insert(n);
        for (auto& [n, _] : received_) names.insert(n);
        return { names.begin(), names.end() };
    }

    // Offline mode: first run, roll a local placement location->item and persist.
    // Returns the item granted for a freshly-checked location, or 0. (Solo route "".)
    int OfflineGrantFor(int loc_id, const Tables& t, uint64_t seed = 0) {
        if (mode_ != Mode::Offline) return 0;
        EnsureSeed(t, seed);
        auto it = placement_.find(loc_id);
        return it == placement_.end() ? 0 : it->second;
    }

private:
    void EnsureSeed(const Tables& t, uint64_t seed) {
        if (!placement_.empty()) return;
        if (fs::exists(seed_path_)) {
            json j; std::ifstream(seed_path_) >> j;
            for (auto& [k, v] : j.items()) placement_[std::stoi(k)] = v.get<int>();
            return;
        }
        // build the item pool: every engram item + specials + world items
        std::vector<int> items;
        for (auto& [iid, _] : t.item_name) items.push_back(iid);
        std::vector<int> locs = t.all_locations;
        std::mt19937_64 rng(seed ? seed : std::random_device{}());
        std::shuffle(items.begin(), items.end(), rng);
        for (size_t i = 0; i < locs.size(); ++i)
            placement_[locs[i]] = items[i % items.size()];
        json j; for (auto& [l, it] : placement_) j[std::to_string(l)] = it;
        std::ofstream(seed_path_) << j.dump(2);
    }

    fs::path dir_, state_path_, seed_path_;
    Mode mode_;
    std::map<std::string, std::set<int>> checked_, received_;   // route -> ids
    std::map<int, int> placement_;  // offline only
};

} // namespace ArkAP
