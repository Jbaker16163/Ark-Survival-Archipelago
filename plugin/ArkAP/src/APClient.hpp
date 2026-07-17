// APClient.hpp - embedded Archipelago client: speaks the AP websocket protocol in-process
// so players can type /connect in game chat instead of running the external Python connector.
//
// DESIGN: this is an EMBEDDED CONNECTOR, not a new integration. Each session reads/writes the
// exact same per-route mailbox files the external connector uses (checks_out.jsonl, items_in.jsonl,
// death_in/out.jsonl, msg_in.jsonl, hint_out.jsonl, hint_status.json, flags.json, session.json),
// so the plugin's battle-tested apply/dedup/state logic is untouched and the external connector
// remains a drop-in alternative (rollback = just don't type /connect).
//
// THREADING: each session runs 1 controller thread (connect/backoff + blocking websocket receive)
// plus 1 pump thread while connected (polls mailbox files every 500ms and sends). Session threads
// NEVER call ArkApi/game code - file IO + WinHTTP only. Status/errors surface in game chat via
// msg_in.jsonl, which the plugin's game tick already relays.
//
// NETWORK: Windows-native WinHTTP websocket API - wss:// via Schannel, zero vendored TLS libs.
// archipelago.gg terminates TLS on every room port, self-hosted rooms are usually plain ws://:
// try ws:// FIRST, then wss:// (see RunOnce for why this order is load-bearing), remember the winner.
#pragma once

#include <string>
#include <set>
#include <map>
#include <vector>
#include <atomic>
#include <thread>
#include <mutex>
#include <fstream>
#include <sstream>
#include <functional>
#include <filesystem>
#include <cmath>
#include <ctime>

#include <Windows.h>
#include <winhttp.h>
#pragma comment(lib, "winhttp.lib")

#include "json.hpp"

