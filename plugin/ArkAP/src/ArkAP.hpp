// ArkAP.hpp - shared types, config, state, file IPC.
// Header-only for the non-ARK logic so it can be unit-tested off the server.
#pragma once

#include <cstdint>
#include <string>
#include <set>
#include <map>
#include <vector>
#include <fstream>
#include <sstream>
#include <ctime>
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
    std::map<int, std::string>  item_to_engram_class; // reverse (PRIMARY class only)
    // A MOD item can own several blueprint classes that share one display name (the apworld groups
    // them, since ap_name is the item-table key). Base-game items have exactly one entry here.
    std::map<int, std::vector<std::string>> item_to_engram_classes;
    // Curated per-mod group item -> the member ITEM ids it unlocks (apworld mod "bundles").
    std::map<int, std::vector<int>> mod_bundles;
    // item id -> owning mod id ("" = base game). Lets a bundle skip engrams from a mod the
    // player's slot never enabled (the plugin loads the whole catalogue; slots pick a subset).
    std::map<int, std::string> item_to_mod;
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

    // mods_dir = data/mods (index.json + <modid>.json). Optional: missing = no mod support.
    bool Load(const fs::path& engrams_json, const fs::path& locations_json,
              const fs::path& mods_dir = {});
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

    // Parse one state file into the live sets. Returns false (and leaves nothing behind) if the
    // file cannot be read.
    //
    // TOLERANT ON PURPOSE. A state.json that looks perfectly valid when you open it can still fail
    // a strict parse, because the damage is invisible in a text editor:
    //   * a UTF-8 BOM (EF BB BF) if anything ever re-saved the file - Notepad, PowerShell's
    //     Out-File - which nlohmann rejects as an unexpected token at position 1;
    //   * trailing NUL bytes, which is what NTFS leaves when a file was extended but the data
    //     never reached disk before a hard stop. That is exactly what a killed server produces,
    //     and it is the most likely cause of the original "tames lost on restart" reports.
    // Both are stripped here, so a file whose JSON is intact loads instead of being written off.
    // `why` receives a short diagnosis for the log, since "CORRUPT" on its own told us nothing.
    bool LoadFrom(const fs::path& p, std::string* why = nullptr) {
        std::error_code ec;
        if (!fs::exists(p, ec)) { if (why) *why = "missing"; return false; }
        std::string raw;
        {   std::ifstream f(p, std::ios::binary);
            if (!f) { if (why) *why = "could not be opened (locked by another process?)"; return false; }
            std::stringstream ss; ss << f.rdbuf(); raw = ss.str();
        }
        const size_t rawLen = raw.size();
        if (raw.size() >= 3 && (unsigned char)raw[0] == 0xEF && (unsigned char)raw[1] == 0xBB &&
            (unsigned char)raw[2] == 0xBF)
            raw.erase(0, 3);                                  // UTF-8 BOM
        while (!raw.empty() && (raw.back() == '\0' || raw.back() == '\n' ||
                                raw.back() == '\r' || raw.back() == ' ' || raw.back() == '\t'))
            raw.pop_back();                                   // NUL padding / stray whitespace
        if (raw.empty()) {
            if (why) *why = "empty (" + std::to_string(rawLen) + " bytes on disk, all padding)";
            return false;
        }
        try {
            json j = json::parse(raw);
            std::map<std::string, std::set<int>> c, r;
            // Walk DEFENSIVELY. nlohmann's .value() throws type_error.306 the moment it is called
            // on a null node, and a single null anywhere - "players": null, or one route mapped to
            // null - then condemns the whole file as corrupt. That is what was happening: the JSON
            // parsed perfectly and the load still failed. Check the type at every step and ignore
            // anything that is not the shape we expect.
            auto ints = [](const json& parent, const char* key, std::set<int>& out) {
                if (!parent.is_object()) return;
                auto it = parent.find(key);
                if (it == parent.end() || !it->is_array()) return;
                for (auto& v : *it)
                    if (v.is_number_integer()) out.insert(v.get<int>());
            };
            if (j.is_object()) {
                ints(j, "checked", c[""]);              // legacy flat form
                ints(j, "received", r[""]);
                auto pit = j.find("players");
                if (pit != j.end() && pit->is_object()) {
                    for (auto& [name, pl] : pit->items()) {
                        if (!pl.is_object()) continue;  // a null/garbage route is skipped, not fatal
                        ints(pl, "checked", c[name]);
                        ints(pl, "received", r[name]);
                    }
                }
            } else {
                if (why) *why = "top level is not an object";
                return false;
            }
            checked_ = std::move(c);                    // only commit once it parsed cleanly
            received_ = std::move(r);
            if (why) *why = "ok";
            return true;
        } catch (const std::exception& e) {
            if (why) *why = std::string("parse failed: ") + e.what() +
                            " (" + std::to_string(rawLen) + " bytes)";
            return false;
        } catch (...) {
            if (why) *why = "parse failed (unknown)";
            return false;
        }
    }

    // Called once at startup (single-threaded) - no lock (avoids DllMain loader-lock issues).
    //
    // A corrupt state file used to be swallowed here: the sets were cleared and play carried on as
    // if the player had checked nothing and received nothing. The next Save() then wrote that empty
    // state over the file, destroying the real progress for good.
    void Load() {
        std::string why;
        if (LoadFrom(state_path_, &why)) return;
        std::error_code ec;
        // MISSING vs UNREADABLE are different situations and must not be treated alike. A missing
        // state.json is a DELIBERATE fresh start - a new save, or reset_ark_test.bat - and falling
        // back to the backup there resurrects the previous run's engrams, which is exactly what an
        // earlier build did.
        if (!fs::exists(state_path_, ec)) {
            started_fresh_ = true;
            std::error_code ec2;
            if (fs::exists(BakPath(), ec2)) {
                fs::remove(BakPath(), ec2);             // stale: never resurrect it later either
                load_error_ = "state.json was missing - starting fresh (stale state.json.bak removed)";
            }
            return;
        }
        std::string whyBak;
        if (LoadFrom(BakPath(), &whyBak)) {
            load_error_ = "state.json unreadable (" + why + ") - recovered from state.json.bak";
            return;
        }
        // Neither copy is usable. PRESERVE the bad file under a timestamped name, then carry on
        // with an empty set and normal saving.
        //
        // The previous build went read-only here, which sounded careful and was actually worse:
        // Save() stayed blocked forever, so nothing the player did afterwards was ever persisted
        // and every restart replayed the same failure. Keeping a copy protects whatever might be
        // recoverable while letting the server move on - and REOWN/AUTORECOVER rebuild ownership
        // from Archipelago's own item list within seconds.
        std::error_code ec3;
        fs::path keep = state_path_;
        keep += ".corrupt-" + std::to_string((long long)std::time(nullptr));
        fs::copy_file(state_path_, keep, fs::copy_options::overwrite_existing, ec3);
        checked_.clear();
        received_.clear();
        load_error_ = "state.json unreadable (" + why + ") and state.json.bak " + whyBak +
                      " - a copy was kept as " + keep.filename().string() +
                      "; unlocks will rebuild from Archipelago";
    }

    // Was there a problem loading? (empty = clean). The plugin surfaces this in chat + the log.
    const std::string& LoadError() const { return load_error_; }
    // state.json was ABSENT at load - per docs/STATE_PERSISTENCE.md rule 2 that is a DELIBERATE
    // fresh start (new save, or a reset tool), never corruption. Callers use it to decide that
    // leftover mailbox history belongs to the previous seed.
    bool StartedFresh() const { return started_fresh_; }
    bool ReadOnly() const { return read_only_; }

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
        if (read_only_) return;                    // corrupt load - never clobber what's on disk

        // ATOMIC: the old code truncated state.json in place and streamed into it. Save() runs on
        // every single MarkChecked/AddItem, so the file is being rewritten constantly - and any
        // hard stop during that window (an ARK crash, or our own /confirm restart, which calls
        // TerminateProcess with no flush) leaves a truncated file. Write a temp, close it, keep the
        // previous good copy as .bak, then rename over the target: a rename on the same volume is
        // atomic, so the file on disk is always one complete state or the other, never a fragment.
        std::error_code ec;
        fs::path tmp = state_path_;
        tmp += ".tmp";
        { std::ofstream f(tmp, std::ios::trunc);
          if (!f) return;
          f << j.dump(2);
          f.flush();
          if (!f) return;                          // write failed - leave the good file alone
        }
        if (fs::exists(state_path_, ec)) {
            fs::remove(BakPath(), ec);
            fs::copy_file(state_path_, BakPath(), fs::copy_options::overwrite_existing, ec);
        }
        fs::rename(tmp, state_path_, ec);
        if (ec) {                                  // rename failed (locked?) - fall back in place
            std::ofstream f(state_path_, std::ios::trunc);
            if (f) f << j.dump(2);
            fs::remove(tmp, ec);
        }
    }

    fs::path BakPath() const {
        fs::path b = state_path_;
        b += ".bak";
        return b;
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
    size_t ReceivedCount(const std::string& p) const {
        auto it = received_.find(p);
        return it == received_.end() ? 0 : it->second.size();
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
    // NEW SEED: forget everything this route did in the old one. Both sets gate an early-return
    // (ReportLocation on checked_, ApplyItem on received_), so carrying them over makes the new
    // seed's checks and item grants silently no-op. Engrams the player already learned stay
    // learned in-game; AP resends the whole item list on connect, so received_ refills at once.
    void ResetRoute(const std::string& p) {
        checked_.erase(p);
        received_.erase(p);
        Save();
    }
    // /apresync: forget only what we think we SENT. Items stay, so nothing is re-granted; the
    // tick simply re-reports every location the player still satisfies.
    void ResetChecked(const std::string& p) {
        checked_.erase(p);
        Save();
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
    std::string load_error_;        // non-empty = surfaced in chat + the log at boot
    bool started_fresh_ = false;    // state.json was absent -> deliberate fresh start
    bool read_only_ = false;        // corrupt load: block Save so the file on disk survives
    std::map<std::string, std::set<int>> checked_, received_;   // route -> ids
    std::map<int, int> placement_;  // offline only
};

} // namespace ArkAP