namespace ArkAP {

namespace fs = std::filesystem;
using json = nlohmann::json;

// NEVER use std::mutex in this plugin. The ARK server environment loads an ancient
// msvcp140.dll (14.16 / VS2017-era); binaries built with the modern MSVC STL (VS2022 17.10+
// constexpr-mutex layout) null-deref inside that dll's mtx_do_lock on the FIRST lock()
// (crash-dumped + symbolicated 2026-07-16; also the true cause of the old "Ipc std::mutex
// faulted in ReportCheck" incident). SRWLOCK is kernel32-only and immune.
struct ApLock {
    SRWLOCK l = SRWLOCK_INIT;
    void lock()   { AcquireSRWLockExclusive(&l); }
    void unlock() { ReleaseSRWLockExclusive(&l); }
};
using ApGuard = std::lock_guard<ApLock>;

static const char* AP_GAME = "ARK Survival Evolved";
static const int   AP_CLIENT_GOAL = 30;                 // AP ClientStatus.CLIENT_GOAL
static const int   AP_BOSS_LOCS[4] = { 8750000, 8750001, 8750002, 8750003 };

// ------------------------------------------------------------------ small helpers
inline std::wstring ApWiden(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

struct APEndpoint {                    // parsed "host:port" (scheme prefix tolerated)
    std::wstring host;
    INTERNET_PORT port = 38281;
    bool valid = false;
};
inline APEndpoint ApParseServer(std::string s) {
    APEndpoint ep;
    auto strip = [&](const char* p) { if (s.rfind(p, 0) == 0) s = s.substr(strlen(p)); };
    strip("wss://"); strip("ws://");
    auto c = s.rfind(':');
    if (c == std::string::npos || c == 0) return ep;
    try { ep.port = (INTERNET_PORT)std::stoi(s.substr(c + 1)); } catch (...) { return ep; }
    ep.host = ApWiden(s.substr(0, c));
    ep.valid = !ep.host.empty() && ep.port != 0;
    return ep;
}

// ------------------------------------------------------------------ APSession
// One AP slot <-> one mailbox folder. Lifecycle: Start() spawns the controller thread;
// Stop() signals + closes the socket + joins. All wire/file work happens on session threads.
class APSession {
public:
    struct Config {
        std::string route;        // plugin mailbox route ("" = root/solo)
        std::string slot;         // AP slot name
        std::string server;       // host:port
        std::string password;
        fs::path    mailbox;      // ipc dir for this route (already created)
        fs::path    pluginDir;    // for the root applied_index.json on seed reset
        std::function<void(const std::string&)> log;                 // -> ArkAP_debug.log
        std::function<std::string(int)> itemName;                    // id -> name ("" = unknown)
    };

    explicit APSession(Config cfg) : cfg_(std::move(cfg)) {
        if (!cfg_.log) cfg_.log = [](const std::string&) {};
        if (!cfg_.itemName) cfg_.itemName = [](int) { return std::string(); };
    }
    ~APSession() { Stop(); }

    void Start() {
        stop_ = false;
        controller_ = std::thread([this] { GuardedController(this); });
    }
    void Stop() {
        stop_ = true;
        Interrupt();                    // graceful WS close unblocks the blocking receive;
                                        // handle FREEING stays on the controller thread (a
                                        // CloseHandle here could race the in-flight receive
                                        // into a use-after-free)
        if (controller_.joinable()) controller_.join();
        CloseAll();                     // controller is gone - safe to free whatever remains
    }

    std::string Status() const {
        ApGuard g(statusMx_);
        return status_;
    }
    const std::string& Route() const { return cfg_.route; }
    const std::string& Slot()  const { return cfg_.slot; }
    const std::string& Server() const { return cfg_.server; }
    const std::string& Password() const { return cfg_.password; }
    bool Fatal() const { return fatal_; }

private:
    // ---------------- crash containment ----------------
    // A fault on a session thread must NEVER take the ARK server down. Two layers:
    // SEH (__try) at the thread entry catches access violations etc.; C++ try/catch inside
    // ControllerMain catches exceptions WITH the what() text so the debug log names the cause.
    // (These statics keep C++ objects out of the __try scope - C2712.)
    static void GuardedController(APSession* s) {
        __try { s->ControllerMain(); }
        __except (s->faultCode_ = GetExceptionCode(), EXCEPTION_EXECUTE_HANDLER) {
            s->OnThreadFault("controller");
        }
    }
    static void GuardedPump(APSession* s, std::atomic<bool>* pumpStop) {
        __try { s->PumpLoop(pumpStop); }
        __except (s->faultCode_ = GetExceptionCode(), EXCEPTION_EXECUTE_HANDLER) {
            s->OnThreadFault("pump");
        }
    }
    void OnThreadFault(const char* which) {
        fatal_ = true;
        char code[24];
        sprintf_s(code, "0x%08X", (unsigned)faultCode_);
        SetStatus(std::string("FAULTED (") + which + " thread, code=" + code +
                  ") - session stopped, server unaffected");
        try { QueueMsg("AP: '" + cfg_.slot + "' connection hit an internal error and stopped "
                       "(the server is fine). /connect to retry."); } catch (...) {}
    }

    // ---------------- status + in-game feedback ----------------
    void SetStatus(const std::string& s) {
        { ApGuard g(statusMx_); status_ = s; }
        cfg_.log("APC[" + (cfg_.route.empty() ? cfg_.slot : cfg_.route) + "] " + s);
    }
    void QueueMsg(const std::string& text) {             // -> game chat via the plugin's tick
        std::ofstream f(cfg_.mailbox / "msg_in.jsonl", std::ios::app);
        if (f) f << text << "\n";
    }

    // ---------------- controller: connect loop with backoff ----------------
    void ControllerMain() {
        int delay = 5;
        while (!stop_) {
            bool connected = false;
            // C++ exceptions are caught HERE with their message (the SEH guard above only
            // sees them as 0xE06D7363 with no text); either way the session dies, not the server.
            try { connected = RunOnce(); }
            catch (const std::exception& e) { SetStatus(std::string("exception: ") + e.what()); }
            catch (...) { SetStatus("unknown exception in connect cycle"); }
            if (stop_ || fatal_) break;
            if (connected) delay = 5;                     // had a real session -> reset backoff
            SetStatus("reconnecting in " + std::to_string(delay) + "s");
            for (int i = 0; i < delay * 2 && !stop_; ++i)
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            delay = (delay * 2 > 60) ? 60 : delay * 2;    // 5 -> 10 -> 20 -> 40 -> 60 cap
        }
        if (!fatal_) SetStatus("stopped");
        CloseAll();                                       // session handle freed on its own thread
    }

    // one connection lifecycle. Returns true if the handshake got as far as a live session.
    bool RunOnce() {
        APEndpoint ep = ApParseServer(cfg_.server);
        if (!ep.valid) { fatal_ = true; SetStatus("bad server address '" + cfg_.server + "'");
                         QueueMsg("AP: bad server address '" + cfg_.server + "'"); return false; }

        // ws FIRST, then wss - deliberately the opposite of the Python connector. Probing
        // wss against a PLAINTEXT server leaves a half-open TLS handshake that times out
        // (12002); cancelling it via WinHttpCloseHandle crashed the ARK server from inside
        // WinHTTP's own worker thread (live-diagnosed 2026-07-16, unguardable by our SEH).
        // Probing ws against a TLS server (archipelago.gg) is just a fast, ordinary HTTP
        // failure - safe. The winning scheme is remembered, so the probe runs once.
        // stage logs bracket every step so a fault in here is pinned to a line in the debug log.
        for (int attempt = 0; attempt < 2 && !stop_; ++attempt) {
            bool secure = (attempt == 0) == preferSecure_;
            cfg_.log(std::string("APC try ") + (secure ? "wss" : "ws") + "://" + cfg_.server);
            if (OpenSocket(ep, secure)) {
                preferSecure_ = secure;
                SetStatus(std::string("connected (") + (secure ? "wss" : "ws") + "://" + cfg_.server + ")");
                bool lived = PumpSession();
                cfg_.log("APC session ended");
                CloseSocket();
                return lived;
            }
            cfg_.log(std::string("APC ") + (secure ? "wss" : "ws") + " attempt failed (normal for "
                     "the wrong scheme - falling through)");
        }
        SetStatus("connect failed (" + cfg_.server + ")");
        return false;
    }

    // ---------------- WinHTTP websocket plumbing ----------------
    bool OpenSocket(const APEndpoint& ep, bool secure) {
        // ONE WinHTTP session for this APSession's whole life (freed by CloseAll at controller
        // exit). Tearing the session handle down between attempts risks cancelling internal
        // async work mid-flight - the crash family we're avoiding.
        if (!hSession_) {
            hSession_ = WinHttpOpen(L"ArkAP-embedded/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                    WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
            if (!hSession_) return false;
            WinHttpSetTimeouts(hSession_, 8000, 8000, 10000, 10000);
        }
        hConnect_ = WinHttpConnect(hSession_, ep.host.c_str(), ep.port, 0);
        if (!hConnect_) { CloseSocket(); return false; }
        HINTERNET hReq = WinHttpOpenRequest(hConnect_, L"GET", L"/", nullptr,
                                            WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                            secure ? WINHTTP_FLAG_SECURE : 0);
        if (!hReq) { CloseSocket(); return false; }
        cfg_.log("APC   handshake: sending upgrade request");
        BOOL ok = WinHttpSetOption(hReq, WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET, nullptr, 0)
               && WinHttpSendRequest(hReq, WINHTTP_NO_ADDITIONAL_HEADERS, 0, nullptr, 0, 0, 0)
               && WinHttpReceiveResponse(hReq, nullptr);
        cfg_.log(std::string("APC   handshake: ") + (ok ? "response received" :
                 ("failed, GetLastError=" + std::to_string(GetLastError()))));
        if (ok) {
            DWORD code = 0, sz = sizeof(code);
            WinHttpQueryHeaders(hReq, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                WINHTTP_HEADER_NAME_BY_INDEX, &code, &sz, WINHTTP_NO_HEADER_INDEX);
            if (code == 101) hWs_ = WinHttpWebSocketCompleteUpgrade(hReq, 0);
        }
        WinHttpCloseHandle(hReq);
        if (!hWs_) { CloseSocket(); return false; }
        // the 10s session receive timeout is right for the HTTP upgrade but wrong for the
        // websocket: an idle AP room sends nothing for minutes - make receives wait forever
        // (0 = infinite) instead of "timing out" into a spurious reconnect every 10s.
        DWORD infinite = 0;
        WinHttpSetOption(hWs_, WINHTTP_OPTION_RECEIVE_TIMEOUT, &infinite, sizeof(infinite));
        return true;
    }
    // ask the peer to close (unblocks a blocked WinHttpWebSocketReceive) WITHOUT freeing
    // handles - safe to call from any thread while the controller may still be receiving.
    void Interrupt() {
        ApGuard g(sendMx_);
        if (hWs_) WinHttpWebSocketClose(hWs_, WINHTTP_WEB_SOCKET_SUCCESS_CLOSE_STATUS, nullptr, 0);
    }
    // free the per-connection handles (websocket + connect), keep the session handle.
    // Controller thread only (or after it has been joined).
    void CloseSocket() {
        ApGuard g(sendMx_);
        if (hWs_)      { WinHttpWebSocketClose(hWs_, WINHTTP_WEB_SOCKET_SUCCESS_CLOSE_STATUS, nullptr, 0);
                         WinHttpCloseHandle(hWs_); hWs_ = nullptr; }
        if (hConnect_) { WinHttpCloseHandle(hConnect_); hConnect_ = nullptr; }
    }
    // full teardown including the long-lived session handle - only when the session is done.
    void CloseAll() {
        CloseSocket();
        ApGuard g(sendMx_);
        if (hSession_) { WinHttpCloseHandle(hSession_); hSession_ = nullptr; }
    }
    bool SendJson(const json& j) {                        // AP protocol: one JSON array per message
        std::string s = json::array({ j }).dump();
        ApGuard g(sendMx_);
        if (!hWs_) return false;
        return WinHttpWebSocketSend(hWs_, WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE,
                                    (PVOID)s.data(), (DWORD)s.size()) == NO_ERROR;
    }
    // blocking read of one complete text message ("" + false on close/error)
    bool RecvMessage(std::string& out) {
        out.clear();
        char buf[65536];
        for (;;) {
            DWORD read = 0;
            WINHTTP_WEB_SOCKET_BUFFER_TYPE type;
            HINTERNET ws = hWs_;                          // freed only on THIS thread (CloseSocket)
            if (!ws || stop_) return false;
            DWORD rc = WinHttpWebSocketReceive(ws, buf, sizeof(buf), &read, &type);
            if (rc == ERROR_WINHTTP_TIMEOUT) continue;    // idle is not an error - keep waiting
            if (rc != NO_ERROR) return false;
            if (type == WINHTTP_WEB_SOCKET_CLOSE_BUFFER_TYPE) return false;
            out.append(buf, read);
            if (type == WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE ||
                type == WINHTTP_WEB_SOCKET_BINARY_MESSAGE_BUFFER_TYPE)
                return true;                              // non-fragment = message complete
        }
    }

    // ---------------- one live session: recv loop + pump thread ----------------
    bool PumpSession() {
        // fresh per-connection protocol state (AP resends everything on reconnect)
        receivedIdx_.clear();
        players_.clear();
        connectedOk_ = false;
        goaled_ = false;
        // skip pre-existing backlogs exactly like the Python connector does at startup
        deathsSent_ = CountLines(cfg_.mailbox / "death_out.jsonl");
        hintsSent_  = CountLines(cfg_.mailbox / "hint_out.jsonl");

        std::atomic<bool> pumpStop{ false };
        std::thread pump([this, &pumpStop] { GuardedPump(this, &pumpStop); });

        bool sawRoom = false;
        std::string raw;
        while (!stop_ && RecvMessage(raw)) {
            json arr = json::parse(raw, nullptr, false);
            if (arr.is_discarded() || !arr.is_array()) continue;
            for (auto& msg : arr) {
                try { Handle(msg, sawRoom); } catch (...) {}
                if (fatal_) break;
            }
            if (fatal_) break;
        }
        pumpStop = true;
        if (pump.joinable()) pump.join();
        return sawRoom;
    }

    void Handle(const json& msg, bool& sawRoom) {
        std::string cmd = msg.value("cmd", "");
        if (cmd == "RoomInfo") {
            sawRoom = true;
            hintCostPct_ = msg.value("hint_cost", 10);
            ResetOnNewSeed(msg.value("seed_name", ""));
            SendJson({ {"cmd", "Connect"}, {"game", AP_GAME}, {"name", cfg_.slot},
                       {"password", cfg_.password}, {"uuid", "ark-embedded-client"},
                       {"version", { {"major",0},{"minor",6},{"build",7},{"class","Version"} }},
                       {"items_handling", 7}, {"tags", json::array({"DeathLink"})},
                       {"slot_data", true} });
        } else if (cmd == "Connected") {
            OnConnected(msg);
        } else if (cmd == "ReceivedItems") {
            int base = msg.value("index", 0);
            int i = 0;
            for (auto& it : msg.value("items", json::array())) {
                int idx = base + i++;
                if (!receivedIdx_.insert(idx).second) continue;
                json rec = { {"item_id", it.value("item", 0)},
                             {"from", players_.count(it.value("player", -1))
                                        ? players_[it.value("player", -1)] : std::string()},
                             {"index", idx} };
                std::ofstream f(cfg_.mailbox / "items_in.jsonl", std::ios::app);
                if (f) f << rec.dump() << "\n";
            }
        } else if (cmd == "RoomUpdate") {
            if (msg.contains("hint_points")) { hintPoints_ = msg.value("hint_points", 0); WriteHintStatus(); }
        } else if (cmd == "Bounced") {
            auto tags = msg.value("tags", json::array());
            bool dl = false; for (auto& t : tags) if (t == "DeathLink") dl = true;
            if (dl && deathLink_) {
                json data = msg.value("data", json::object());
                if (data.value("source", "") != cfg_.slot) {
                    std::string cause = data.value("cause", data.value("source", "someone") + " died");
                    QueueMsg("DeathLink: " + cause);
                    std::ofstream f(cfg_.mailbox / "death_in.jsonl", std::ios::app);
                    if (f) f << data.dump() << "\n";
                }
            }
        } else if (cmd == "ConnectionRefused") {
            fatal_ = true;
            std::string err = msg.value("errors", json::array()).dump();
            SetStatus("REFUSED " + err);
            QueueMsg("AP refused '" + cfg_.slot + "': " + err + " - /disconnect then /connect again");
        } else if (cmd == "PrintJSON") {
            std::string type = msg.value("type", "");
            if (type == "ItemSend" || type == "ItemCheat") {
                // our check released someone else's item -> show it in ARK chat
                json item = msg.value("item", json::object());
                int sender = item.value("player", -1);
                int receiver = msg.value("receiving", -1);
                if (mySlot_ >= 0 && sender == mySlot_ && receiver != mySlot_ && receiver >= 0) {
                    std::string rn = players_.count(receiver) ? players_[receiver]
                                                              : ("player " + std::to_string(receiver));
                    QueueMsg(cfg_.slot + " sent an item to " + rn);
                }
            } else if (type == "Hint") {
                json item = msg.value("item", json::object());
                int recv = msg.value("receiving", -1), finder = item.value("player", -1);
                if (mySlot_ >= 0 && (recv == mySlot_ || finder == mySlot_)) {
                    std::string iname = (recv == mySlot_) ? cfg_.itemName(item.value("item", 0)) : std::string();
                    if (iname.empty()) iname = "item " + std::to_string(item.value("item", 0));
                    std::string fn = players_.count(finder) ? players_[finder]
                                                            : ("player " + std::to_string(finder));
                    QueueMsg("Hint: " + iname + " is in " + fn + "'s world (" +
                             (msg.value("found", false) ? "already found" : "not found yet") + ")");
                }
            }
        }
    }

    void OnConnected(const json& msg) {
        mySlot_ = msg.value("slot", -1);
        for (auto& p : msg.value("players", json::array()))
            players_[p.value("slot", -1)] = p.value("alias", "").empty()
                ? p.value("name", "") : p.value("alias", "");
        json sd = msg.value("slot_data", json::object());
        if (sd.is_null()) sd = json::object();
        deathLink_ = sd.value("death_link", deathLink_);

        // goal: boss groups (any difficulty per boss) with legacy single-loc fallback
        requiredGroups_.clear();
        int gb = sd.value("goal_bosses", 0);
        if (gb > 0 && sd.contains("boss_groups") && sd["boss_groups"].is_array()) {
            int n = 0;
            for (auto& g : sd["boss_groups"]) {
                if (n++ >= gb) break;
                std::vector<int> grp;
                for (auto& l : g) grp.push_back(l.get<int>());
                if (!grp.empty()) requiredGroups_.push_back(grp);
            }
        } else if (gb > 0) {
            for (int i = 0; i < gb && i < 4; ++i) requiredGroups_.push_back({ AP_BOSS_LOCS[i] });
        }

        // relay plugin flags (same file/content as the Python connector)
        try { std::ofstream(cfg_.mailbox / "flags.json")
                << json({ {"bundle_saddles", sd.value("bundle_saddles", false)},
                          {"free_starter_engrams", sd.value("free_starter_engrams", false)} }).dump(); }
        catch (...) {}

        totalLocs_ = (int)msg.value("checked_locations", json::array()).size()
                   + (int)msg.value("missing_locations", json::array()).size();
        hintPoints_ = msg.value("hint_points", 0);
        WriteHintStatus();
        try { WriteSpawnFragment(sd); } catch (...) {}

        // seed sent checks with what AP already knows so we don't re-send them
        for (auto& l : msg.value("checked_locations", json::array()))
            sentChecks_.insert(l.get<int>());

        connectedOk_ = true;
        SetStatus("connected as '" + cfg_.slot + "' (" +
                  std::to_string(msg.value("missing_locations", json::array()).size()) +
                  " locations remaining)");
        QueueMsg("AP: connected as '" + cfg_.slot + "'" +
                 (cfg_.route.empty() ? "" : " for " + cfg_.route));
    }

    // ---------------- pump: mailbox files -> AP (every 500ms) ----------------
    void PumpLoop(std::atomic<bool>* pumpStop) {
        while (!*pumpStop && !stop_) {
            try { PumpOnce(); } catch (...) {}
            for (int i = 0; i < 5 && !*pumpStop && !stop_; ++i)
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }
    void PumpOnce() {
        if (!connectedOk_) return;
        // checks_out.jsonl -> LocationChecks (whole-file read; dedup via sentChecks_)
        std::vector<int> fresh;
        { std::ifstream f(cfg_.mailbox / "checks_out.jsonl");
          std::string line;
          while (std::getline(f, line)) {
              auto p = line.find("\"loc_id\"");
              if (p == std::string::npos) continue;
              int loc = 0;
              try { loc = std::stoi(line.substr(line.find(':', p) + 1)); } catch (...) { continue; }
              if (sentChecks_.insert(loc).second) fresh.push_back(loc);
          } }
        if (!fresh.empty())
            SendJson({ {"cmd", "LocationChecks"}, {"locations", fresh} });

        // death_out.jsonl -> Bounce DeathLink
        if (deathLink_) {
            size_t total = CountLines(cfg_.mailbox / "death_out.jsonl");
            if (total < deathsSent_) deathsSent_ = total;              // file reset -> resync
            while (deathsSent_ < total) {
                ++deathsSent_;
                SendJson({ {"cmd", "Bounce"}, {"tags", json::array({"DeathLink"})},
                           {"data", { {"time", (double)std::time(nullptr)},
                                      {"source", cfg_.slot},
                                      {"cause", cfg_.slot + " died in ARK"} }} });
            }
        }
        // hint_out.jsonl -> Say !hint <item>
        { std::vector<std::string> lines;
          std::ifstream f(cfg_.mailbox / "hint_out.jsonl");
          std::string line;
          while (std::getline(f, line)) if (!line.empty()) lines.push_back(line);
          if (lines.size() < hintsSent_) hintsSent_ = lines.size();
          while (hintsSent_ < lines.size()) {
              SendJson({ {"cmd", "Say"}, {"text", "!hint " + lines[hintsSent_]} });
              ++hintsSent_;
          } }
        // goal: every required boss has ANY difficulty checked
        if (!goaled_ && !requiredGroups_.empty()) {
            bool all = true;
            for (auto& g : requiredGroups_) {
                bool any = false;
                for (int l : g) if (sentChecks_.count(l)) { any = true; break; }
                if (!any) { all = false; break; }
            }
            if (all) {
                goaled_ = true;
                QueueMsg("AP: goal reached for '" + cfg_.slot + "'!");
                SendJson({ {"cmd", "StatusUpdate"}, {"status", AP_CLIENT_GOAL} });
            }
        }
    }

    // ---------------- file helpers (formats identical to the Python connector) ----------------
    static size_t CountLines(const fs::path& p) {
        std::ifstream f(p);
        size_t n = 0; std::string line;
        while (std::getline(f, line)) if (!line.empty()) ++n;
        return n;
    }
    void WriteHintStatus() {
        int cost = (int)std::ceil(hintCostPct_ / 100.0 * totalLocs_);
        try { std::ofstream(cfg_.mailbox / "hint_status.json")
                << json({ {"hint_points", hintPoints_}, {"hint_cost", cost} }).dump(); }
        catch (...) {}
    }
    // On a NEW seed the AP item indices restart at 0: clear items_in + the applied-index
    // watermarks (root lives in the plugin dir, this route's inside its mailbox).
    void ResetOnNewSeed(const std::string& seed) {
        if (seed.empty()) return;
        fs::path sess = cfg_.mailbox / "session.json";
        std::string last;
        try { if (fs::exists(sess)) { json j; std::ifstream(sess) >> j; last = j.value("seed", ""); } }
        catch (...) {}
        if (seed == last) return;
        std::error_code ec;
        fs::remove(cfg_.mailbox / "items_in.jsonl", ec);
        fs::remove(cfg_.mailbox / "applied_index.json", ec);
        if (cfg_.route.empty()) fs::remove(cfg_.pluginDir / "applied_index.json", ec);
        receivedIdx_.clear();
        try { std::ofstream(sess) << json({ {"seed", seed} }).dump(); } catch (...) {}
        SetStatus("new seed '" + seed + "' - cleared item backlog");
    }
    // randomize_dino_spawns: write the Game.ini fragment (embedded client can't patch the live
    // Game.ini - ARK rewrites it from memory on shutdown while we're running inside the server).
    void WriteSpawnFragment(const json& sd) {
        std::vector<std::string> lines;
        for (auto& ov : sd.value("spawn_overrides", json::array())) {
            std::string container = ov.at(0).get<std::string>();
            std::string parts;
            for (auto& e : ov.at(1)) {
                std::string cls; double w = 1.0;
                if (e.is_string()) cls = e.get<std::string>();
                else { cls = e.at(0).get<std::string>(); w = e.at(1).get<double>(); }
                if (!parts.empty()) parts += ",";
                std::ostringstream ws; ws << w;
                parts += "(AnEntryName=\"AP_" + cls + "\",EntryWeight=" + ws.str() +
                         ",NPCsToSpawnStrings=(\"" + cls + "\"))";
            }
            lines.push_back("ConfigOverrideNPCSpawnEntriesContainer=("
                            "NPCSpawnEntriesContainerClassString=\"" + container + "\","
                            "NPCSpawnEntries=(" + parts + "))");
        }
        fs::path frag = cfg_.mailbox / "game_ini_fragment.txt";
        if (lines.empty()) { std::error_code ec; fs::remove(frag, ec); return; }
        std::ofstream f(frag);
        f << "[/script/shootergame.shootergamemode]\n";
        for (auto& l : lines) f << l << "\n";
        QueueMsg("AP: spawn randomizer active - paste ipc\\game_ini_fragment.txt into Game.ini "
                 "(or run the external connector once with game_ini set), then restart the server");
    }

    // ---------------- members ----------------
    Config cfg_;
    std::thread controller_;
    std::atomic<bool> stop_{ false };
    std::atomic<bool> fatal_{ false };
    DWORD faultCode_ = 0;                       // SEH code captured by the thread guards

    HINTERNET hSession_ = nullptr, hConnect_ = nullptr;
    HINTERNET hWs_ = nullptr;
    ApLock sendMx_;
    bool preferSecure_ = false;                 // ws first (see RunOnce comment) - wss probe
                                                // against plaintext crashes WinHTTP in-process

    mutable ApLock statusMx_;
    std::string status_ = "starting";

    // protocol state
    bool connectedOk_ = false;
    bool deathLink_ = true;
    bool goaled_ = false;
    int  mySlot_ = -1;
    int  hintCostPct_ = 10, hintPoints_ = 0, totalLocs_ = 0;
    std::set<int> receivedIdx_;                 // item indices written this connection
    std::set<int> sentChecks_;                  // persists across reconnects (AP reseeds it too)
    std::vector<std::vector<int>> requiredGroups_;
    std::map<int, std::string> players_;
    size_t deathsSent_ = 0, hintsSent_ = 0;
};

// ------------------------------------------------------------------ APManager
// route -> session. Persists connections (ap_connections.json in the plugin dir) so a server
// restart reconnects everyone automatically. Game-thread only (commands + load/unload).
class APManager {
public:
    APManager(fs::path pluginDir,
              std::function<void(const std::string&)> log,
              std::function<std::string(int)> itemName,
              std::function<fs::path(const std::string&)> mailboxFor)
        : dir_(std::move(pluginDir)), log_(std::move(log)),
          itemName_(std::move(itemName)), mailboxFor_(std::move(mailboxFor)) {}

    ~APManager() { StopAll(); }

    std::string Connect(const std::string& route, const std::string& slot,
                        const std::string& server, const std::string& password) {
        if (!ApParseServer(server).valid)
            return "bad server address - use host:port (e.g. archipelago.gg:38281)";
        auto it = sessions_.find(route);
        if (it != sessions_.end()) { it->second->Stop(); sessions_.erase(it); }
        // also retire any session for the SAME SLOT under a different route (e.g. a first
        // /connect that bound to "_unnamed" before the survivor name resolved) - two live
        // sessions for one slot would double-send checks and deaths.
        for (auto sit = sessions_.begin(); sit != sessions_.end(); ) {
            if (sit->second->Slot() == slot) { sit->second->Stop(); sit = sessions_.erase(sit); }
            else ++sit;
        }
        APSession::Config c;
        c.route = route; c.slot = slot; c.server = server; c.password = password;
        c.mailbox = mailboxFor_(route); c.pluginDir = dir_;
        c.log = log_; c.itemName = itemName_;
        sessions_[route] = std::make_unique<APSession>(std::move(c));
        sessions_[route]->Start();
        Persist();
        return "connecting '" + slot + "' to " + server +
               (route.empty() ? "" : " (mailbox " + route + ")") + "...";
    }

    std::string Disconnect(const std::string& route) {
        auto it = sessions_.find(route);
        if (it == sessions_.end()) return "no AP connection for this player";
        it->second->Stop();
        sessions_.erase(it);
        Persist();
        return "disconnected";
    }

    std::string StatusAll() const {
        if (sessions_.empty()) return "no embedded AP connections (use /connect <slot> <host:port> [password])";
        std::string out;
        for (auto& [route, s] : sessions_) {
            if (!out.empty()) out += " | ";
            out += (route.empty() ? "(solo)" : route) + ": " + s->Slot() + " @ " +
                   s->Server() + " - " + s->Status();
        }
        return out;
    }

    void StopAll() {
        for (auto& [_, s] : sessions_) s->Stop();
        sessions_.clear();
    }

    // restart persisted connections (called once from Load; threads only touch files/network)
    void ResumePersisted() {
        try {
            fs::path p = dir_ / "ap_connections.json";
            if (!fs::exists(p)) return;
            json j; std::ifstream(p) >> j;
            for (auto& c : j.value("connections", json::array())) {
                std::string route = c.value("route", "");
                if (route == "_unnamed") continue;       // never resume a mis-bound session
                APSession::Config cfg;
                cfg.route = route;
                cfg.slot = c.value("slot", "");
                cfg.server = c.value("server", "");
                cfg.password = c.value("password", "");
                if (cfg.slot.empty() || !ApParseServer(cfg.server).valid) continue;
                cfg.mailbox = mailboxFor_(route); cfg.pluginDir = dir_;
                cfg.log = log_; cfg.itemName = itemName_;
                sessions_[route] = std::make_unique<APSession>(std::move(cfg));
                sessions_[route]->Start();
            }
            if (!sessions_.empty())
                log_("APC resumed " + std::to_string(sessions_.size()) + " persisted connection(s)");
        } catch (...) {}
    }

private:
    void Persist() const {
        // NOTE: room passwords are stored in plain text on the server (same trust level as
        // connector.ini for the external connector - the server admin can already see them).
        try {
            json arr = json::array();
            for (auto& [route, s] : sessions_)
                arr.push_back({ {"route", route}, {"slot", s->Slot()},
                                {"server", s->Server()}, {"password", s->Password()} });
            std::ofstream(dir_ / "ap_connections.json") << json({ {"connections", arr} }).dump(2);
        } catch (...) {}
    }

    fs::path dir_;
    std::function<void(const std::string&)> log_;
    std::function<std::string(int)> itemName_;
    std::function<fs::path(const std::string&)> mailboxFor_;
    std::map<std::string, std::unique_ptr<APSession>> sessions_;
};

} // namespace ArkAP
