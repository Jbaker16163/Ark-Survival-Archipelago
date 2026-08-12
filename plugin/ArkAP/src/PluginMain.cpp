// PluginMain.cpp - ArkAP: ARK: Survival Evolved <-> Archipelago (ArkServerApi, Pre-Aquatica)
//
// Built against the ArkApi SDK (version/Core/Public). Conventions taken from the
// AllEngrams example: DllMain -> Load/Unload, DECLARE_HOOK, GetHooks().SetHook,
// player_controller->GetShooterPlayerState()->ServerUnlockEngram(...).
//
// v1 scope (real, confirmed functions):
//   - GATE engrams      : hook AShooterPlayerState.ServerUnlockEngram
//   - CHECK dossiers     : hook AShooterPlayerController.ServerUnlockPerMapExplorerNote_Implementation
//   - CHECK first tame   : hook AShooterPlayerController.ClientNotifyTamedDino_Implementation
//   - APPLY items        : grant engrams received from AP; record other items for gating
//   - IPC poll           : API::Timer recurring (game thread) reads items_in.jsonl
//   - DumpEngrams/DumpNotes console cmds to harvest real ids for the data files
//
// Marked // VERIFY where an SDK detail should be confirmed on first compile.
// TODO (need more digging): supply-crate gate, taming gate, boss-defeat check.

#include <fstream>
#include <sstream>
#include <ctime>
#include <cctype>
#include <unordered_map>
#include <algorithm>   // std::search (case-insensitive Game.ini section find)
#include <Windows.h>   // SEH (__try/__except) to survive access violations in game-data reads

#include <API/ARK/Ark.h>
#include "Timer.h"
#include "json.hpp"
#include "ArkAP.hpp"
#include "APClient.hpp"

#pragma comment(lib, "ArkApi.lib")

using ArkAP::Tables;
using ArkAP::State;
using ArkAP::Ipc;
using ArkAP::Mode;

// ----------------------------------------------------------------- globals
static Tables g_tables;
static std::unique_ptr<State> g_state;
static std::unique_ptr<Ipc>   g_ipc;
static std::unique_ptr<ArkAP::APManager> g_apManager;   // embedded AP client (/connect)
static Mode  g_mode = Mode::AP;
static bool  g_applying = false;       // true while WE grant, so the gate doesn't block us
// MULTIPLAYER (ArkAP.config.json "multiplayer": true): every gate/grant/check is routed by the
// acting player's survivor character name ("route"). Each route has its own ipc/<name>/ mailbox
// (one connector instance per AP slot) + its own state/counter buckets. Flag OFF = every route
// is "" = one shared bucket + the root ipc folder = exactly the old solo behavior.
static bool  g_multiplayer = false;
static std::string g_gameIniOverride;      // ArkAP.config.json "game_ini_path" (blank = auto-derive)
static std::map<std::string, std::time_t> g_suppressDeathUntil;  // per-route anti-loop for DeathLink kills
// live COLLECTIVE counters per route (every tame/kill/breed, repeats included). Persisted in
// counters.json. Hooks append "<kind>\t<route>" lines to events_queue.jsonl on the net thread;
// the game tick drains new lines (persisted queue_pos) into these totals.
static std::map<std::string, int> g_totalTames, g_totalKills, g_totalBreeds, g_totalDeaths;
static bool  g_countersLoaded = false;
static bool  g_registry_built = false;
static bool  g_tickFaulted = false;    // set by Tick's __except, logged next tick
static bool  g_pollFaulted = false;
static bool  g_reassertFaulted = false;

// engram registry, built once the server is ready
static std::unordered_map<UClass*, int> g_engramClassToItem;          // item blueprint class -> AP item id
// The game's own engram ENTRY behind each mapped class. Kept purely so a failed unlock can be
// explained with facts (level, points, prereq chain) instead of a guess.
static std::unordered_map<UClass*, UPrimalEngramEntry*> g_classToEntry;
// AP item id -> its engram item class(es). A MOD item can own several classes that share one
// display name (the apworld groups them), so this is a LIST; base-game items have exactly one.
static std::unordered_map<int, std::vector<UClass*>> g_itemToEngram;
static std::set<int> g_starterItemIds;  // free starter engram item ids (from engrams.json starter_engrams)
// PER-ROUTE flags (each slot's own flags.json) - NOT global. A mixed multiplayer lobby (one player
// free_starter/bundle_saddles on, another off) must not leak one slot's setting onto everyone.
static std::map<std::string, bool> g_routeFreeStarter;
static std::map<std::string, bool> g_routeBundleSaddles;
static bool FlagFor(const std::map<std::string, bool>& m, const std::string& r) {
    auto it = m.find(r); return it != m.end() && it->second;
}

// taming registry: DinoNameTag -> AP item id (loaded straight from dinos.json, no game data)
static std::unordered_map<std::string, int> g_tameTagToItem;
static std::unordered_map<std::string, int> g_tameTagToTameLoc;   // DinoNameTag -> "Tamed: X" check loc
static std::unordered_map<std::string, int> g_killTagToLoc;       // DinoNameTag -> "Killed: X" check loc

// saddle bundling: tame item id -> its saddle ENGRAM item id; gated PER-ROUTE by g_routeBundleSaddles.
static std::unordered_map<int, int> g_tameItemToSaddleItem;
// route -> mod ids that slot enabled (from flags.json / slot_data). The plugin loads the WHOLE mod
// catalogue, so a structure bundle must skip engrams belonging to a mod this slot didn't take.
static std::map<std::string, std::set<std::string>> g_routeMods;
// route -> {representative item id -> folded member item ids} (from flags.json / slot_data's
// item_groups; set by engrams_per_item / tames_per_item). When a representative arrives, the
// members were never pooled by AP, so the plugin unlocks them here off the representative.
static std::map<std::string, std::map<int, std::vector<int>>> g_routeItemGroups;
// route -> {unpooled item id -> the POOLED item id that unlocks it}. AP can only hint items it
// actually placed, so /hint on a bundled member (count-group, S+ variant, structure bundle, mod
// group, bundled saddle) must be redirected to the item you should actually chase.
static std::map<std::string, std::map<int, int>> g_routeHintRedirect;
// Is this item allowed for this route? Base-game items ("" owner) always are; a mod item only if
// that slot enabled the mod. Unknown route (no flags yet) -> allow, so nothing silently vanishes.
static bool ItemAllowedForRoute(const std::string& route, int item_id) {
    auto oit = g_tables.item_to_mod.find(item_id);
    if (oit == g_tables.item_to_mod.end() || oit->second.empty()) return true;   // base game
    auto rit = g_routeMods.find(route);
    if (rit == g_routeMods.end() || rit->second.empty()) return true;            // unknown -> allow
    return rit->second.count(oit->second) > 0;
}

// trap filler: item id -> dino spawn spec (effect = spawn wild dinos at <distance> in front)
struct TrapSpawn { std::string blueprint; int count; int level; int distance; };
static std::unordered_map<int, TrapSpawn> g_fillerSpawn;
static std::set<APrimalDinoCharacter*> g_trapDinos;   // spawned trap dinos -> the tame gate refuses them

// good filler: item id -> one or more GFI give specs (effect = give item(s) to the player)
struct FillerGive { std::string gfi; int qty; int quality; std::string code; };
static std::unordered_map<int, std::vector<FillerGive>> g_fillerGive;

// buff/debuff filler: item id -> console command run AS the target player
// (e.g. "ForceGiveBuff Buff_Bleeding true"). Debuffs are trap-flagged in filler.json.
static std::unordered_map<int, std::string> g_fillerBuff;

// crate registry (loaded from crates.json): crate class name -> gated access item
static std::unordered_map<std::string, int> g_crateGateClassToItem;     // beacon/cave/deepsea -> access item id

// boss registry: class-name substring -> per-difficulty boss check locs + tek grant key.
// difficulty from the actor class name: "_Easy" = Gamma, "_Medium" = Beta, else Alpha.
struct BossEntry { std::string frag; std::string baseTag; int locGamma = 0; int locBeta = 0; int locAlpha = 0; };
static std::vector<BossEntry> g_bosses;
// alpha-predator kills: class-name fragment -> "Killed: Alpha X" check loc
static std::vector<std::pair<std::string, int>> g_alphaFragToLoc;
// tek grants: boss baseTag -> engram item ids granted locally on that boss's first kill
static std::unordered_map<std::string, std::vector<int>> g_tekGrants;
// inventory "hold N" checks: fire loc when the player holds >= qty of item_class (substring)
struct InvCheck { int loc; std::string cls; int qty; std::string name; };
// exploration checks: a region is a POLYGON of world X/Y measured in-game with /dumppos. Being
// anywhere inside the loop counts. Regions deliberately overlap (caves sit under biomes), so a
// player can complete several at once - we test them all, we do not stop at the first hit.
// Altitude is ignored on purpose: you fly through the volcano to reach its maw.
// A region can be made of SEVERAL disjoint shapes. Hand-drawn areas are one loop, but the ones
// imported from ark.wiki.gg's region data are rectangle sets - MurderSnow is 24 of them - and a
// bounding box round those would swallow half the map. `parts` holds them all; being inside ANY
// part counts. Single-shape regions just have one part, so nothing about them changes.
struct ExploreArea { int loc; std::string name;
                     std::vector<std::vector<std::pair<double, double>>> parts; };
static std::vector<ExploreArea> g_explore;
// DEPTH regions have no polygon - they fire anywhere below a world Z. The deep ocean cannot be
// circled like a landmass, and a surface polygon would fire from a bird flying over it, which
// would hand out the Scuba-gated check for free.
struct DepthArea { int loc; std::string name; double zBelow; };
static std::vector<DepthArea> g_depth;
// death checks: "kind" tag (from locations.json) -> loc id
static std::map<std::string, int> g_deathKindToLoc;
// ---- MAP FILTERING -------------------------------------------------------------------------
// A location that belongs only to another map must never fire here. The Island's exploration
// polygons are plain world coordinates, so standing at the same X/Y on Scorched Earth completes
// an Island region - the check is real, the player is nowhere near it. Same shape of problem for
// any other map-specific check, which is why the guard lives in ReportLocation (the one choke
// point) rather than only in the exploration test.
//
// FAIL-OPEN, matching the apworld's _map_filter: an id maps.json has never heard of still fires.
// Dropping unknown ids would turn "we forgot to tag a new category" into checks that silently stop
// working, which is far harder to notice than an extra check.
static std::set<int> g_mapAllowedIds;      // ids on THIS map (plus "any")
static std::set<int> g_mapKnownIds;        // ids tagged for ANY map at all
static std::string   g_mapKey;             // our key for the running map ("island", "scorched", ...)
static bool MapAllowsLoc(int id) {
    if (g_mapKnownIds.empty()) return true;            // no maps.json -> no filtering
    if (!g_mapKnownIds.count(id)) return true;         // untagged -> fail open
    return g_mapAllowedIds.count(id) != 0;
}
// RECOVERY WINDOW. When the item list is deliberately re-sent to rebuild lost state, the player
// ALREADY owns everything in it - so announcing all 200 unlocks in chat and re-firing every filler
// effect is pure noise (and a shower of free resources). While a route is inside its window,
// ApplyItem still records ownership and pushes engram unlocks, but says nothing and skips filler.
// Keyed by route -> unix time the window closes; the re-send arrives asynchronously after the
// client reconnects, so it is a time window rather than a single pass.
static std::map<std::string, long long> g_quietUntil;
static bool QuietFor(const std::string& route) {
    auto it = g_quietUntil.find(route);
    if (it == g_quietUntil.end()) return false;
    if (std::time(nullptr) > it->second) { g_quietUntil.erase(it); return false; }
    return true;
}
static std::vector<InvCheck> g_invChecks;

namespace fs = std::filesystem;

// Which dll is actually loaded. Declared up here rather than beside Load() because the JOIN greet
// and /apstatus both quote it: "what version are they running?" was answered by asking someone to
// find a log file on the server box, which is no answer at all when the report comes from a player.
static const char* ARKAP_BUILD = "v165-fresh-start-clears-mailboxes";

// the plugin's own folder: ArkApi/Plugins/ArkAP
static fs::path PluginDir() {
    return fs::current_path() / "ArkApi" / "Plugins" / "ArkAP";
}

// forward decls (defined below)
void ReportLocation(const std::string& route, int loc_id);
void ApplyItem(const std::string& route, int item_id, const std::string& from);

// True only once the server is fully up - guards all game-data access.
static bool ServerReady() {
    return ArkApi::GetApiUtils().GetStatus() == ArkApi::ServerStatus::Ready;
}

// Safe fetch of the engram-entry list; returns nullptr until game data exists.
static UPrimalGameData* GameData() {
    auto* engine = Globals::GEngine()();          // same pattern as the AllEngrams example
    if (!engine) return nullptr;
    auto* globals = static_cast<UPrimalGlobals*>(engine->GameSingletonField());
    return globals ? globals->PrimalGameDataOverrideField() : nullptr;
}

static void DebugLog(const std::string& s) {
    char buf[16] = "??:??:??";
    std::time_t t = std::time(nullptr);
    std::tm tmv{};
    if (localtime_s(&tmv, &t) == 0) std::strftime(buf, sizeof(buf), "%H:%M:%S", &tmv);
    std::ofstream f(PluginDir() / "ArkAP_debug.log", std::ios::app);
    if (f) f << "[" << buf << "] " << s << "\n";
}

static std::string ClassShortName(UClass* cls) {
    if (!cls) return "";
    FString n; cls->GetFullName(&n, nullptr);    // UObjectBaseUtility.GetFullName
    return n.ToString();
}

// ----------------------------------------------------------------- multiplayer routing
// route = survivor character name, filesystem-safe ("" = solo/shared). All helpers hold FString
// locals, so callers keep them OUT of __try blocks (call from Do* workers).
static std::string SanitizeRoute(const std::string& s) {
    std::string out;
    for (char c : s)
        if (isalnum((unsigned char)c) || c == ' ' || c == '-' || c == '_') out += c;
    while (!out.empty() && out.back() == ' ') out.pop_back();
    while (!out.empty() && out.front() == ' ') out.erase(out.begin());
    if (out.size() > 40) out.resize(40);
    return out.empty() ? "_unnamed" : out;
}
static std::string RouteFor(AShooterPlayerController* pc) {
    if (!g_multiplayer || !pc) return "";
    FString n = ArkApi::GetApiUtils().GetCharacterName(pc);
    return SanitizeRoute(n.ToString());
}
// the connected controller whose character team matches (kill/breed attribution). null = none.
static AShooterPlayerController* PcForTeam(int team) {
    if (team == 0) return nullptr;
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) return nullptr;
    for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (!pc) continue;
        AShooterCharacter* ch = pc->GetPlayerCharacter();
        if (ch && ch->TargetingTeamField() == team) return pc;
    }
    return nullptr;
}
// the controller a route's items/effects target. route "" (solo) = the first connected player.
// Every controller an effect for `route` should hit. The two modes are deliberately simple:
//
//   multiplayer = false  ONE Archipelago slot for the whole server. An unlock belongs to everybody
//                        connected, so every effect fans out to every player in-world.
//   multiplayer = true   One slot (and one yaml) PER survivor. An effect reaches only that
//                        survivor's controller.
//
// Engram grants and the tame gate were already server-wide in shared mode; the filler effects were
// not - GiveFiller/BuffFiller/SpawnTrap each took the FIRST controller PcForRoute() returned, so
// one player got the resource pack and everyone else got nothing.
// An empty route means DIFFERENT things in the two modes, and conflating them leaked unlocks:
//
//   multiplayer = false  the whole server IS one slot -> everyone, which is the point.
//   multiplayer = true   there is no such thing as a server-wide item. An empty route here means
//                        something wrote to the ROOT mailbox instead of ipc\<CharacterName> - a
//                        misconfiguration. Item APPLICATION refuses those outright (see
//                        PollMailbox); this guard just makes sure no stray effect can fan out
//                        server-wide in per-player mode either.
static bool RootMailboxIsShared() { return !g_multiplayer; }

// A disconnected player's controller LINGERS in PlayerControllerList, and its survivor name is
// never cleared - so RouteFor() still matches it (see DoGreetJoiners, which learned this the hard
// way). Anything that "delivers to a route" must therefore skip it, or the delivery lands on a
// dead player state: the engram grant VERIFIES as successful against that state, the log reads
// granted=1, the live survivor gets nothing, and Reassert is permanently satisfied so it never
// retries. A live player has a NetConnection; a lingering one does not.
static bool IsLivePc(AShooterPlayerController* pc) {
    return pc && pc->NetConnectionField() != nullptr;
}

static std::vector<AShooterPlayerController*> PcsForRoute(const std::string& route) {
    std::vector<AShooterPlayerController*> out;
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) return out;
    const bool everyone = route.empty() && RootMailboxIsShared();
    for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (!IsLivePc(pc)) continue;                          // lingering disconnected controller
        if (route.empty()) {
            out.push_back(pc);
            if (!everyone) break;                             // misconfigured root -> contain it
            continue;
        }
        if (RouteFor(pc) == route) { out.push_back(pc); break; }  // that survivor only
    }
    return out;
}

// Is this route ready to RECEIVE? Connected is not enough - a survivor sitting on the respawn
// screen has a controller but no character, so an engram grant lands on a body that isn't there
// and every filler effect has nowhere to go. "_unnamed" is never ready: it is SanitizeRoute's
// fallback for a name it could not read, so it names nobody and must never be delivered to.
static bool RouteReady(const std::string& route) {
    if (route == "_unnamed") return false;
    for (auto* pc : PcsForRoute(route))      // already skips lingering disconnected controllers
        if (pc->GetPlayerCharacter()) return true;
    return false;
}

static AShooterPlayerController* PcForRoute(const std::string& route) {
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) return nullptr;
    for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (!pc) continue;
        if (route.empty() || RouteFor(pc) == route) return pc;
    }
    return nullptr;
}
// every route that should receive global check reports (boss kills) / milestone scans:
// solo = {""}; multiplayer = all routes ever persisted + everyone connected right now.
static std::vector<std::string> KnownRoutes() {
    if (!g_multiplayer) return { "" };
    std::set<std::string> names;
    for (auto& n : g_state->Players()) if (!n.empty()) names.insert(n);
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (world) for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (pc) { std::string r = RouteFor(pc); if (!r.empty()) names.insert(r); }
    }
    return { names.begin(), names.end() };
}
// hook -> tick event queue for the collective counters ("<kind>\t<route>" per line).
// The server's maximum WILD creature level. ARK derives it from the difficulty: max = value * 30,
// which is 30 on a stock single-player setting and 150 on the difficulty most servers run. Read it
// rather than hardcoding, so a "high level" check means the same thing everywhere.
//
// If it cannot be read we fall back LOW on purpose. An over-easy check is a shrug; an impossible
// one strands whatever item the fill put behind it.
static float ReadDifficultySEH() {                   // POD-only: __try needs nothing to unwind
    float d = 0.f;
    __try {
        AShooterGameMode* gm = ArkApi::GetApiUtils().GetShooterGameMode();
        if (gm) {
            d = gm->OverrideOfficialDifficultyField();
            if (d <= 0.f) d = gm->DifficultyValueField();
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) { d = 0.f; }
    return d;
}
// ---- the map's REAL GPS transform, read from the game instead of measured ------------------------
// ARK converts world coordinates to the lat/lon on your compass with four numbers the map itself
// carries (APrimalWorldSettings). Our exploration polygons live in world coordinates, so authoring
// them from a drawn map needs exactly this conversion:
//
//     lat = (y - LatitudeOrigin) / LatitudeScale        -> divisor = Scale, shift = -Origin/Scale
//
// The Island's are 8000 and -400000, which is the 8000/50 pair we had measured against the
// obelisks - so this reproduces the hand-calibrated answer and removes the need to fly to corners
// on every new map. POD-only reads, so the SEH block has nothing to unwind.
struct MapGeo { float latOrigin, latScale, lonOrigin, lonScale; bool over; int src; bool got; };
// Two ways to reach APrimalWorldSettings, because the first can be null early or on a streaming
// level: UWorld::GetWorldSettings, then the player's own AActor::GetWorldSettings. `got` means we
// READ the struct - the values may still be zero, which is itself the answer (see DumpMapGeo).
static MapGeo ReadMapGeoSEH(AActor* actor) {
    MapGeo g = {0.f, 0.f, 0.f, 0.f, false, 0, false};
    __try {
        AWorldSettings* ws = nullptr;
        UWorld* w = ArkApi::GetApiUtils().GetWorld();
        if (w) { ws = w->GetWorldSettings(false, false); if (ws) g.src = 1; }
        if (!ws && actor) { ws = actor->GetWorldSettings(); if (ws) g.src = 2; }
        if (ws) {
            APrimalWorldSettings* p = static_cast<APrimalWorldSettings*>(ws);
            g.latOrigin = p->LatitudeOriginField();
            g.latScale  = p->LatitudeScaleField();
            g.lonOrigin = p->LongitudeOriginField();
            g.lonScale  = p->LongitudeScaleField();
            g.over      = p->bOverrideLongitudeAndLatitudeField();
            g.got       = true;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) { g.got = false; }
    return g;
}
static void DumpMapGeo(AActor* actor) {
    static bool done = false;
    if (done) return;
    MapGeo g = ReadMapGeoSEH(actor);
    if (!g.got) {
        DebugLog("MAPGEO could not reach APrimalWorldSettings (world and controller both refused)");
        return;
    }
    // Log the raw values ALWAYS. Zeros here are a real finding - it means this map leaves the
    // override fields blank and ARK is using built-in defaults, which no amount of retrying fixes.
    DebugLog("MAPGEO raw src=" + std::to_string(g.src) +
             " override=" + std::string(g.over ? "true" : "false") +
             " latScale=" + std::to_string(g.latScale) + " latOrigin=" + std::to_string(g.latOrigin) +
             " lonScale=" + std::to_string(g.lonScale) + " lonOrigin=" + std::to_string(g.lonOrigin));
    if (g.latScale == 0.f || g.lonScale == 0.f) {
        DebugLog("MAPGEO scale is zero - this map does not fill the world-settings fields, so the "
                 "transform has to come from /dumppos samples instead (--calib).");
        return;
    }
    done = true;
    const double latDiv = g.latScale, latShift = -(double)g.latOrigin / g.latScale;
    const double lonDiv = g.lonScale, lonShift = -(double)g.lonOrigin / g.lonScale;
    DebugLog("MAPGEO map=" + (g_mapKey.empty() ? std::string("?") : g_mapKey) +
             " -> lat divisor=" + std::to_string(latDiv) + " shift=" + std::to_string(latShift) +
             " | lon divisor=" + std::to_string(lonDiv) + " shift=" + std::to_string(lonShift));
    std::ofstream f(PluginDir() / "ArkAP_map_geo.json");
    if (f) f << "{\"map\": \"" << g_mapKey << "\", \"lat_scale\": " << g.latScale
             << ", \"lat_origin\": " << g.latOrigin << ", \"lon_scale\": " << g.lonScale
             << ", \"lon_origin\": " << g.lonOrigin << ", \"divisor\": " << latDiv
             << ", \"shift\": " << latShift << "}" << std::endl;
}

static int MaxWildLevel() {
    static int cached = 0;
    if (cached) return cached;
    float d = ReadDifficultySEH();
    cached = (d > 0.f) ? (int)(d * 30.f + 0.5f) : 30;   // safe floor - stock difficulty
    if (cached < 5) cached = 30;
    DebugLog("MAXWILD level=" + std::to_string(cached) +
             " (difficulty=" + std::to_string(d) + ")");
    return cached;
}

// A wild creature's level as a PERCENTAGE of this server's maximum. AbsoluteBaseLevel is the level
// it spawned at, before any taming bonus levels, which is the number a player recognises.
static void ReportLevelMilestones(APrimalDinoCharacter* dino, const std::string& route,
                                  const char* hiTag, const char* vhiTag) {
    if (!dino) return;
    int lvl = dino->AbsoluteBaseLevelField();
    if (lvl <= 0) return;
    const double pct = (double)lvl / (double)MaxWildLevel();
    auto fire = [&](const char* tag) {
        auto it = g_tables.milestone_tag_to_loc.find(tag);
        if (it != g_tables.milestone_tag_to_loc.end()) ReportLocation(route, it->second);
    };
    if (pct >= 0.50) { DebugLog("LEVEL " + std::to_string(lvl) + "/" + std::to_string(MaxWildLevel()) +
                                " -> " + hiTag); fire(hiTag); }
    if (pct >= 0.80) fire(vhiTag);
}

static void QueueCountEvent(const char* kind, const std::string& route) {
    std::ofstream f(PluginDir() / "events_queue.jsonl", std::ios::app);
    if (f) f << kind << "\t" << route << "\n";
}

// hook workers (objects live here so the hook's __try has nothing to unwind)
static void DoNoteHook(AShooterPlayerController* pc, int idx,
                       void(*orig)(AShooterPlayerController*, int)) {
    orig(pc, idx);
    std::string route = RouteFor(pc);
    DebugLog("HOOK note idx=" + std::to_string(idx) + (route.empty() ? "" : " by=" + route));
    std::ofstream f(PluginDir() / "note_queue.jsonl", std::ios::app);
    if (f) f << idx << "\t" << route << "\n";

    // CALIBRATION FOR FREE. A map's world-unit-per-degree transform is not exposed by the game
    // (APrimalWorldSettings leaves the fields zero unless a map overrides them), so it has to be
    // measured. Explorer notes are perfect reference points: the wiki publishes each note's lat/lon,
    // and picking one up tells us the world coordinate of that exact spot. Every note a player
    // collects is therefore a calibration sample, at no cost to them.
    // tools/calibrate_from_notes.py joins these against the published coordinates.
    if (pc) {
        FVector p = ArkApi::GetApiUtils().GetPosition(pc);
        DebugLog("NOTEPOS idx=" + std::to_string(idx) +
                 " x=" + std::to_string((long long)p.X) +
                 " y=" + std::to_string((long long)p.Y));
        std::ofstream g(PluginDir() / "ArkAP_note_positions.jsonl", std::ios::app);
        if (g) g << "{\"note_index\": " << idx << ", \"x\": " << (long long)p.X
                 << ", \"y\": " << (long long)p.Y << "}" << std::endl;
    }
}

// ----------------------------------------------------------------- hooks
DECLARE_HOOK(AShooterPlayerState_ServerUnlockEngram, void, AShooterPlayerState*, TSubclassOf<UPrimalItem>, bool, bool);
DECLARE_HOOK(AShooterPlayerController_ServerUnlockPerMapExplorerNote_Implementation, void, AShooterPlayerController*, int);
DECLARE_HOOK(APrimalDinoCharacter_TameDino, void, APrimalDinoCharacter*, AShooterPlayerController*, bool, int, bool, bool, bool);
DECLARE_HOOK(APrimalStructureItemContainer_SupplyCrate_BeginPlay, void, APrimalStructureItemContainer_SupplyCrate*);
DECLARE_HOOK(APrimalDinoCharacter_Die, bool, APrimalDinoCharacter*, float, FDamageEvent*, AController*, AActor*);
DECLARE_HOOK(AShooterCharacter_Die, bool, AShooterCharacter*, float, FDamageEvent*, AController*, AActor*);
DECLARE_HOOK(APrimalDinoCharacter_DoMate, void, APrimalDinoCharacter*, APrimalDinoCharacter*);
DECLARE_HOOK(AShooterGameMode_Logout, void, AShooterGameMode*, AController*);
void ForgetGreeted(void* pc);                                    // defined with the greeter below

// dino name tag as std::string ("" on fault). Has FString objects -> kept out of any __try.
// Is this DinoNameTag one our kill/tame checks actually key on?
static bool KnownDinoTag(const std::string& t) {
    return g_killTagToLoc.count(t) || g_tameTagToItem.count(t) || g_tameTagToTameLoc.count(t);
}
// Kraken's Better Dinos (+ similar mods) REPLACE vanilla dinos with classes whose DinoNameTag is
// prefixed BD / BD_ / BDBionic (e.g. "BD_Dilo", "BDBionicAnkylo") - so a mod dino's kill/tame logs
// "mapped=0" and the check never fires (and lock_taming wouldn't gate it). If the raw tag isn't a
// known check tag but the prefix-stripped form IS a real vanilla tag, use that. Only remaps when it
// resolves to an existing vanilla tag, so genuinely-new mod creatures keep their own tag (and stay
// visible in the logs for harvesting). No vanilla ARK tag starts with "BD", so this shadows nothing.
static std::string CanonDinoTag(const std::string& raw) {
    if (raw.empty() || KnownDinoTag(raw)) return raw;
    // longest first. "Bionic" catches Kraken's Tek variants (BionicRaptor/BionicStego/...). NOT
    // "Mega" - MegaRaptor/MegaRex are the VANILLA alphas, handled by their own checks.
    static const std::string prefixes[] = { "BDBionic", "BD_", "BD", "Bionic" };
    for (const std::string& p : prefixes) {
        if (raw.size() > p.size() && raw.compare(0, p.size(), p) == 0) {
            std::string s = raw.substr(p.size());
            if (KnownDinoTag(s)) return s;
        }
    }
    return raw;
}
static std::string DinoTag(APrimalDinoCharacter* dino) {
    FString fs;
    dino->DinoNameTagField().ToString(&fs);
    return CanonDinoTag(fs.ToString());
}

// Whose tame is this? The game calls TameDino with ForPC = NULL for anything that is not a player
// actively taming a wild creature - a BABY HATCHING/BIRTHING above all, but also a cryopod or soul
// ball release and an admin tame. RouteFor(null) is "", and in multiplayer "" is the ROOT mailbox,
// which owns nothing - so the gate refused every baby with "Taming is locked for this creature",
// even though the player had tamed both parents. (Solo never saw it: there "" IS the player.)
// Attribute by the dino's TEAM instead, exactly how kills are already attributed.
static AShooterPlayerController* TamePcFor(APrimalDinoCharacter* dino,
                                           AShooterPlayerController* forPc) {
    if (forPc || !dino) return forPc;
    return PcForTeam(dino->TargetingTeamField());
}
// gate worker (objects live here, not in the __try-bearing hook). Returns true if the tame must be BLOCKED.
static bool DoTameGate(APrimalDinoCharacter* dino, AShooterPlayerController* forPc) {
    std::string tag = DinoTag(dino);
    if (tag.empty()) return false;
    std::string route = RouteFor(TamePcFor(dino, forPc));
    DebugLog("TAME tag=" + tag + (route.empty() ? "" : " by=" + route) +
             (forPc ? "" : " (no controller: birth/release, attributed by team)"));
    { std::ofstream f(PluginDir() / "dino_queue.jsonl", std::ios::app); if (f) f << tag << "\n"; }
    auto it = g_tameTagToItem.find(tag);
    if (it == g_tameTagToItem.end()) return false;              // not a tracked species
    // Still nobody to attribute it to (the owning player is offline, or the team is a tribe with
    // no one connected). We cannot fairly gate a tame with no owner, and refusing it strands a
    // baby the player has already earned - so allow it and say why.
    if (g_multiplayer && route.empty()) {
        DebugLog("TAME tag=" + tag + " has no attributable owner - ALLOWED (never refuse an "
                 "unowned tame; in multiplayer the root route owns nothing by design)");
        return false;
    }
    return !g_state->HasItem(route, it->second);   // tracked + not unlocked FOR THIS PLAYER
}

// --- engram GATE --- (identify whose learn this is: the controller owning this player state)
static std::string RouteForPlayerState(AShooterPlayerState* ps) {
    if (!g_multiplayer || !ps) return "";
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) return "";
    for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (pc && pc->GetShooterPlayerState() == ps) return RouteFor(pc);
    }
    return "";
}
void Hook_AShooterPlayerState_ServerUnlockEngram(AShooterPlayerState* _this,
        TSubclassOf<UPrimalItem> forItemEntry, bool bNotify, bool bForce) {
    if (!g_applying) {                            // player-initiated learn -> gate it
        UClass* cls = forItemEntry.uClass;
        auto it = g_engramClassToItem.find(cls);
        if (it != g_engramClassToItem.end() && !g_state->HasItem(RouteForPlayerState(_this), it->second)) {
            ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), L"That engram is still locked.");
            return;                               // blocked: AP hasn't granted this engram to this player
        }
    }
    AShooterPlayerState_ServerUnlockEngram_original(_this, forItemEntry, bNotify, bForce);
}

// --- dossier CHECK ---
void Hook_AShooterPlayerController_ServerUnlockPerMapExplorerNote_Implementation(
        AShooterPlayerController* _this, int ExplorerNoteIndex) {
    // entire body SEH-guarded (incl. the original call) so a fault can't crash the server.
    __try {
        DoNoteHook(_this, ExplorerNoteIndex,
                   AShooterPlayerController_ServerUnlockPerMapExplorerNote_Implementation_original);
    } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// chat helper - kept out of any __try (the FString temporary would require unwinding -> C2712).
static void ChatNotify(const wchar_t* msg) {
    ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), msg);
}

// queue a successful tame's tag so the tick reports the "Tamed: X" check (file-based, thread-safe).
static void DoQueueTameCheck(APrimalDinoCharacter* dino, AShooterPlayerController* forPc) {
    std::string tag = DinoTag(dino);
    if (tag.empty()) return;
    std::string route = RouteFor(TamePcFor(dino, forPc));   // births carry no controller; use the team
    { std::ofstream f(PluginDir() / "tame_check_queue.jsonl", std::ios::app);
      if (f) f << tag << "\t" << route << "\n"; }
    QueueCountEvent("tame", route);                 // collective count (drained on the game tick)
    ReportLevelMilestones(dino, route, "milestone_tamelevel_hi", "milestone_tamelevel_vhi");
}
static void QueueTameCheck(APrimalDinoCharacter* dino, AShooterPlayerController* forPc) {
    __try { DoQueueTameCheck(dino, forPc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- taming GATE (per-dino) ---
void Hook_APrimalDinoCharacter_TameDino(APrimalDinoCharacter* _this, AShooterPlayerController* ForPC,
        bool bIgnoreMaxTameLimit, int OverrideTamingTeamID, bool bPreventNameDialog,
        bool bSkipAddingTamedLevels, bool bSuppressNotifications) {
    if (g_trapDinos.count(_this)) {                 // trap-spawned dino -> never tameable
        ChatNotify(L"You are unable to tame trap dinos.");
        return;
    }
    bool blocked = false;
    __try { blocked = DoTameGate(_this, ForPC); } __except (EXCEPTION_EXECUTE_HANDLER) {}   // POD-only locals -> __try OK
    if (blocked) {                                  // tracked dino, AP hasn't unlocked it -> refuse the tame
        ChatNotify(L"Taming is locked for this creature.");
        return;
    }
    APrimalDinoCharacter_TameDino_original(_this, ForPC, bIgnoreMaxTameLimit, OverrideTamingTeamID,
                                           bPreventNameDialog, bSkipAddingTamedLevels, bSuppressNotifications);
    QueueTameCheck(_this, ForPC);                   // tame succeeded -> fire the "Tamed: X" check
}

// --- supply-crate / beacon / artifact gate + check (v34) ---
// Runs on each crate spawn (BeginPlay). One pass: harvest the class name, fire the artifact
// CHECK (discovery = streaming the container in), and GATE beacons/cave crates (destroy a
// locked one so it yields no loot). Returns true if the crate was destroyed (caller stops).
// Supply-crate classes are gated by EXACT name, but the DLC maps ship the same crate under a
// map-suffixed class: SupplyCrate_Level35_Double_ScorchedEarth_C is the Island's
// SupplyCrate_Level35_Double_C. Unrecognised = ungated, so on Scorched and Ragnarok every beacon
// dropped full loot regardless of whether the player held the access item.
//
// Rather than guess the DLC class names, strip a known map suffix and the trailing _C and match
// what remains. A crate that still does not match behaves exactly as before (ungated), so this can
// only ever gate MORE of what we already intended to gate - never something new and unrelated.
static std::string NormalizeCrateClass(const std::string& cls) {
    std::string s = cls;
    if (s.size() > 2 && s.compare(s.size() - 2, 2, "_C") == 0) s.erase(s.size() - 2);
    static const char* suffixes[] = {
        "_ScorchedEarth", "_Ragnarok", "_Aberration", "_Extinction", "_Genesis", "_Gen2",
        "_TheCenter", "_Valguero", "_CrystalIsles", "_LostIsland", "_Fjordur", "_SE",
    };
    for (const char* suf : suffixes) {
        size_t n = strlen(suf);
        if (s.size() > n && s.compare(s.size() - n, n, suf) == 0) { s.erase(s.size() - n); break; }
    }
    return s;
}
// normalized class -> access item, built alongside g_crateGateClassToItem
static std::unordered_map<std::string, int> g_crateGateNormToItem;

// A beacon's LEVEL NUMBER is map-relative and must never be matched across maps. The Island's six
// tiers are 3/15/25/35/45/60; Scorched Earth's are 3/15/30/45/55/70. So SupplyCrate_Level45 is the
// YELLOW drop on the Island and the PURPLE one on Scorched - matching by level (or by stripping the
// map suffix, which amounts to the same thing) silently gates the wrong colour behind the wrong
// item. Beacons therefore have to be listed explicitly in crates.json, per map.
//
// Cave and ocean crates are different: their tier is named, not numbered by level
// (SupplyCrate_Cave_QualityTier2_ScorchedEarth_C really is our Cave T2), so suffix matching is
// sound for them and is all this predicate allows through.
static bool CrateHasLevelToken(const std::string& cls) {
    size_t p = cls.find("Level");
    return p != std::string::npos && p + 5 < cls.size() &&
           cls[p + 5] >= '0' && cls[p + 5] <= '9';
}

static bool DoCrateHook(APrimalStructureItemContainer_SupplyCrate* crate) {
    // GetFullName on the instance = "<ClassName> <PackagePath>:<ObjName>"; first token is the class.
    FString fn; crate->GetFullName(&fn, nullptr);
    std::string full = fn.ToString();
    std::string name = full.substr(0, full.find(' '));
    if (name.empty()) return false;

    static std::set<std::string> seen;                         // harvest each class once
    if (seen.insert(name).second) {
        DebugLog("CRATE name=" + name);
        std::ofstream f(PluginDir() / "crate_queue.jsonl", std::ios::app);
        if (f) f << name << "\n";
    }

    // (artifacts are NOT checks: BeginPlay = world-load on a dedicated server, can't tell
    //  spawned from looted, so they'd auto-fire on every fresh game. Dropped.)
    // crates are WORLD objects (can't attribute a spawn to a player) -> unlocked once ANY player
    // has the access item; locked only while nobody does.
    int gateItem = 0;                                          // beacon / cave / deep-sea -> GATE
    auto gate = g_crateGateClassToItem.find(name);
    if (gate != g_crateGateClassToItem.end()) {
        gateItem = gate->second;
    } else if (!CrateHasLevelToken(name)) {                    // DLC variant of a NON-beacon crate
        auto alt = g_crateGateNormToItem.find(NormalizeCrateClass(name));
        if (alt != g_crateGateNormToItem.end()) gateItem = alt->second;
        static std::set<std::string> matchedLogged;
        if (matchedLogged.insert(name).second && gateItem)
            DebugLog("CRATE map-variant matched by suffix: " + name + " -> item " +
                     std::to_string(gateItem));
    }
    // BEAVER DAMS are not supply drops. Ragnarok's (and the Island's) Giant Beaver Dams are built
    // from SupplyCrateBaseBP_Instantaneous_DamLogs/DenLogs classes, so they arrive here looking
    // like beacons. They must never be gated: a locked crate is DESTROYED, and destroying dams
    // would delete the map's cementing paste supply. Excluded from the warning too, or every
    // Ragnarok log nags about a crate nobody should touch.
    bool isDam = name.find("DamLogs") != std::string::npos ||
                 name.find("DenLogs") != std::string::npos;
    // Warn only about SUPPLY crates. Artifact crates reach this hook too and are deliberately
    // ungated (they are not checks - see above), so flagging them would send people chasing a
    // non-problem.
    if (!gateItem && !isDam &&
        (name.rfind("SupplyCrate", 0) == 0 || name.rfind("SupplyCreate", 0) == 0)) {
        static std::set<std::string> ungatedLogged;
        if (ungatedLogged.insert(name).second)
            DebugLog("CRATE UNGATED (not in crates.json): " + name +
                     " - add this exact class to the right colour in crates.json");
    }
    if (gateItem && !g_state->HasItemAny(gateItem)) {
        static std::set<std::string> destroyedLogged;          // log each locked class once (these respawn constantly)
        if (destroyedLogged.insert(name).second) DebugLog("CRATE locked, destroying: " + name + " (further hidden)");
        crate->Destroy(false, true);                           // locked -> remove so it gives no loot
        return true;
    }
    return false;
}
// hook wrapper has no unwinding objects -> __try is legal here
void Hook_APrimalStructureItemContainer_SupplyCrate_BeginPlay(APrimalStructureItemContainer_SupplyCrate* _this) {
    APrimalStructureItemContainer_SupplyCrate_BeginPlay_original(_this);
    __try { DoCrateHook(_this); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- boss-kill CHECK (v35) ---
// Fires on any dino death; filters to boss classes by difficulty-agnostic substring
// (SpiderBoss/GorillaBoss/DragonBoss/Overseer) and reports that boss's location.
static void GrantTekForBoss(const std::string& baseTag);   // defined below (after ApplyItem helpers)

// passive harvest of real wild-dino Character_BP class names (ground truth for the
// randomize_dino_spawns spawn_classes.json). Every distinct class seen dying is logged once to
// ArkAP_dino_classes.jsonl - run 'cheat DestroyWildDinos' near spawn zones to harvest en masse.
static std::set<std::string> g_seenDinoClasses;
static std::string RawDinoTag(APrimalDinoCharacter* dino) {          // DinoNameTag, NOT canonicalized
    FString fs; dino->DinoNameTagField().ToString(&fs); return fs.ToString();
}
// name = class short; rawTag = the creature's DinoNameTag (may be modded, e.g. "BD_Dilo"). Records
// the tag + what CanonDinoTag resolves it to + whether that lands on a real kill/tame check, so a
// dump tells us at a glance which modded creatures still need a prefix/alias.
static void HarvestDinoClass(const std::string& name, const std::string& rawTag) {
    if (name.find("_Character_BP") == std::string::npos) return;   // only real dino BP classes
    if (!g_seenDinoClasses.insert(name).second) return;            // once each
    std::string canon = CanonDinoTag(rawTag);
    std::ofstream f(PluginDir() / "ArkAP_dino_classes.jsonl", std::ios::app);
    if (f) f << "{\"class\": \"" << name << "\", \"tag\": \"" << rawTag << "\", \"canon\": \""
             << canon << "\", \"mapped\": " << (KnownDinoTag(canon) ? "true" : "false") << "}\n";
}

static void DoBossDeath(APrimalDinoCharacter* dino, AActor* damageCauser) {
    FString fn; dino->GetFullName(&fn, nullptr);
    std::string full = fn.ToString();
    std::string name = full.substr(0, full.find(' '));          // class name prefix
    if (name.empty()) return;
    HarvestDinoClass(name, RawDinoTag(dino));                   // ground-truth spawn class + tag
    for (auto& b : g_bosses) {
        if (name.find(b.frag) != std::string::npos) {
            // Boss kills are the GOAL, not AP check locations (nothing gets stranded behind a hard
            // boss kill). Signal the defeat by base-tag to boss_out.jsonl in EVERY known route's
            // mailbox (boss fights are team efforts); the client counts required tags -> AP goal.
            DebugLog("BOSS-KILL name=" + name + " boss=" + b.baseTag + " -> boss_out.jsonl");
            for (auto& r : KnownRoutes()) {
                std::ofstream f(g_ipc->DirFor(r) / "boss_out.jsonl", std::ios::app);
                if (f) f << b.baseTag << "\n";
            }
            GrantTekForBoss(b.baseTag);                         // tek engrams unlock on any difficulty
            return;
        }
    }
    // first-kill-of-species CHECK: attribute by the damage causer's team (a player or their dino),
    // then route to the connected player on that team.
    AShooterPlayerController* killerPc = nullptr;
    if (damageCauser) {
        auto* c = static_cast<APrimalCharacter*>(damageCauser);   // player char or a tamed dino
        if (c) killerPc = PcForTeam(c->TargetingTeamField());
    }
    if (!killerPc) return;                                      // wild-on-wild / unattributable
    std::string route = RouteFor(killerPc);
    QueueCountEvent("kill", route);                             // collective kill count
    // alpha-predator kills (Alpha Raptor/Carno/Rex + ocean alphas) - class-fragment match, by-me only.
    for (auto& [frag, loc] : g_alphaFragToLoc) {
        if (name.find(frag) != std::string::npos) {
            DebugLog("ALPHA-KILL name=" + name + " loc=" + std::to_string(loc));
            ReportLocation(route, loc);
            break;                                              // still fall through to species kill
        }
    }
    static std::set<std::string> seenKills;                     // "route|tag" - first kill per player
    std::string tag = DinoTag(dino);
    if (!tag.empty()) {
        static std::set<std::string> seenAnyKill;          // tag-verify aid: log every distinct kill
        if (seenAnyKill.insert(tag).second)
            DebugLog("KILL tag=" + tag + " mapped=" + (g_killTagToLoc.count(tag) ? "1" : "0"));
    }
    if (!tag.empty() && g_killTagToLoc.count(tag) && seenKills.insert(route + "|" + tag).second) {
        std::ofstream f(PluginDir() / "kill_check_queue.jsonl", std::ios::app);
        if (f) f << tag << "\t" << route << "\n";
    }
    // harvest aid: a boss-looking death we didn't map (verify the real class string)
    if (name.find("Boss") != std::string::npos || name.find("Overseer") != std::string::npos)
        DebugLog("BOSS-DEATH unmatched name=" + name);
}
// --- breeding CHECK (collective) --- DoMate fires when two tamed dinos complete mating
// (fertilized egg species AND gestation species both pass through it; wild dinos never mate in
// vanilla, so every fire is a player breeding event). It may run once per PARTNER - counting only
// the female side keeps it to one event per pair. Logged for both genders to verify in the field.
static void DoBreedCount(APrimalDinoCharacter* dino) {   // objects here, SEH in the hook wrapper
    bool female = dino->bIsFemale().Get();
    DebugLog("BREED mate tag=" + DinoTag(dino) + " female=" + (female ? "1" : "0"));
    if (!female) return;
    // attribute to the tamed pair's owning team -> the connected player on that team.
    AShooterPlayerController* owner = PcForTeam(dino->TargetingTeamField());
    QueueCountEvent("breed", RouteFor(owner));
}
// Player left -> forget them so their NEXT join greets again. Detecting LEAVE is far more reliable
// than detecting join: the join hook never fired, and controller identity alone is unusable because
// the controller both LINGERS after a disconnect and can be REUSED on reconnect.
void Hook_AShooterGameMode_Logout(AShooterGameMode* _this, AController* Exiting) {
    ForgetGreeted((void*)Exiting);                               // pointer compare only - no deref
    DebugLog("LOGOUT -> greet state cleared (next join will be greeted)");
    AShooterGameMode_Logout_original(_this, Exiting);
}

void Hook_APrimalDinoCharacter_DoMate(APrimalDinoCharacter* _this, APrimalDinoCharacter* WithMate) {
    APrimalDinoCharacter_DoMate_original(_this, WithMate);
    if (!_this) return;
    __try { DoBreedCount(_this); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// a WILD creature killed by a player -> the high-level kill milestones (tamed ones do not count:
// you would just be killing your own bred stock).
static void DoDinoLevelKill(APrimalDinoCharacter* dino, AController* killer, AActor* causer) {
    if (!dino || dino->TargetingTeamField() >= 50000) return;   // >= 50000 = a tamed/tribe creature
    AShooterPlayerController* pc = nullptr;
    if (killer) pc = static_cast<AShooterPlayerController*>(killer);
    std::string route = pc ? RouteFor(pc) : std::string();
    ReportLevelMilestones(dino, route, "milestone_killlevel_hi", "milestone_killlevel_vhi");
}

bool Hook_APrimalDinoCharacter_Die(APrimalDinoCharacter* _this, float KillingDamage,
        FDamageEvent* DamageEvent, AController* Killer, AActor* DamageCauser) {
    bool ret = APrimalDinoCharacter_Die_original(_this, KillingDamage, DamageEvent, Killer, DamageCauser);
    __try { DoBossDeath(_this, DamageCauser); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    __try { DoDinoLevelKill(_this, Killer, DamageCauser); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    g_trapDinos.erase(_this);   // dead trap dino: drop the pointer so a reused address can't
                                // falsely flag a fresh legit dino as a trap
    return ret;
}

// --- DeathLink: broadcast when a player dies (unless WE killed them for an incoming link) ---
// The real Die() can unpossess the character, after which no controller reports it via
// GetPlayerCharacter() - resolving the route post-death silently yielded "" and dumped every
// multiplayer death into the ROOT death_out.jsonl (which no per-slot connector reads, so
// DeathLink went dead both ways). So resolve the route BEFORE Die() runs, while the controller
// still owns the character, and stash it for DoPlayerDeath. Game thread only; the Die hook is
// the sole writer/reader, and it consumes the value on the very next line.
static std::string g_dyingRoute;
static bool g_dyingRouteValid = false;
static void ResolveDyingRoute(AShooterCharacter* who) {
    g_dyingRoute.clear();
    g_dyingRouteValid = false;
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (world) for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (pc && pc->GetPlayerCharacter() == who) { g_dyingRoute = RouteFor(pc); g_dyingRouteValid = true; break; }
    }
}
// Work out WHAT killed the player, as one of the "kind" tags in locations.json.
//
// Two independent sources, in order of confidence:
//   1. the UDamageType class - environmental causes (drowning, heat, cold, falling, lava) name
//      themselves, so no guessing about the killer is needed;
//   2. the causer/killer actor's class - a creature. Alpha wins over carnivore (reusing the very
//      class fragments the alpha-KILL checks already match on), otherwise bIsCarnivore decides.
//
// ARK's exact DamageType class names are not documented anywhere reliable, so EVERY death logs its
// raw type string. If a cause never fires, the log names the string to add here - no guessing, and
// a miss costs one unmatched check rather than a wrong one.
static bool NameHas(const std::string& hay, const char* needle) {
    std::string h = hay, n = needle;
    for (auto& c : h) c = (char)std::tolower((unsigned char)c);
    for (auto& c : n) c = (char)std::tolower((unsigned char)c);
    return h.find(n) != std::string::npos;
}
static std::string FirstToken(std::string full) {
    return full.substr(0, full.find(' '));
}
// Read one of the dying player's stats. Called BEFORE the original Die() so the values are still
// live. POD-only so the SEH guard has nothing to unwind.
static float StatSEH(AShooterCharacter* who, EPrimalCharacterStatusValue::Type v) {
    float out = -1.f;
    __try { if (who) out = who->GetCurrentStatusValue(v); }
    __except (EXCEPTION_EXECUTE_HANDLER) { out = -1.f; }
    return out;
}

// Work out WHAT killed the player, as one of the "kind" tags in locations.json.
//
// The damage TYPE is nearly useless here: ARK reports the base "/Script/Engine.DamageType" for
// drowning rather than a named subclass (confirmed from a live log), so environmental causes are
// indistinguishable that way. What DOES distinguish them is the player's own stats at the moment
// of death - you drown with Oxygen at 0, starve with Food at 0, dehydrate with Water at 0.
//
// Order matters: a creature kill is identified from the causer and wins outright, because a player
// mauled by a Raptor may well also be starving. Stats come next, then the damage-type strings as a
// last resort in case some causes really do name themselves.
//
// Every death logs the full picture (type, causer, damage, and all four stats) so the remaining
// tags can be calibrated from real deaths instead of guessed at.
static std::string DeathKind(AShooterCharacter* who, FDamageEvent* ev, float killingDamage,
                             AController* killer, AActor* causer, std::string& detail) {
    std::string dmg;
    if (ev) {
        UClass* dt = ev->DamageTypeClassField().uClass;
        if (dt) dmg = ClassShortName(dt);
    }
    std::string causerName;
    if (causer) { FString fn; causer->GetFullName(&fn, nullptr); causerName = FirstToken(fn.ToString()); }
    if (causerName.empty() && killer) { FString fn; killer->GetFullName(&fn, nullptr); causerName = FirstToken(fn.ToString()); }

    const float o2   = StatSEH(who, EPrimalCharacterStatusValue::Oxygen);
    const float food = StatSEH(who, EPrimalCharacterStatusValue::Food);
    const float watr = StatSEH(who, EPrimalCharacterStatusValue::Water);
    const float temp = StatSEH(who, EPrimalCharacterStatusValue::Temperature);

    char nums[192];
    snprintf(nums, sizeof(nums), " dmg=%.1f O2=%.1f food=%.1f water=%.1f temp=%.1f",
             killingDamage, o2, food, watr, temp);
    detail = "type=" + (dmg.empty() ? std::string("?") : dmg) +
             " causer=" + (causerName.empty() ? std::string("?") : causerName) + nums;

    // 1. a creature did it - the most specific answer available
    if (!causerName.empty()) {
        for (auto& af : g_alphaFragToLoc)
            if (NameHas(causerName, af.first.c_str())) return "alpha";
        if (NameHas(causerName, "character_bp")) {      // only cast once it looks like a dino
            auto* dino = static_cast<APrimalDinoCharacter*>(causer);
            if (dino) return dino->bIsCarnivore().Get() ? "carnivore" : "herbivore";
        }
    }

    // 2. the player's own stats. A depleted bar is the cause; -1 means the read failed.
    if (o2   >= 0.f && o2   <= 0.5f) return "drowning";
    if (food >= 0.f && food <= 0.5f) return "starvation";
    if (watr >= 0.f && watr <= 0.5f) return "dehydration";
    if (temp >= -200.f && temp <   0.f) return "cold";
    if (temp <=  200.f && temp >  45.f) return "heat";

    // 3. damage-type strings, in case some causes DO name themselves
    if (NameHas(dmg, "drown"))                                                      return "drowning";
    if (NameHas(dmg, "lava") || NameHas(dmg, "volcan"))                             return "lava";
    if (NameHas(dmg, "fall") || NameHas(dmg, "landing"))                            return "falling";
    if (NameHas(dmg, "hypotherm") || NameHas(dmg, "cold") || NameHas(dmg, "freez")) return "cold";
    if (NameHas(dmg, "hypertherm") || NameHas(dmg, "heat") || NameHas(dmg, "burn")) return "heat";
    if (NameHas(dmg, "starv") || NameHas(dmg, "hunger"))                            return "starvation";
    if (NameHas(dmg, "dehydr") || NameHas(dmg, "thirst"))                           return "dehydration";

    // 4. nothing named it and no stat was empty: a big single hit with no attacker is a fall.
    //    Environmental ticks are small and repeated, so the damage size separates them.
    if (causerName.empty() && killingDamage >= 40.f) return "falling";

    return "";                                          // unknown: logged, never guessed
}

static void DoPlayerDeath(AShooterCharacter* who) {
    // which player's death is this? (their route decides which slot broadcasts the DeathLink)
    // pre-Die resolution wins; fall back to a post-Die sweep if it somehow didn't run.
    std::string route;
    if (g_dyingRouteValid) {
        route = g_dyingRoute;
    } else {
        UWorld* world = ArkApi::GetApiUtils().GetWorld();
        if (world) for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
            auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
            if (pc && pc->GetPlayerCharacter() == who) { route = RouteFor(pc); break; }
        }
    }
    if (g_multiplayer && route.empty())              // never silently share a death with everyone
        DebugLog("PLAYER death: route unresolved - death goes to the ROOT mailbox (shared)");
    if (route == "_unnamed") {                       // names nobody: the DeathLink would go nowhere
        DebugLog("PLAYER death: survivor name unreadable (_unnamed) - NOT broadcasting a DeathLink "
                 "and not counting the death, rather than sending it to a mailbox nobody reads");
        return;
    }
    auto sit = g_suppressDeathUntil.find(route);
    if (sit != g_suppressDeathUntil.end() && std::time(nullptr) < sit->second) return;   // incoming-link kill -> don't echo
    QueueCountEvent("death", route);
    DebugLog("PLAYER death -> death_out.jsonl" + (route.empty() ? std::string() : " route=" + route));
    std::ofstream f(g_ipc->DirFor(route) / "death_out.jsonl", std::ios::app);
    if (f) f << "{\"death\":1}\n";
}
// Classify + report BEFORE the original Die runs: the causer and the damage event are still valid
// then, the same reason the dying route is resolved early.
static void DoDeathCheck(AShooterCharacter* who, FDamageEvent* ev, float killingDamage,
                         AController* killer, AActor* causer) {
    if (g_deathKindToLoc.empty()) return;
    std::string detail;
    std::string kind = DeathKind(who, ev, killingDamage, killer, causer, detail);
    std::string route = g_dyingRouteValid ? g_dyingRoute : std::string();
    auto it = g_deathKindToLoc.find(kind);
    DebugLog("DEATH " + detail + " -> kind=" + (kind.empty() ? "(unmatched)" : kind) +
             (it != g_deathKindToLoc.end() ? " loc=" + std::to_string(it->second) : ""));
    if (it != g_deathKindToLoc.end()) ReportLocation(route, it->second);
}

bool Hook_AShooterCharacter_Die(AShooterCharacter* _this, float KillingDamage,
        FDamageEvent* DamageEvent, AController* Killer, AActor* DamageCauser) {
    __try { ResolveDyingRoute(_this); } __except (EXCEPTION_EXECUTE_HANDLER) {}   // BEFORE Die: controller still owns the character
    __try { DoDeathCheck(_this, DamageEvent, KillingDamage, Killer, DamageCauser); }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
    bool ret = AShooterCharacter_Die_original(_this, KillingDamage, DamageEvent, Killer, DamageCauser);
    __try { DoPlayerDeath(_this); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return ret;
}

// ----------------------------------------------------------------- reporting / applying
void ReportLocation(const std::string& route, int loc_id) {
    if (loc_id == 0) { DebugLog("REPORT skip: loc_id=0 (unmapped)"); return; }
    // "_unnamed" is SanitizeRoute's fallback when a survivor name could not be read (mid-spawn,
    // respawn screen, a controller half torn down). /connect already refuses to bind a session
    // there; reporting a CHECK there was still possible, and it was worse than useless - the id
    // got marked checked against a route no AP client reads, the line went to a phantom
    // ipc\_unnamed mailbox, and MailboxRoutes then polled that folder for the rest of the boot.
    // Live-hit: two kill-level milestones vanished this way in one session. Drop it unmarked so
    // the rescan can re-report it under the real survivor.
    // NO OWNER, NO REPORT (multiplayer). "_unnamed" is SanitizeRoute's fallback for a name it
    // could not read; "" is the ROOT mailbox, which in multiplayer belongs to nobody - PollMailbox
    // already refuses to APPLY items there for the same reason. A check written to either is
    // simply lost, and marking it would stop the real survivor ever re-reporting it. Live case:
    // a baby claimed with no resolvable owner logged `REPORT loc=8753050 ->` with no route at all.
    if (g_multiplayer && (route.empty() || route == "_unnamed")) {
        DebugLog("REPORT skip: loc=" + std::to_string(loc_id) + " has NO resolvable survivor "
                 "(route would be " + (route.empty() ? "the shared root" : "_unnamed") +
                 ") - NOT marked, will re-report once it can be attributed");
        return;
    }
    if (!MapAllowsLoc(loc_id)) {
        // Deliberately NOT marked checked: on a cluster the player may travel to the map that owns
        // this location, and it has to fire there. But the rescan retries it forever, so say it
        // once per route per boot instead of once per attempt (one live log repeated a single id
        // a dozen times in three seconds).
        static std::set<std::pair<std::string, int>> skipLogged;
        if (skipLogged.insert({ route, loc_id }).second)
            DebugLog("REPORT skip: loc=" + std::to_string(loc_id) + " belongs to another map (running " +
                     (g_mapKey.empty() ? std::string("?") : g_mapKey) + ")" +
                     (route.empty() ? "" : " [" + route + "]") + " - further skips of this id quiet");
        return;
    }
    if (g_state->AlreadyChecked(route, loc_id)) return;   // quiet: per-player dedup
    g_state->MarkChecked(route, loc_id);
    g_ipc->ReportCheck(route, loc_id);
    DebugLog("REPORT loc=" + std::to_string(loc_id) +
             (route.empty() ? "" : " [" + route + "]") + " -> checks_out.jsonl");
    if (g_mode == Mode::Offline) {
        int item = g_state->OfflineGrantFor(loc_id, g_tables);
        if (item) ApplyItem(route, item, "offline");
    }
}

// A refused grant is USUALLY TEMPORARY, so we never stop retrying - we only stop shouting.
//
// ServerUnlockEngram(bForce=true) bypasses the level and engram-point costs. It does NOT bypass
// an engram's PREREQUISITE ENGRAMS. Tranq Arrow needs Stone Arrow + Narcotic, Narcotic needs
// Mortar And Pestle, Refined Tranq Dart needs Tranq Dart, Tripwire C4 needs C4. Until the player
// receives those - separate AP items that may arrive much later - the game refuses, and there is
// nothing wrong with the class, the registry or the data.
//
// v151 gave up after 10 verified failures and blacklisted the class permanently, on the theory
// that a refusal meant "no engram entry behind this class". That was wrong: a live log showed 10
// give-ups while `registry fallback: recovered=2` proved at least 8 of them came from real engram
// entries. Worse, the blacklist made the failure PERMANENT - when the prerequisite finally
// arrived, the retry that would have fixed it had been switched off. Retrying forever was noisy;
// blacklisting was silently unrecoverable, which is worse.
//
// So: retry every tick as before, but log on a widening schedule (1, 2, 3, then ~x3 each time) so
// a stuck engram stays visible without the 1,001,589-line / 238 MB flood that started all this.
static std::map<UClass*, int> g_grantFailures;
static bool ShouldLogGrantFailure(int n) {
    if (n <= 3) return true;
    for (int t = 10; t <= 1000000000; t *= 3) if (n == t) return true;
    return false;
}

// Learned engrams live in TWO places: the survivor's persistent stats (what HasEngram reads, and
// what survives death) and the craftable blueprints inside the CURRENT character's inventory. A
// respawn builds a new inventory, so every blueprint has to be pushed again for the new body.
// Keyed on the character pointer, which is only ever compared, never dereferenced.

// Say WHY an unlock did not take, in facts rather than a guess. Every number here comes from the
// game's own engram entry, so the log names the actual blocker instead of "almost always a
// prerequisite". MeetsEngramRequirements is asked three ways to separate the causes: the full
// check, level-only, and the full check with prerequisites waived - whichever flips tells us which
// rule is doing the blocking.
static void DiagnoseGrantFailure(AShooterPlayerState* ps, UClass* engramClass,
                                 const std::string& route) {
    const std::string who = route.empty() ? "" : " [" + route + "]";
    auto eit = g_classToEntry.find(engramClass);
    if (eit == g_classToEntry.end() || !eit->second) {
        DebugLog("  WHY: no UPrimalEngramEntry is registered for this class, so ServerUnlockEngram "
                 "had nothing to unlock - the AP item can never work" + who);
        return;
    }
    UPrimalEngramEntry* e = eit->second;
    FString nm; e->GetEngramName(&nm);
    const int  needLvl = e->GetRequiredLevel();
    const int  haveLvl = ps->GetCharacterLevel();
    const int  needPts = e->GetRequiredEngramPoints();
    const int  havePts = ps->FreeEngramPointsField();
    const bool manual  = e->bCanBeManuallyUnlocked().Get();
    const bool full    = e->MeetsEngramRequirements(ps, false, false);
    const bool lvlOnly = e->MeetsEngramRequirements(ps, true,  false);
    const bool noPre   = e->MeetsEngramRequirements(ps, false, true);
    const bool chain   = e->MeetsEngramChainRequirements(ps);
    DebugLog("  WHY: \"" + nm.ToString() + "\"" + who +
             " level=" + std::to_string(haveLvl) + "/" + std::to_string(needLvl) +
             " points=" + std::to_string(havePts) + "/" + std::to_string(needPts) +
             " manuallyUnlockable=" + (manual  ? "1" : "0") +
             " meetsAll="           + (full    ? "1" : "0") +
             " levelOK="            + (lvlOnly ? "1" : "0") +
             " okIfPrereqsIgnored=" + (noPre   ? "1" : "0") +
             " prereqChainOK="      + (chain   ? "1" : "0"));
    if (!manual)
        DebugLog("  WHY: bCanBeManuallyUnlocked=0 - the game forbids unlocking this one directly "
                 "(typically a Tek engram gated behind a boss)");
    if (!lvlOnly)
        DebugLog("  WHY: CHARACTER LEVEL is short - needs " + std::to_string(needLvl) +
                 ", player is " + std::to_string(haveLvl));
    if (!chain) {
        TArray<TSubclassOf<UPrimalEngramEntry>> pre;
        e->GetAllChainedPreReqs(ps, &pre);
        std::string list;
        for (int i = 0; i < pre.Num() && i < 12; ++i)
            if (pre[i].uClass)
                list += (list.empty() ? "" : ", ") + ClassShortName(pre[i].uClass);
        DebugLog("  WHY: PREREQUISITE CHAIN not satisfied. Chain: " +
                 (list.empty() ? std::string("(the game reported none)") : list));
    }
    if (full && manual)
        DebugLog("  WHY: the game says every requirement IS met, yet HasEngram is still false "
                 "after the call - that points at the entry/class pairing, not at the player");
}

// grant an engram: route "" = every connected player (solo/shared); otherwise only that player.
// Returns TRUE when the engram is now known to at least one target (freshly granted, or they
// already had it), FALSE when there was NOBODY to grant it to - which is the normal case for an
// item that arrives while its owner is dead or logged out. Reassert picks those up on respawn, so
// the caller only needs the answer to say so in the log.
static bool GrantEngramToPlayers(const std::string& route, UClass* engramClass,
                                 bool* refusedOut = nullptr) {
    if (!engramClass) return false;
    TSubclassOf<UPrimalItem> engram; engram.uClass = engramClass;   // rebuild from the class
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) { DebugLog("grant: no world"); return false; }
    auto& world_players = world->PlayerControllerListField();
    int seen = 0, granted = 0, had = 0, stale = 0, nobody = 0, refused = 0;
    for (TWeakObjectPtr<APlayerController> wpc : world_players) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (!pc) continue;
        // Never grant to a lingering disconnected controller - it would verify as a success
        // against a dead player state and the real survivor would never see the engram.
        if (!IsLivePc(pc)) {
            if (route.empty() || RouteFor(pc) == route) ++stale;
            continue;
        }
        // An empty route here is INTENTIONALLY server-wide: the only caller that uses one is the
        // Tek grant on a boss kill, which is a team reward rather than a per-slot item. v135 added
        // a guard that capped this at one player to contain mis-routed items - wrong place for it,
        // since it also broke Tek in multiplayer. Mis-routed ITEMS are now refused in PollMailbox
        // before they can ever reach here, so this can go back to granting everyone.
        if (!route.empty() && RouteFor(pc) != route) continue;   // multiplayer: this slot's player only
        ++seen;
        auto* ps = pc->GetShooterPlayerState();
        if (!ps) continue;
        // NO BODY, NO GRANT - wait for a spawned survivor. Nothing here needs the character
        // any more (see below), so this is purely the rule that items wait for a live, alive
        // player rather than landing on a corpse or a respawn screen; the mailbox hold applies the
        // same rule one level up. Reassert retries every tick, so deferring costs a second.
        //
        // NB the old justification for this check - "ServerUnlockEngram also pushes a craftable
        // into the character's inventory and HasEngram only sees the persistent half" - was wrong.
        // There is no inventory half.
        auto* body = pc->GetPlayerCharacter();
        if (!body) { ++nobody; continue; }
        // ONE CALL, NOTHING ELSE. ServerUnlockEngram records the engram in the survivor's
        // persistent stats, which is what makes it appear learned and craftable - that is all an
        // engram is. It does NOT need a blueprint item pushed alongside it.
        //
        // v156 added AddEngramBlueprintToPlayerInventory on the theory that the unlock's "inventory
        // half" was going missing. There is no inventory half. A blueprint ITEM is a separate,
        // lootable thing (UPrimalItem keeps bIsBlueprint and bIsEngram as different flags), so that
        // call was minting a real item every time it ran - which is what buried a player's
        // inventory in duplicate IED blueprints. v75 of this plugin never made that call and worked
        // for an entire long-term run; ArkServerApi's own AllEngrams plugin does not make it either
        // (vendor/ASE-Plugins/AllEngrams: HasEngram check, then ServerUnlockEngram, done).
        if (ps->HasEngram(engram)) { ++had; continue; }
        g_applying = true;
        ps->ServerUnlockEngram(engram, true, true);
        g_applying = false;
        // verify it landed. Silent failures here are permanent for the player (ApplyItem dedups
        // by id), so a refusal has to end up in the log rather than nowhere. Reassert retries it
        // every tick regardless, which covers the "player not fully spawned yet" case.
        if (!ps->HasEngram(engram)) {
            ++refused;
            int n = ++g_grantFailures[engramClass];
            if (ShouldLogGrantFailure(n)) {
                DebugLog("grant REFUSED for " + ClassShortName(engramClass) +
                         (route.empty() ? "" : " [" + route + "]") +
                         " (attempt " + std::to_string(n) + ") - will keep retrying");
                DiagnoseGrantFailure(ps, engramClass, route);
            }
            continue;                       // nothing was granted - do not count it as one
        }
        g_grantFailures.erase(engramClass);  // it took; forget any earlier transient failure
        ++granted;
    }
    // NAME what was granted. Without it a reassert grant is an anonymous line, and "did Lurch ever
    // actually get the Grappling Hook?" could not be answered from the log at all - the APPLY line
    // said engram=1, no grant line followed (he was dead), and two nameless grants fired when he
    // respawned. Which two was unknowable.
    if (granted > 0)   // quiet: only log when we actually unlocked something
        DebugLog("grant: " + ClassShortName(engramClass) +
                 " controllers=" + std::to_string(seen) +
                 " already=" + std::to_string(had) + " granted=" + std::to_string(granted) +
                 (stale ? " (skipped " + std::to_string(stale) + " disconnected)" : "") +
                 (nobody ? " (skipped " + std::to_string(nobody) + " with no body)" : "") +
                 (route.empty() ? "" : " [" + route + "]"));
    if (refusedOut && refused) *refusedOut = true;
    return granted > 0 || had > 0;
}

// trap effect: SpawnDino "<blueprint>" <distance> <yOffset> 5000 <level> = a WILD dino <distance>
// units in front of the TARGET player, dropped from Z+5000. Pack members spread via yOffset.
// Returns false when the target player isn't in-world yet (caller queues a retry).
static bool DoSpawnTrapOn(AShooterPlayerController* pc, const TrapSpawn& t) {
    if (!pc) return false;
    AShooterCharacter* ch = pc->GetPlayerCharacter();
    if (!ch || ch->IsDead()) return false;             // in-world AND alive (dead -> retry after respawn)

    // class leaf ("Raptor_Character_BP") from the Blueprint'...' path - used to find the spawns.
    std::string core = t.blueprint;
    { auto q = core.find('\''); if (q != std::string::npos) { core = core.substr(q + 1);
        if (!core.empty() && core.back() == '\'') core.pop_back(); } }
    std::string leaf = core.substr(core.rfind('.') == std::string::npos ? 0 : core.rfind('.') + 1);

    // snapshot same-class dinos ALREADY nearby, so innocent wilds don't get tagged as traps.
    FVector ppos = ArkApi::GetApiUtils().GetPosition(pc);
    const float scanRange = static_cast<float>(t.distance + 8000);   // covers forward 2500 + Z 5000 drop
    std::set<AActor*> preexisting;
    for (AActor* a : ArkApi::GetApiUtils().GetAllActorsInRange(ppos, scanRange, EServerOctreeGroup::DINOPAWNS)) {
        auto* d = static_cast<APrimalDinoCharacter*>(a);
        if (!d) continue;
        FString fn; d->GetFullName(&fn, nullptr);
        if (fn.ToString().find(leaf) != std::string::npos) preexisting.insert(a);
    }

    int count = t.count > 0 ? t.count : 1;
    for (int i = 0; i < count; ++i) {
        int yoff = (i - count / 2) * 350;              // spread the pack sideways
        std::string c = "SpawnDino \"" + t.blueprint + "\" " + std::to_string(t.distance) + " " +
                        std::to_string(yoff) + " 5000 " + std::to_string(t.level);   // Z+5000 = drop from sky
        FString cmd(ArkApi::Tools::Utf8Decode(c).c_str()); FString res;
        pc->ConsoleCommand(&res, &cmd, true);
    }

    // tag only NEW same-class dinos (not in the snapshot) so the tame gate refuses them.
    int tagged = 0;
    for (AActor* a : ArkApi::GetApiUtils().GetAllActorsInRange(ppos, scanRange, EServerOctreeGroup::DINOPAWNS)) {
        auto* d = static_cast<APrimalDinoCharacter*>(a);
        if (!d || preexisting.count(a)) continue;
        FString fn; d->GetFullName(&fn, nullptr);
        if (fn.ToString().find(leaf) != std::string::npos) { g_trapDinos.insert(d); ++tagged; }
    }
    DebugLog("TRAP spawndino req=" + std::to_string(count) + " dist=" + std::to_string(t.distance) +
             " pre=" + std::to_string(preexisting.size()) + " tagged=" + std::to_string(tagged) +
             " bp=" + t.blueprint);
    return true;
}
static bool DoSpawnTrap(const std::string& route, const TrapSpawn& t) {
    auto pcs = PcsForRoute(route);
    if (pcs.empty()) return false;
    bool any = false;
    for (auto* pc : pcs) if (DoSpawnTrapOn(pc, t)) any = true;
    return any;                                        // nobody alive yet -> caller retries
}

// Returns false only when the effect must be retried (no player in-world). Faults count as done.
static bool SpawnTrap(const std::string& route, int item_id) {
    auto it = g_fillerSpawn.find(item_id);             // iterator only - no unwinding object here
    if (it == g_fillerSpawn.end()) return true;        // not a trap item -> nothing to do
    bool ok = true;
    __try { ok = DoSpawnTrap(route, it->second); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return ok;
}

// good filler: native GiveItem to the TARGET player (GFI console match is unreliable on a
// dedicated server; gfi = full Blueprint'..' path). Returns false when not in-world (retry).
static bool DoGiveFillerTo(AShooterPlayerController* pc, const std::vector<FillerGive>& gives) {
    // require a LIVE character: GiveItem to a dead/dying pawn lands in the corpse's inventory
    // (= lost unless looted). Deferring returns it to g_pendingFx until after respawn.
    if (!pc || !pc->GetPlayerCharacter() || pc->GetPlayerCharacter()->IsDead()) return false;
    for (auto& g : gives) {
        // A GFI CODE is preferred when one is set. GiveItem needs the blueprint path exactly right
        // down to the folder, and three of ours were silently wrong for weeks (ok=0 every single
        // time). ARK's own GFI search only needs the short name, so it cannot be broken by an
        // asset living somewhere other than where we guessed.
        if (!g.code.empty()) {
            FString cmd(ArkApi::Tools::Utf8Decode(
                "GFI " + g.code + " " + std::to_string(g.qty) + " " +
                std::to_string(g.quality) + " 0").c_str());
            FString res;
            pc->ConsoleCommand(&res, &cmd, true);
            DebugLog("GIVE via GFI code=" + g.code + " qty=" + std::to_string(g.qty));
            continue;
        }
        TArray<UPrimalItem*> out;
        FString bp(ArkApi::Tools::Utf8Decode(g.gfi).c_str());
        bool ok = pc->GiveItem(&out, &bp, g.qty, (float)g.quality, false, false, 0.f);
        DebugLog("GIVE ok=" + std::string(ok ? "1" : "0") + " qty=" + std::to_string(g.qty) + " bp=" + g.gfi);
    }
    return true;
}
static bool DoGiveFiller(const std::string& route, const std::vector<FillerGive>& gives) {
    auto pcs = PcsForRoute(route);
    if (pcs.empty()) return false;
    bool any = false;
    for (auto* pc : pcs) if (DoGiveFillerTo(pc, gives)) any = true;
    return any;                                        // nobody alive yet -> caller retries
}
static bool GiveFiller(const std::string& route, int item_id) {
    auto it = g_fillerGive.find(item_id);
    if (it == g_fillerGive.end()) return true;         // not a give item -> nothing to do
    bool ok = true;
    __try { ok = DoGiveFiller(route, it->second); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return ok;
}

// buff/debuff filler: run the ForceGiveBuff command on the TARGET player's controller.
// Same live-character rule as gives: dead/absent -> retry after respawn (a debuff landing on a
// corpse would silently no-op; a buff would be wasted).
static bool DoBuffFiller(const std::string& route, const std::string& cmdStr) {
    auto pcs = PcsForRoute(route);
    bool any = false;
    for (auto* pc : pcs) {
        if (!pc || !pc->GetPlayerCharacter() || pc->GetPlayerCharacter()->IsDead()) continue;
        FString cmd(ArkApi::Tools::Utf8Decode(cmdStr).c_str()); FString res;
        pc->ConsoleCommand(&res, &cmd, true);
        any = true;
    }
    if (any) DebugLog("BUFF applied cmd=" + cmdStr + " players=" + std::to_string(pcs.size()) +
                      (route.empty() ? "" : " [" + route + "]"));
    return any;
}
static bool BuffFiller(const std::string& route, int item_id) {
    auto it = g_fillerBuff.find(item_id);
    if (it == g_fillerBuff.end()) return true;         // not a buff item -> nothing to do
    bool ok = true;
    __try { ok = DoBuffFiller(route, it->second); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return ok;
}

// per-tick cap on EXPENSIVE filler effects (dino spawns / item gives / buffs). A big AP send -
// e.g. ~400 traps arriving at once after an idle endgame - applied in ONE game frame spawned
// hundreds of dino groups simultaneously and flooded/hitched the server (live-hit 2026-07-20).
// Non-filler unlocks (engrams/tames) stay unthrottled: they're cheap idempotent state writes.
// Refilled each tick (1s); overflow queues to g_pendingFx and drains on later ticks.
static const int FX_PER_TICK = 6;
static int       g_fxBudget = FX_PER_TICK;

// filler effects that arrived while the target player wasn't in-world (OR were throttled) -
// retried each tick, still subject to the per-tick budget.
static std::vector<std::pair<std::string, int>> g_pendingFx;   // (route, item id)
static void RetryPendingFx() {
    if (g_pendingFx.empty()) return;
    std::vector<std::pair<std::string, int>> again;
    for (auto& [route, id] : g_pendingFx) {
        // A recovery that started while this was queued must still cancel it - otherwise the burst
        // just arrives late, which is exactly how 114 filler gives landed 31 seconds after the
        // re-send began.
        if (QuietFor(route)) continue;                                      // recovery -> drop it
        if (g_fxBudget <= 0) { again.emplace_back(route, id); continue; }   // throttled -> next tick
        if (!SpawnTrap(route, id) || !GiveFiller(route, id) || !BuffFiller(route, id))
            again.emplace_back(route, id);                                   // player absent -> keep
        else
            --g_fxBudget;                                                    // one effect fired
    }
    if (again.size() < g_pendingFx.size())
        DebugLog("FX retried: delivered " + std::to_string(g_pendingFx.size() - again.size()) +
                 ", still pending " + std::to_string(again.size()));
    g_pendingFx.swap(again);
}

// bundle_structures items: one AP item unlocks every structure engram of a material. Ids +
// classification rule mirror the apworld's Items.py STRUCTURE_BUNDLES - keep them in sync.
// Match = engram_class contains "PrimalItemStructure_" AND material appears as a word in ap_name.
static const std::map<int, std::string> kStructureBundles = {
    {8738001, "Wood"}, {8738002, "Stone"}, {8738003, "Metal"}, {8738004, "Greenhouse"},
    {8738005, "Tek"}, {8738006, "Thatch"},
};
static bool NameHasWord(const std::string& name, const std::string& word) {
    size_t pos = 0;
    while ((pos = name.find(word, pos)) != std::string::npos) {
        bool lb = pos == 0 || !isalnum((unsigned char)name[pos - 1]);
        size_t end = pos + word.size();
        bool rb = end >= name.size() || !isalnum((unsigned char)name[end]);
        if (lb && rb) return true;
        pos = end;
    }
    return false;
}
// One item often unlocks SEVERAL things - a count-group representative, a structure or mod bundle,
// a saddle bundled with its tame. Only the headline was ever announced, so a player receiving
// "Engram: Stone Wall" had no idea the other 40 stone structures came with it.
//
// The member list is passed as an ARGUMENT rather than baked into the format string: an item or
// survivor name containing a brace would otherwise be read as a format field by FString::Format.
// Long lists are wrapped across several chat lines instead of truncated, since the whole point is
// that the player can see everything they got.
static void AnnounceUnlock(const std::string& route, const std::string& headline,
                           const std::vector<std::string>& extras, const std::string& from,
                           bool showCount = true) {
    // WHO IS THIS FOR? Previously the recipient only appeared when a per-player route existed, so
    // on a shared server the line just said "Unlocked X" and left everyone guessing whether it was
    // theirs. Name it either way: a survivor in per-player mode, "everyone" when the whole server
    // shares one Archipelago slot.
    // Say plainly who benefits, and make the two modes distinguishable at a glance: a shared slot
    // unlocks for the whole server, a per-player slot unlocks for that survivor ONLY - which
    // matters when four people are watching the same chat wondering if it was theirs.
    // Only two cases can reach here: a named survivor (per-player mode), or a genuinely shared
    // slot. Root-mailbox items in per-player mode are refused before they ever get applied.
    std::wstring who = route.empty() ? std::wstring(L"everyone on the server")
                                     : ArkApi::Tools::Utf8Decode(route) + L" only";

    // Every AP item name carries a category prefix ("Engram: ", "Tame: "). Repeating it on each
    // entry of a list is pure noise - "Engram: Riot Gloves, Engram: Riot Boots" - so if the
    // headline and every extra share one, hoist it out and print it once.
    static const char* kPrefixes[] = { "Engram: ", "Tame: ", "Beacon: ", "Cave Crate: ", "Buff: " };
    std::string prefix, head = headline;
    std::vector<std::string> items = extras;
    for (const char* pf : kPrefixes) {
        const std::string P(pf);
        if (head.rfind(P, 0) != 0) continue;
        bool all = true;
        for (auto& e : items) if (e.rfind(P, 0) != 0) { all = false; break; }
        if (!all) continue;
        prefix = P;
        head = head.substr(P.size());
        for (auto& e : items) e = e.substr(P.size());
        break;
    }

    // Short lists read far better inline than as a count plus a continuation line - "(+1 more)"
    // followed by a lone item was the worst of both.
    // Bundles (showCount == false) already state their size in the headline - "ALL Stone
    // structures (14 engrams)" - so running members on from that with a comma reads as if the
    // bundle were just another list entry. Those always go on their own lines.
    std::string joined = head;
    size_t inlined = 0;
    if (showCount) {
        const size_t budget = 100;
        for (auto& e : items) {
            if (joined.size() + e.size() + 2 > budget) break;
            joined += ", " + e;
            ++inlined;
        }
    }
    const bool allInline = (inlined == items.size());

    std::wstring first = L"Unlocked for " + who + L": " +
                         ArkApi::Tools::Utf8Decode(prefix + joined);
    if (showCount && !allInline)
        first += L"  (+" + std::to_wstring(items.size() - inlined) + L" more)";
    if (!from.empty())
        first += L"   (found by " + ArkApi::Tools::Utf8Decode(from) + L")";
    ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), L"{}", first);
    if (allInline) return;

    const size_t kWrap = 140;                    // keep a line readable in ARK's chat box
    std::wstring line;
    for (size_t i = inlined; i < items.size(); ++i) {
        std::wstring nm = ArkApi::Tools::Utf8Decode(items[i]);
        if (!line.empty() && line.size() + nm.size() + 2 > kWrap) {
            ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), L"{}",
                                                       L"      " + line);
            line.clear();
        }
        if (!line.empty()) line += L", ";
        line += nm;
    }
    if (!line.empty())
        ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), L"{}", L"      " + line);
}

static std::string ItemNameOf(int id) {
    auto it = g_tables.item_name.find(id);
    return it == g_tables.item_name.end() ? std::string() : it->second;
}

static void ApplyStructureBundle(const std::string& route, int bundle_id,
                                 const std::string& material, const std::string& from) {
    int members = 0;
    std::vector<std::string> names;
    for (auto& [item_id, cls] : g_tables.item_to_engram_class) {
        if (cls.find("PrimalItemStructure_") == std::string::npos) continue;
        auto nit = g_tables.item_name.find(item_id);
        if (nit == g_tables.item_name.end() || !NameHasWord(nit->second, material)) continue;
        if (!ItemAllowedForRoute(route, item_id)) continue;   // mod this slot didn't enable
        g_state->AddItem(route, item_id);                // persists -> gate + reassert keep it
        auto eit = g_itemToEngram.find(item_id);
        if (eit != g_itemToEngram.end())
            for (UClass* c : eit->second) GrantEngramToPlayers(route, c);
        names.push_back(nit->second);
        ++members;
    }
    AnnounceUnlock(route, "ALL " + material + " structures (" + std::to_string(members) +
                          " engrams)", names, from, false);
    DebugLog("BUNDLE structures material=" + material + " members=" + std::to_string(members));
}

// A mod group item unlocks its member engrams. Unlike the structure bundles (which re-derive
// members from a material word), the member ITEM IDS come straight from the apworld's mod json, so
// the two sides can never drift.
static void ApplyModBundle(const std::string& route, int bundle_id,
                           const std::vector<int>& members, const std::string& from) {
    int granted = 0;
    std::vector<std::string> names;
    for (int mid : members) {
        g_state->AddItem(route, mid);                    // persists -> gate + reassert keep it
        auto eit = g_itemToEngram.find(mid);
        if (eit == g_itemToEngram.end()) continue;       // mod not installed on this server
        for (UClass* c : eit->second) GrantEngramToPlayers(route, c);
        std::string n = ItemNameOf(mid);
        if (!n.empty()) names.push_back(n);
        ++granted;
    }
    std::string label = ItemNameOf(bundle_id);
    if (label.empty()) label = "mod bundle";
    AnnounceUnlock(route, label + " (" + std::to_string(granted) + " engrams)", names, from, false);
    DebugLog("BUNDLE mod id=" + std::to_string(bundle_id) + " granted=" + std::to_string(granted) +
             "/" + std::to_string(members.size()));
}

void ApplyItem(const std::string& route, int item_id, const std::string& from) {
    bool is_new = g_state->AddItem(route, item_id);
    bool is_engram = g_itemToEngram.count(item_id) > 0;
    bool is_filler = g_fillerSpawn.count(item_id) > 0 || g_fillerGive.count(item_id) > 0
                  || g_fillerBuff.count(item_id) > 0;
    DebugLog("APPLY id=" + std::to_string(item_id) + " new=" + (is_new ? "1" : "0") +
             " engram=" + (is_engram ? "1" : "0") + " from=" + from +
             (route.empty() ? "" : " [" + route + "]"));
    auto bit = kStructureBundles.find(item_id);         // structure bundle -> unlock every member
    if (bit != kStructureBundles.end()) {
        if (is_new) ApplyStructureBundle(route, item_id, bit->second, from);
        return;
    }
    // curated per-mod group (S+ Wiring / Turrets / Automation / ...) -> unlock every member engram
    auto mbit = g_tables.mod_bundles.find(item_id);
    if (mbit != g_tables.mod_bundles.end()) {
        if (is_new) ApplyModBundle(route, item_id, mbit->second, from);
        return;
    }
    // the pool holds many COPIES of the same filler id; each copy (new index) re-fires its
    // effect. Everything else dedups by id.
    if (!is_new && !is_filler) return;             // already received (non-filler)

    // A recovery re-send must be decided BEFORE the throttle below. The throttle defers overflow
    // filler to g_pendingFx and returns, and the retry path has no idea a recovery is happening -
    // so checking "quiet" after it meant deferred filler still fired, half a minute later, in a
    // burst. Ask first, and drop recovery filler outright rather than queueing it.
    const bool quiet = QuietFor(route);           // re-send to rebuild lost state: stay silent

    // What ELSE does this item hand over? Collected BEFORE the announcement so the chat line can
    // name every one of them - the saddle that rides along with a tame, and every member folded
    // under a count-group representative. These are granted further down; this only reads the maps.
    std::vector<std::string> extras;
    if (FlagFor(g_routeBundleSaddles, route)) {
        auto sit0 = g_tameItemToSaddleItem.find(item_id);
        if (sit0 != g_tameItemToSaddleItem.end()) {
            std::string n = ItemNameOf(sit0->second);
            if (!n.empty()) extras.push_back(n);
        }
    }
    {   auto grit0 = g_routeItemGroups.find(route);
        if (grit0 != g_routeItemGroups.end()) {
            auto mit0 = grit0->second.find(item_id);
            if (mit0 != grit0->second.end())
                for (int member : mit0->second) {
                    std::string n = ItemNameOf(member);
                    if (!n.empty() && std::find(extras.begin(), extras.end(), n) == extras.end())
                        extras.push_back(n);
                }
        }
    }
    if (quiet && is_filler) return;               // already granted in the original run

    // throttle expensive filler so a huge simultaneous send doesn't flood one frame: when the
    // per-tick budget is spent, defer this copy (effect AND its chat line) to a later tick.
    if (is_filler) {
        if (g_fxBudget <= 0) { g_pendingFx.emplace_back(route, item_id); return; }
        --g_fxBudget;
    }

    // announce known items (skip unknown ids), naming everything it unlocks
    auto nameIt = g_tables.item_name.find(item_id);
    if (!quiet && nameIt != g_tables.item_name.end())
        AnnounceUnlock(route, nameIt->second, extras, from);

    auto it = g_itemToEngram.find(item_id);       // engram item -> push the unlock now
    if (it != g_itemToEngram.end()) {
        bool reached = false, refused = false;
        for (UClass* c : it->second) reached |= GrantEngramToPlayers(route, c, &refused);
        // Two very different reasons the grant can not land, and calling both "nobody in-world"
        // sent one live investigation down the wrong path: the player may be dead/logged out, or
        // the player may be right there and the GAME refused because a prerequisite engram is
        // missing. Reassert retries either way; name which one it is.
        if (!reached)
            DebugLog("ENGRAM deferred id=" + std::to_string(item_id) + ": " +
                     (refused ? "the game REFUSED it (prerequisite engram not received yet)"
                              : "nobody in-world for " +
                                (route.empty() ? std::string("this server") : "route " + route)) +
                     " - Reassert keeps retrying");
    }
    // taming / supply / boss / map items: gating reads State on demand, nothing to push.

    // filler effects; if the target player isn't in-world yet, queue a retry.
    // During a recovery re-send the effect already fired in the original run - repeating it would
    // hand out the resources (or the trap dinos) a second time.
    bool trapOk = quiet ? true : SpawnTrap(route, item_id);   // trap filler -> spawn dinos nearby
    bool giveOk = quiet ? true : GiveFiller(route, item_id);  // good filler -> give item(s)
    bool buffOk = quiet ? true : BuffFiller(route, item_id);  // buff/debuff -> ForceGiveBuff
    if (!trapOk || !giveOk || !buffOk) {
        g_pendingFx.emplace_back(route, item_id);
        DebugLog("FX deferred (target player not in-world) id=" + std::to_string(item_id));
    }

    // bundle_saddles: a tame unlock also grants the dino's saddle engram - ONLY if THIS route's slot
    // enabled it (per-route, so an unbundled slot never gets the saddle handed over with the tame).
    if (FlagFor(g_routeBundleSaddles, route)) {
        auto sit = g_tameItemToSaddleItem.find(item_id);
        if (sit != g_tameItemToSaddleItem.end()) {
            g_state->AddItem(route, sit->second);     // record so the gate + reassert keep it
            auto eit = g_itemToEngram.find(sit->second);
            if (eit != g_itemToEngram.end())
                for (UClass* c : eit->second) GrantEngramToPlayers(route, c);
            DebugLog("BUNDLE saddle item=" + std::to_string(sit->second) + " with tame=" + std::to_string(item_id));
        }
    }

    // count-grouping (engrams_per_item / tames_per_item): a representative unlock also grants every
    // FOLDED member. Members were never pooled, so AP never sends them - we mark them owned (so the
    // engram/tame gate + reassert keep them) and push any engram unlock now. Same pattern as saddles.
    auto grit = g_routeItemGroups.find(route);
    if (grit != g_routeItemGroups.end()) {
        auto mit = grit->second.find(item_id);
        if (mit != grit->second.end())
            for (int member : mit->second) {
                g_state->AddItem(route, member);
                auto eit = g_itemToEngram.find(member);          // engram member -> unlock its class
                if (eit != g_itemToEngram.end())
                    for (UClass* c : eit->second) GrantEngramToPlayers(route, c);
                DebugLog("GROUP member=" + std::to_string(member) + " via rep=" + std::to_string(item_id));
            }
    }
}

// tek engrams are never AP pool items: each boss's set unlocks locally on its first kill
// (any difficulty). Stored in the SHARED "" bucket = unlocked for every player (boss fights
// are team efforts). Reassert grants them to everyone.
static void GrantTekForBoss(const std::string& baseTag) {
    auto it = g_tekGrants.find(baseTag);
    if (it == g_tekGrants.end()) return;
    int granted = 0;
    for (int item : it->second) {
        if (!g_state->AddItem("", item)) continue;     // already unlocked (earlier difficulty kill)
        auto eit = g_itemToEngram.find(item);
        if (eit != g_itemToEngram.end())
            for (UClass* c : eit->second) GrantEngramToPlayers("", c);
        ++granted;
    }
    if (granted > 0) {
        std::wstring m = L"Boss defeated - " + std::to_wstring(granted) + L" Tek engrams unlocked!";
        ChatNotify(m.c_str());
        DebugLog("TEK granted " + std::to_string(granted) + " engrams for " + baseTag);
    }
}

// Re-apply every received engram to its players (idempotent - HasEngram skips ones already
// known). Handles items that arrived before the player was in-world. Shared "" grants to all.
// One grant, individually guarded. The registry's second pass caches UClass* obtained from
// BPLoadClass, and nothing holds a GC reference to them - so a pointer can go stale between boot
// and use. Guarding only the whole pass (as ReassertEngrams does) meant one stale class aborted
// every remaining engram for every remaining player, silently: the log said "FAULT in
// ReassertEngrams" and nothing else. Per-class guarding keeps the rest of the pass working.
// No C++ object is constructed inside the __try (route is passed by reference), so this is legal.
static bool GrantEngramGuarded(const std::string& route, UClass* c) {
    __try { GrantEngramToPlayers(route, c); return true; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
static void DoReassert() {
    static std::set<int> badLogged;              // name each culprit once, not every tick
    for (auto& route : g_state->Players())
        for (auto& [item_id, classes] : g_itemToEngram)
            if (g_state->HasItem(route, item_id))
                for (UClass* c : classes)
                    if (c && !GrantEngramGuarded(route, c) && badLogged.insert(item_id).second)
                        DebugLog("!! FAULT granting engram item=" + std::to_string(item_id) +
                                 " (stale class pointer?) - skipped, rest of the pass continues");
}

// free starter engrams: mark the configured starter engrams as owned (reassert then grants them
// in-game). Multiplayer: every connected player gets them in their own bucket.
static std::set<std::string> g_starterGrantedRoutes;
static void DoGrantStarter() {
    if (g_starterItemIds.empty()) return;
    for (auto& route : KnownRoutes()) {
        if (!FlagFor(g_routeFreeStarter, route)) continue;   // per-route: only slots that enabled it
        if (g_starterGrantedRoutes.count(route)) continue;
        g_starterGrantedRoutes.insert(route);
        int n = 0;
        for (int item : g_starterItemIds)
            if (g_state->AddItem(route, item)) ++n;
        if (n) DebugLog("STARTER granted " + std::to_string(n) + " of " +
                        std::to_string(g_starterItemIds.size()) + " starter engrams" +
                        (route.empty() ? "" : " [" + route + "]"));
    }
}
static void ReassertEngrams() {
    __try { DoReassert(); } __except (EXCEPTION_EXECUTE_HANDLER) { g_reassertFaulted = true; }
}

// Each received item's absolute AP index (from the connector) uniquely identifies a COPY -
// the pool holds many copies of the same filler item_id, and each copy must re-fire its
// effect. The highest applied index is persisted PER MAILBOX (applied_index.json: root keeps
// the legacy location, subdirs keep theirs inside) so a restart doesn't re-give filler.
static fs::path WatermarkPath(const std::string& route) {
    return route.empty() ? PluginDir() / "applied_index.json"
                         : g_ipc->DirFor(route) / "applied_index.json";
}
static int LoadWatermark(const std::string& route) {
    try { fs::path p = WatermarkPath(route);
          if (fs::exists(p)) { nlohmann::json j; std::ifstream(p) >> j; return j.value("max", -1); }
    } catch (...) {}
    return -1;
}
static void SaveWatermark(const std::string& route, int v) {
    try { std::ofstream(WatermarkPath(route)) << "{\"max\": " << v << "}\n"; } catch (...) {}
}

// Read one mailbox's items_in.jsonl (small file). Lines carry {"item_id","from","index"};
// dedup is by INDEX (persisted watermark). Legacy lines without an index dedup by item id.
// Re-assert OWNERSHIP of an item already applied in a previous session: mark it owned, push its
// engram unlock, and expand the same saddle / count-group members ApplyItem would. Deliberately
// silent - no chat line, no filler effect, no trap - because this replays the player's whole
// history once at boot.
static void ReownItem(const std::string& route, int item_id) {
    if (g_fillerSpawn.count(item_id) || g_fillerGive.count(item_id) || g_fillerBuff.count(item_id))
        return;                                     // filler owns nothing; its effect already fired
    bool isNew = g_state->AddItem(route, item_id);
    // A bundle rep owns nothing by itself - all of its value is in the members, so re-owning just
    // the rep would leave the player holding an item that unlocks nothing. Expand it, but only
    // when state had genuinely lost it (isNew): on a routine boot backfill every rep is already
    // owned, so this stays quiet.
    auto bit = kStructureBundles.find(item_id);
    if (bit != kStructureBundles.end()) {
        if (isNew) ApplyStructureBundle(route, item_id, bit->second, "");
        return;
    }
    auto mbit = g_tables.mod_bundles.find(item_id);
    if (mbit != g_tables.mod_bundles.end()) {
        if (isNew) ApplyModBundle(route, item_id, mbit->second, "");
        return;
    }
    auto it = g_itemToEngram.find(item_id);
    if (it != g_itemToEngram.end())
        for (UClass* c : it->second) GrantEngramToPlayers(route, c);
    if (FlagFor(g_routeBundleSaddles, route)) {     // bundled saddle rides along with its tame
        auto sit = g_tameItemToSaddleItem.find(item_id);
        if (sit != g_tameItemToSaddleItem.end()) {
            g_state->AddItem(route, sit->second);
            auto eit = g_itemToEngram.find(sit->second);
            if (eit != g_itemToEngram.end())
                for (UClass* c : eit->second) GrantEngramToPlayers(route, c);
        }
    }
    auto grit = g_routeItemGroups.find(route);      // count-grouping members
    if (grit != g_routeItemGroups.end()) {
        auto mit = grit->second.find(item_id);
        if (mit != grit->second.end())
            for (int member : mit->second) {
                g_state->AddItem(route, member);
                auto eit = g_itemToEngram.find(member);
                if (eit != g_itemToEngram.end())
                    for (UClass* c : eit->second) GrantEngramToPlayers(route, c);
            }
    }
    if (isNew)                                      // only interesting when it was actually missing
        DebugLog("REOWN id=" + std::to_string(item_id) +
                 (route.empty() ? "" : " [" + route + "]") + " (state had lost it)");
}

static void PollMailbox(const std::string& route) {
    int watermark = LoadWatermark(route);
    // Once per route per server boot, re-own everything at or below the watermark (see below).
    // The flag is only CONSUMED at the end, after a non-empty mailbox was actually processed -
    // on the first ticks items_in.jsonl is usually still empty (the AP client has not connected
    // yet), and marking it done there would skip the backfill entirely.
    static std::set<std::string> backfilledRoutes;
    const bool backfilled = backfilledRoutes.count(route) > 0;
    static std::map<std::string, std::set<int>> processedIds;   // legacy (index-less) lines only
    fs::path path = g_ipc->DirFor(route) / "items_in.jsonl";

    std::string content;
    { std::ifstream f(path, std::ios::binary);
      if (f) { std::stringstream ss; ss << f.rdbuf(); content = ss.str(); } }
    if (content.empty()) return;

    // multiplayer misconfig tripwire: items in the ROOT mailbox are SHARED (unlock for every
    // player). In multiplayer each slot's connector must point at ipc\<CharacterName> instead.
    // PER-PLAYER MODE: an item in the ROOT mailbox has no owner. The route IS the identity, so
    // there is no way to tell who it was sent to - and applying it to "everyone", or to whichever
    // controller happens to be first, is a guess that unlocks one player's items for the rest of
    // the server. Refuse to apply it. Nothing is lost: the lines stay in the file and the
    // watermark does not advance, so once the connector points at ipc\<CharacterName> the items
    // apply correctly to the right survivor.
    if (g_multiplayer && route.empty()) {
        static bool warned = false;
        if (!warned) {
            warned = true;
            DebugLog("!! MULTIPLAYER: items are arriving in the ROOT ipc mailbox, which has no "
                     "owner - NOT applying them. Each slot must use ipc\\<CharacterName>; "
                     "reconnect that player with /connect (or fix connector.ini ipc_dir).");
            ChatNotify(L"ArkAP: items arrived in the shared root mailbox and were NOT applied - "
                       L"in multiplayer each slot needs its own ipc\\<CharacterName>. "
                       L"Reconnect with /connect.");
        }
        return;                                  // never guess an owner
    }

    // WAIT FOR THE SURVIVOR. Nothing here is delivered to a player who is disconnected or dead:
    // hold the whole mailbox instead, un-drained and with the watermark untouched, so it replays
    // intact the moment they are back and standing up. Deferring only the EFFECT was not enough -
    // ownership, the chat announcement and the engram unlock all still fired into the void, and
    // an unlock that "succeeded" against an absent body is exactly the failure that is impossible
    // to tell from a working one in a log. Nothing is lost by waiting: the lines stay in the
    // file, and an offline player's mailbox simply drains when they return.
    static std::map<std::string, bool> held;                 // log the TRANSITION, not every tick
    if (!RouteReady(route)) {
        if (!held[route]) {
            held[route] = true;
            DebugLog("MAILBOX held: " + (route.empty() ? std::string("this server") : route) +
                     " has no live, spawned survivor - items wait (watermark not advanced)");
        }
        return;
    }
    if (held[route]) {
        held[route] = false;
        DebugLog("MAILBOX resumed: " + (route.empty() ? std::string("this server") : route) +
                 " is back in-world - delivering everything that queued up");
    }

    // STALE WATERMARK GUARD. The watermark is only ever set from an index that was present in
    // this file, so it can never legitimately exceed the file's highest index. When it does, the
    // mailbox was reset (new seed) without its watermark going with it - which used to silently
    // swallow every item of the new seed up to the old high-water mark. Treat it as absent.
    {   int maxIdx = -1;
        std::stringstream scan(content);
        std::string l;
        while (std::getline(scan, l)) {
            auto p = l.find("\"index\"");
            if (p == std::string::npos) continue;
            try { maxIdx = (std::max)(maxIdx, std::stoi(l.substr(l.find(':', p) + 1))); }
            catch (...) {}
        }
        if (watermark > maxIdx) {
            DebugLog("WATERMARK stale: " + std::to_string(watermark) + " > highest index in "
                     "items_in.jsonl (" + std::to_string(maxIdx) + ") - mailbox was reset without "
                     "it; clearing so nothing is skipped" + (route.empty() ? "" : " [" + route + "]"));
            watermark = -1;
            SaveWatermark(route, -1);
        }
    }

    std::stringstream ls(content);
    std::string line;
    bool wmDirty = false;
    while (std::getline(ls, line)) {
        auto q = line.find("\"item_id\"");
        if (q == std::string::npos) continue;
        int id = 0;
        try { id = std::stoi(line.substr(line.find(':', q) + 1)); }
        catch (...) { continue; }
        int idx = -1;
        auto ip = line.find("\"index\"");
        if (ip != std::string::npos) {
            try { idx = std::stoi(line.substr(line.find(':', ip) + 1)); } catch (...) { idx = -1; }
        }
        if (idx >= 0) {
            if (idx <= watermark) {
                // Already applied, so its filler effect must NOT re-fire - but OWNERSHIP still
                // has to be re-asserted once per boot. state.json is the only record that the
                // player owns this item, and if it is ever lost (or arrives incomplete) the
                // watermark means nothing would ever re-fill it: taming and crates stay locked
                // forever with no way back except a manual command. Re-owning here is idempotent
                // (a set insert plus a HasEngram-guarded unlock) and makes that self-healing.
                //
                // The `!backfilled` gate alone was not enough. It only covers the FIRST non-empty
                // pass of a boot, so an item whose line reached the mailbox LATE - after the
                // watermark had already moved past its index, which is exactly what AP's resend
                // on a reconnect looks like when the original write was lost - was skipped on
                // every pass forever. The player saw it awarded on the AP server and nothing at
                // all in game; only /send (a fresh index) could recover it. So also re-own any
                // line this route does not actually own yet: idempotent, silent, and it turns a
                // permanently lost item into a one-line "state had lost it" in the log.
                if (!backfilled || !g_state->HasItem(route, id)) ReownItem(route, id);
                continue;
            }
            watermark = idx; wmDirty = true;
        } else {
            if (!processedIds[route].insert(id).second) continue;   // legacy line: dedup by id
        }
        std::string from;
        auto fp = line.find("\"from\"");
        if (fp != std::string::npos) {
            auto a = line.find('"', line.find(':', fp) + 1);
            auto b = (a == std::string::npos) ? std::string::npos : line.find('"', a + 1);
            if (b != std::string::npos) from = line.substr(a + 1, b - a - 1);
        }
        ApplyItem(route, id, from);   // non-filler dupes still dedup via persisted state
    }
    if (wmDirty) SaveWatermark(route, watermark);
    backfilledRoutes.insert(route);                 // mailbox had content - backfill is done
}

// every mailbox: the root (route "") + one subfolder per multiplayer slot.
static std::vector<std::string> MailboxRoutes() {
    std::vector<std::string> routes = { "" };
    if (!g_multiplayer) return routes;
    std::error_code ec;
    for (auto& e : fs::directory_iterator(g_ipc->Root(), ec)) {
        if (!e.is_directory()) continue;
        // "_unnamed" is not a survivor - it is the fallback SanitizeRoute returns when a name
        // could not be read. A folder by that name is debris from an older build; treat it as a
        // route and every tick spends work polling a mailbox no AP client will ever write to.
        if (e.path().filename() == "_unnamed") {
            static bool warned = false;
            if (!warned) {
                warned = true;
                DebugLog("!! ipc\\_unnamed exists and is NOT a survivor mailbox - ignoring it. "
                         "It is debris from a build that could report under an unresolved name; "
                         "anything inside it was never delivered. Safe to delete.");
            }
            continue;
        }
        routes.push_back(e.path().filename().string());
    }
    return routes;
}

// AUTOMATIC recovery from a lost state file. The REOWN backfill in PollMailbox rebuilds
// ownership by replaying items_in.jsonl - but that file is itself deleted on a seed change, so if
// state.json is lost afterwards there is no history left to replay and the player stays locked out
// with no in-game symptom except "taming is locked forever".
//
// The inconsistency is detectable though: an applied-index watermark of N means N items were
// applied at some point, so an EMPTY received set for that route cannot be true. When we see that,
// clear the watermark and session marker so Archipelago re-sends the slot's whole item list on the
// next connect, which rebuilds the set. Runs once per route per boot. This is exactly what
// /aprecover does by hand - it just no longer needs anyone to know that command exists.
static void DoAutoRecoverLostState() {
    static std::set<std::string> done;
    for (auto& route : MailboxRoutes()) {
        if (!done.insert(route).second) continue;
        // READ-ONLY state (both state.json and its backup unreadable) must NOT trigger a re-send.
        // Save() is blocked in that mode, so the rebuilt set never reaches disk - the next restart
        // sees an empty set again and asks for the whole list again, flooding the player on every
        // single boot. The corrupt file is the thing to fix; re-sending just papers over it loudly.
        if (g_state->ReadOnly()) {
            DebugLog("AUTORECOVER skipped: state is read-only (corrupt) - fix state.json first");
            continue;
        }
        int wm = LoadWatermark(route);
        if (wm < 0 || g_state->ReceivedCount(route) > 0) continue;   // consistent - nothing to do
        std::error_code ec;
        // ONLY the watermark. Deleting session.json here used to look like a cheap way to force a
        // re-send, but session.json is what the client compares the room's seed_name against - so
        // removing it made the very next connect believe the seed had CHANGED. That fake seed
        // change then wiped checks_out, boss_out and the whole checked/received set: a sledgehammer
        // for a problem that only needed the watermark cleared. Archipelago sends the full item
        // list on every connect anyway, and items_in.jsonl still holds the history, so clearing the
        // watermark alone is sufficient.
        fs::remove(WatermarkPath(route), ec);
        g_quietUntil[route] = std::time(nullptr) + 180;      // silent while the list comes back
        DebugLog("AUTORECOVER watermark=" + std::to_string(wm) + " but 0 items owned" +
                 (route.empty() ? "" : " [" + route + "]") +
                 " - state was lost; asking Archipelago to re-send the item list");
        ChatNotify(L"ArkAP: your unlock history was missing and is being rebuilt from "
                   L"Archipelago. Taming and crates return in a few seconds.");
    }
}
static void AutoRecoverLostState() {
    __try { DoAutoRecoverLostState(); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// NEW SEED handoff: the AP client thread cannot touch State, so it drops seed_reset.json in the
// route's mailbox and we do the reset here on the game thread. Without this the previous seed's
// checked/received sets survive into the new one and permanently suppress its checks and grants.
static void DoProcessSeedReset() {
    for (auto& route : MailboxRoutes()) {
        fs::path marker = g_ipc->DirFor(route) / "seed_reset.json";
        std::error_code ec;
        if (!fs::exists(marker, ec)) continue;
        std::string seed;
        try { nlohmann::json j; std::ifstream(marker) >> j; seed = j.value("seed", ""); }
        catch (...) {}
        g_state->ResetRoute(route);
        fs::remove(marker, ec);
        DebugLog("SEED RESET seed=" + seed + (route.empty() ? "" : " [" + route + "]") +
                 " - cleared checked/received for this route");
        ArkApi::GetApiUtils().SendChatMessageToAll(
            FString(L"Archipelago"),
            L"New seed detected - this server's Archipelago progress has been reset. "
            L"Checks you have already earned will re-report over the next few seconds.");
    }
}
static void ProcessSeedReset() {
    __try { DoProcessSeedReset(); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

static void DoPollIncoming() {                    // runs on the game thread (Timer)
    if (!ServerReady()) return;
    for (auto& route : MailboxRoutes()) PollMailbox(route);
}
static void PollIncoming() {
    __try { DoPollIncoming(); } __except (EXCEPTION_EXECUTE_HANDLER) { g_pollFaulted = true; }
}

// ----------------------------------------------------------------- engram registry + dumps
// The game-data workers below can hit an access violation if the data layout isn't
// what we expect. AVs are SEH, NOT catchable by C++ try/catch, so each worker is
// isolated in a Do*() function called inside an __try/__except wrapper. The wrapper
// has no objects needing unwinding (required for __try), so SEH propagates cleanly.

// "BlueprintGeneratedClass /Game/X/Y.Y_C"  ->  "Blueprint'/Game/X/Y.Y'"  (what BPLoadClass wants)
static std::string EngramClassToBlueprintPath(const std::string& full) {
    size_t sp = full.find(' ');
    std::string path = (sp == std::string::npos) ? full : full.substr(sp + 1);
    if (path.size() > 2 && path.compare(path.size() - 2, 2, "_C") == 0)
        path.erase(path.size() - 2);
    return "Blueprint'" + path + "'";
}

static int g_unresolvedEngrams = 0;               // reported in /apstatus

static bool MapEngramEntry(UPrimalEngramEntry* e) {
    if (!e) return false;
    TSubclassOf<UPrimalItem> sub = e->BluePrintEntryField();
    UClass* cls = sub.uClass;
    if (!cls) return false;
    std::string name = ClassShortName(cls);       // item blueprint class path
    auto it = g_tables.engram_class_to_item.find(name);
    if (it == g_tables.engram_class_to_item.end()) return false;
    bool fresh = !g_itemToEngram.count(it->second);
    g_engramClassToItem[cls] = it->second;
    g_classToEntry[cls] = e;
    auto& v = g_itemToEngram[it->second];
    if (std::find(v.begin(), v.end(), cls) == v.end()) v.push_back(cls);
    return fresh;
}
static void DoBuildRegistry() {
    UPrimalGameData* gd = GameData();
    if (!gd) return;
    g_engramClassToItem.clear();
    g_itemToEngram.clear();
    g_classToEntry.clear();
    g_grantFailures.clear();

    for (UPrimalEngramEntry* e : gd->EngramBlueprintEntriesField())
        MapEngramEntry(e);
    // EngramBlueprintEntries is only ONE of the three lists UPrimalGameData keeps. The other two
    // hold CLASSES rather than instances, and AdditionalEngramBlueprintClasses is the one mods
    // append to. Walking only the entries is what left Stimulant, Sparkpowder, the arrows and 37
    // other classes to the BPLoadClass fallback below - and a class loaded by path has no engram
    // ENTRY behind it, so ServerUnlockEngram silently does nothing on it. The player's item
    // arrives, the log says granted, and the engram stays locked forever. Take the entries from
    // the class lists' default objects instead, which ARE the game's own entries.
    int fromClasses = 0;
    for (auto& sub : gd->EngramBlueprintClassesField())
        if (sub.uClass && MapEngramEntry(
                static_cast<UPrimalEngramEntry*>(sub.uClass->GetDefaultObject(true)))) ++fromClasses;
    for (auto& sub : gd->AdditionalEngramBlueprintClassesField())
        if (sub.uClass && MapEngramEntry(
                static_cast<UPrimalEngramEntry*>(sub.uClass->GetDefaultObject(true)))) ++fromClasses;
    if (fromClasses)
        DebugLog("registry: " + std::to_string(fromClasses) + " engrams came ONLY from the "
                 "EngramBlueprintClasses / AdditionalEngramBlueprintClasses lists");
    // THIRD PASS - the entry list is not a reliable index of the game's engrams. On Lurch's
    // server 30 vanilla engrams (Campfire, Spear, Sparkpowder, Gunpowder, Narcotic, Stimulant,
    // the arrows...) were absent from EngramBlueprintEntries, so their AP items arrived, logged
    // "engram=0", and silently unlocked nothing - forever, since ApplyItem dedups by id. Several
    // of those are progression gates in the apworld's logic, so it was a softlock risk.
    // We already hold the exact BlueprintGeneratedClass path in engrams.json, so load it directly
    // instead of hoping it shows up in a list we do not own.
    int recovered = 0, missing = 0, notUnlockable = 0;
    auto engMap = gd->ItemEngramMapField();      // hoisted: the accessor copies
    for (auto& [clsPath, item_id] : g_tables.engram_class_to_item) {
        if (g_itemToEngram.count(item_id)) continue;          // already mapped by the walk above
        std::string bp = EngramClassToBlueprintPath(clsPath);
        FString fbp(ArkApi::Tools::Utf8Decode(bp).c_str());
        UClass* cls = UVictoryCore::BPLoadClass(&fbp);
        if (!cls) {
            ++missing;
            if (missing <= 20) DebugLog("registry UNRESOLVED " + clsPath);
            continue;
        }
        // A class loaded by path may still have a perfectly good engram ENTRY - UPrimalGameData
        // publishes the authoritative item-class -> entry map, so ASK rather than assume. If an
        // entry exists the class is fully unlockable; if not, ServerUnlockEngram would be a silent
        // no-op on it, so it is registered for the GATE direction only (class -> item, used to
        // lock the engram in the hook) and reported. Better an honest line than a grant that
        // logs success and does nothing.
        g_engramClassToItem[cls] = item_id;
        UPrimalEngramEntry* entry = nullptr;
        if (auto* found = engMap.Find(cls)) entry = *found;
        if (entry) {
            g_itemToEngram[item_id].push_back(cls);
            g_classToEntry[cls] = entry;
            ++recovered;
        } else {
            DebugLog("registry NOT-UNLOCKABLE item=" + std::to_string(item_id) + " " + clsPath +
                     " - class loads, but the game has no engram entry for it");
            ++notUnlockable;
        }
    }
    g_unresolvedEngrams = missing;
    if (recovered || missing || notUnlockable)
        DebugLog("registry fallback: recovered=" + std::to_string(recovered) +
                 " NOT-unlockable=" + std::to_string(notUnlockable) +
                 " still_unresolved=" + std::to_string(missing) +
                 (notUnlockable ? " (no engram entry for those - their AP items cannot unlock "
                                  "anything; exclude them from the pool)" : ""));
}
static void BuildRegistrySEH() {                   // __try only, no objects to unwind
    __try { DoBuildRegistry(); }
    __except (EXCEPTION_EXECUTE_HANDLER) { /* bad layout - leave registry empty (gate allows all) */ }
}
static void BuildEngramRegistry() {
    BuildRegistrySEH();
    g_registry_built = true;                       // attempt once; never retry-loop a fault
    DebugLog("registry built: " + std::to_string(g_engramClassToItem.size()) + " engrams mapped");
}

// UPrimalGameData keeps engrams in THREE places and only one of them holds instances:
//   EngramBlueprintEntries          - instantiated entries (what we used to dump)
//   EngramBlueprintClasses          - classes, not yet instantiated
//   AdditionalEngramBlueprintClasses- classes, and the field MODS append to
// Dumping only the first is why "30 vanilla engrams were absent" (see DoBuildRegistry's second
// pass) and why a mod can come back with a fraction of its real engram set - Awesome Teleporters
// yielded 3. The registry has a BPLoadClass fallback to cover the gap for content we already
// know about; a DUMP has nothing to fall back on, because the whole point of it is to discover
// classes we do not have yet. So walk the class lists too, reading each one's CDO.
static void DumpOneEngram(nlohmann::json& out, std::set<std::string>& seen,
                          UPrimalEngramEntry* e, const char* src) {
    if (!e) return;
    std::string cls = ClassShortName(e->BluePrintEntryField().uClass);
    if (cls.empty() || !seen.insert(cls).second) return;      // same engram via two lists
    FString ename; e->NameField().ToString(&ename);
    out.push_back({ {"entry_name", ename.ToString()},
                    {"item_class", cls},
                    {"level", e->GetRequiredLevel()},
                    {"source", src} });
}
static void DoDumpEngrams() {
    UPrimalGameData* gd = GameData();
    if (!gd) return;
    nlohmann::json out = nlohmann::json::array();
    std::set<std::string> seen;
    for (UPrimalEngramEntry* e : gd->EngramBlueprintEntriesField())
        DumpOneEngram(out, seen, e, "entries");
    const size_t fromEntries = out.size();
    // Written now, before the CDO walk: instantiating a default object can fault on a badly
    // behaved mod class, and the SEH guard around this whole function would then leave no dump
    // at all. A partial dump beats none.
    std::ofstream(PluginDir() / "ArkAP_engrams_dump.json") << out.dump(2);

    for (auto& sub : gd->EngramBlueprintClassesField())
        if (sub.uClass)
            DumpOneEngram(out, seen,
                          static_cast<UPrimalEngramEntry*>(sub.uClass->GetDefaultObject(true)),
                          "classes");
    for (auto& sub : gd->AdditionalEngramBlueprintClassesField())
        if (sub.uClass)
            DumpOneEngram(out, seen,
                          static_cast<UPrimalEngramEntry*>(sub.uClass->GetDefaultObject(true)),
                          "additional");
    std::ofstream(PluginDir() / "ArkAP_engrams_dump.json") << out.dump(2);
    DebugLog("DUMP engrams: " + std::to_string(out.size()) + " unique (" +
             std::to_string(fromEntries) + " from EngramBlueprintEntries, " +
             std::to_string(out.size() - fromEntries) + " only reachable via the class lists)");
}
// Console: ArkAP.DumpEngrams - harvest real engram classes to regenerate engrams.json.
static void DumpEngrams(APlayerController*, FString*, bool) {
    __try { DoDumpEngrams(); }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
}
// Console: ArkAP.BuildRegistry - (re)build the engram gate map after deploying real engrams.json.
static void BuildRegistryCmd(APlayerController*, FString*, bool) { BuildEngramRegistry(); }

// Dump every explorer note / dossier on the CURRENT map: index range (the
// ExplorerNoteIndex used by the dossier check) + count. Run once per map to
// harvest all maps' notes. SEH-guarded like the engram dump.
static void DoDumpNotes() {
    UPrimalGameData* gd = GameData();
    if (!gd) return;
    auto& notes = gd->ExplorerNoteEntriesField();
    nlohmann::json out;
    out["count"] = notes.Num();
    nlohmann::json idx = nlohmann::json::array();
    for (int i = 0; i < notes.Num(); ++i) idx.push_back(i);
    out["indices"] = idx;
    std::ofstream(PluginDir() / "ArkAP_notes_dump.json") << out.dump(2);
}
static void DumpNotes(APlayerController*, FString*, bool) {
    __try { DoDumpNotes(); }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// One-shot harvest of every wild-dino Character_BP class currently loaded near the player.
// Ground truth for spawn_classes.json (randomize_dino_spawns). Chat: /dumpdinos - fly around the
// map's spawn zones running it, or run 'cheat DestroyWildDinos' (passive harvest on Die catches
// everything that dies). Results accumulate in ArkAP_dino_classes.jsonl.
static void DoDumpDinos(AShooterPlayerController* pc) {
    if (!pc) return;
    FVector ppos = ArkApi::GetApiUtils().GetPosition(pc);
    int before = (int)g_seenDinoClasses.size();
    for (AActor* a : ArkApi::GetApiUtils().GetAllActorsInRange(ppos, 500000.f, EServerOctreeGroup::DINOPAWNS)) {
        auto* d = static_cast<APrimalDinoCharacter*>(a);
        if (!d) continue;
        FString fn; d->GetFullName(&fn, nullptr);
        std::string full = fn.ToString();
        HarvestDinoClass(full.substr(0, full.find(' ')), RawDinoTag(d));
    }
    int total = (int)g_seenDinoClasses.size();
    std::wstring m = L"Harvested " + std::to_wstring(total - before) + L" new dino classes (" +
                     std::to_wstring(total) + L" total) -> ArkAP_dino_classes.jsonl";
    ChatNotify(m.c_str());
    DebugLog("DUMPDINOS new=" + std::to_string(total - before) + " total=" + std::to_string(total));
}
// ---- /dumppos <key> : capture ground-truth world coordinates for the EXPLORATION checks ----------
// The lat/lon -> world-coordinate formula is inconsistently documented, so the map regions are
// measured in-game rather than guessed: stand in a region and run this, as many times as you like.
// Each call appends one sample to ArkAP_positions.jsonl; tools/gen_explore.py turns the samples for
// a key into padded bounding box(es). See docs/EXPLORATION_MAPPING.md for the region key list.
static std::map<std::string, int> g_posSamples;      // key -> samples taken this session
static void DoDumpPos(AShooterPlayerController* pc, FString* message) {
    if (!pc) return;
    DumpMapGeo(pc);               // one-shot: the map's own lat/lon constants -> ArkAP_map_geo.json
    std::string text = message ? message->ToString() : std::string();
    auto sp = text.find(' ');
    std::string keyArg = (sp == std::string::npos) ? "" : text.substr(sp + 1);
    for (auto& ch : keyArg) ch = (char)std::tolower((unsigned char)ch);
    while (!keyArg.empty() && (unsigned char)keyArg.back() <= ' ') keyArg.pop_back();
    while (!keyArg.empty() && (unsigned char)keyArg.front() <= ' ') keyArg.erase(keyArg.begin());
    if (keyArg.empty()) {
        ChatNotify(L"Usage: /dumppos <region key>   e.g. /dumppos volcano   "
                   L"(keys are in docs/EXPLORATION_MAPPING.md)");
        return;
    }
    FVector p = ArkApi::GetApiUtils().GetPosition(pc);
    { std::ofstream f(PluginDir() / "ArkAP_positions.jsonl", std::ios::app);
      if (f) f << "{\"key\": \"" << keyArg << "\", \"x\": " << (long long)p.X
               << ", \"y\": " << (long long)p.Y << ", \"z\": " << (long long)p.Z << "}\n"; }
    int n = ++g_posSamples[keyArg];
    std::wstring m = L"Sample " + std::to_wstring(n) + L" for '" + ArkApi::Tools::Utf8Decode(keyArg) +
                     L"' recorded (" + std::to_wstring((long long)p.X) + L", " +
                     std::to_wstring((long long)p.Y) + L"). Walk the edges and repeat.";
    ChatNotify(m.c_str());
    DebugLog("DUMPPOS key=" + keyArg + " n=" + std::to_string(n) +
             " x=" + std::to_string((long long)p.X) + " y=" + std::to_string((long long)p.Y) +
             " z=" + std::to_string((long long)p.Z));
}
static void DumpPosChat(AShooterPlayerController* pc, FString* m, EChatSendMode::Type) {
    __try { DoDumpPos(pc, m); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

static void DumpDinosChat(AShooterPlayerController* pc, FString*, EChatSendMode::Type) {
    __try { DoDumpDinos(pc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// /whoami - show which AP route (survivor character name) this player resolves to, and whether
// multiplayer routing is on. The route must EXACTLY match the connector's ipc\<name> folder.
static void DoWhoAmI(AShooterPlayerController* pc) {
    std::string route = RouteFor(pc);
    std::wstring m = std::wstring(L"ArkAP: multiplayer=") + (g_multiplayer ? L"ON" : L"OFF (solo/shared)");
    if (g_multiplayer)
        m += L" | your route: '" + ArkApi::Tools::Utf8Decode(route) +
             L"' -> mailbox ipc\\" + ArkApi::Tools::Utf8Decode(route);
    ChatNotify(m.c_str());
    DebugLog("WHOAMI multiplayer=" + std::string(g_multiplayer ? "1" : "0") + " route=" + route);
}
static void WhoAmIChat(AShooterPlayerController* pc, FString*, EChatSendMode::Type) {
    __try { DoWhoAmI(pc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- embedded AP client: /connect <host:port> <slot> [password] / /disconnect / /apstatus ---
// The session runs on its own threads inside the plugin and drives the SAME mailbox files the
// external connector uses - so /connect and the external connector are interchangeable per slot
// (don't run both for the same player at once: they'd double-send).
static void DoApConnect(AShooterPlayerController* pc, FString* message) {
    if (!g_apManager) { ChatNotify(L"ArkAP: embedded AP client not initialised."); return; }
    std::vector<std::string> tok;
    { std::istringstream ss(message ? message->ToString() : std::string());
      std::string t; while (ss >> t) tok.push_back(t); }          // tok[0] = "/connect"
    if (tok.size() < 3) {
        ChatNotify(L"Usage: /connect <host>:<port> <slot> [password]  "
                   L"e.g. /connect archipelago.gg:38281 Alice");
        return;
    }
    // Accept EITHER order - "<host:port> <slot>" (new, AP convention) or "<slot> <host:port>"
    // (old, v76-v81) - so nobody's existing habit/instructions break. The address is the token
    // that parses as host:port (has a numeric port); the other is the slot. Password is always
    // tok[3+] in both layouts, so only the server/slot pick differs.
    std::string server, slot;
    if (ArkAP::ApParseServer(tok[1]).valid)      { server = tok[1]; slot = tok[2]; }
    else if (ArkAP::ApParseServer(tok[2]).valid) { server = tok[2]; slot = tok[1]; }
    else {
        ChatNotify(L"ArkAP: couldn't find a host and port in that command. "
                   L"Use /connect <host>:<port> <slot>  e.g. /connect archipelago.gg:38281 Alice");
        return;
    }
    std::string password;                        // room passwords may contain spaces
    for (size_t i = 3; i < tok.size(); ++i) { if (i > 3) password += " "; password += tok[i]; }
    std::string route = RouteFor(pc);            // multiplayer: this player's own mailbox
    // If the survivor name can't be resolved right now (still spawning in, respawn screen...)
    // the route degrades to "_unnamed" - binding the session there would deliver this slot's
    // items to a mailbox no player routes to (live-hit 2026-07-16). Refuse and ask to retry.
    if (g_multiplayer && route == "_unnamed") {
        ChatNotify(L"ArkAP: couldn't read your survivor name yet - spawn in fully, then run /connect again.");
        return;
    }
    std::string reply = g_apManager->Connect(route, slot, server, password);
    ChatNotify(ArkApi::Tools::Utf8Decode(reply).c_str());
    DebugLog("APCONNECT server=" + server + " slot=" + slot +
             (route.empty() ? "" : " route=" + route));
}
static void ApConnectChat(AShooterPlayerController* pc, FString* m, EChatSendMode::Type) {
    __try { DoApConnect(pc, m); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}
static void DoApDisconnect(AShooterPlayerController* pc) {
    if (!g_apManager) return;
    std::string reply = g_apManager->Disconnect(RouteFor(pc));
    ChatNotify(ArkApi::Tools::Utf8Decode(reply).c_str());
}
static void ApDisconnectChat(AShooterPlayerController* pc, FString*, EChatSendMode::Type) {
    __try { DoApDisconnect(pc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}
static void DoApStatus() {
    // No early return on a missing manager any more: /apstatus was the natural place to ask "what
    // version is this server on?", and it answered with silence on exactly the servers most likely
    // to be running something old (embedded client off, or offline mode).
    std::string s = "ArkAP " + std::string(ARKAP_BUILD) + " | ";
    s += g_apManager ? g_apManager->StatusAll()
                     : std::string("embedded AP client is off (external connector / offline mode)");
    s += " | engrams mapped=" + std::to_string(g_engramClassToItem.size());
    if (g_unresolvedEngrams)                       // never let this fail silently again
        s += ", UNRESOLVED=" + std::to_string(g_unresolvedEngrams) +
             " (those items cannot unlock - see ArkAP_debug.log)";
    if (!g_grantFailures.empty())                  // mapped, but the game is refusing them for now
        s += ", AWAITING PREREQS=" + std::to_string(g_grantFailures.size()) +
             " (engrams the game refuses until their prerequisite engram arrives - still retrying)";
    ChatNotify(ArkApi::Tools::Utf8Decode(s).c_str());
}
static void ApStatusChat(AShooterPlayerController*, FString*, EChatSendMode::Type) {
    __try { DoApStatus(); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- /confirm : apply randomize_dino_spawns to Game.ini and RESTART the server ---
// The embedded /connect client can't patch Game.ini live (ARK rewrites it from memory at a
// graceful shutdown, wiping the patch). So /confirm splices the connector-format fragment the
// client wrote (ipc\<route>\game_ini_fragment.txt) into Game.ini as a managed block, saves the
// world, spawns a detached helper that waits for THIS process to exit and relaunches the exact
// same command line, then HARD-terminates (no graceful shutdown => the patch survives). Net
// effect: the server restarts itself in a few seconds with randomized spawns live.
static const char* kIniSection = "[/script/shootergame.shootergamemode]";
static const char* kIniBegin   = "; === ArkAP NPCReplacements BEGIN (auto-managed, do not edit) ===";
static const char* kIniEnd     = "; === ArkAP NPCReplacements END ===";

static fs::path GameIniPath() {
    if (!g_gameIniOverride.empty()) return fs::path(g_gameIniOverride);
    std::error_code ec;
    fs::path cwd = fs::current_path(ec);                 // ...\ShooterGame\Binaries\Win64
    return cwd / ".." / ".." / "Saved" / "Config" / "WindowsServer" / "Game.ini";
}
// exact whole-line presence / removal (ARK's ini writer reorders and drops comments, so we can't
// rely on our BEGIN/END markers still being there - match the Config lines themselves).
static bool HasIniLine(const std::string& txt, const std::string& line) {
    size_t p = 0;
    while ((p = txt.find(line, p)) != std::string::npos) {
        size_t e = p + line.size();
        bool startOk = (p == 0) || txt[p - 1] == '\n';
        bool endOk = (e == txt.size()) || txt[e] == '\n' || txt[e] == '\r';
        if (startOk && endOk) return true;
        p = e;
    }
    return false;
}
static void RemoveIniLine(std::string& txt, const std::string& line) {
    size_t p = 0;
    while ((p = txt.find(line, p)) != std::string::npos) {
        size_t e = p + line.size();
        bool startOk = (p == 0) || txt[p - 1] == '\n';
        bool endOk = (e == txt.size()) || txt[e] == '\n' || txt[e] == '\r';
        if (startOk && endOk) {
            size_t del = e;
            if (del < txt.size() && txt[del] == '\r') ++del;
            if (del < txt.size() && txt[del] == '\n') ++del;
            txt.erase(p, del - p);
        } else {
            p = e;
        }
    }
}
static size_t CiFind(const std::string& hay, const std::string& needle) {
    auto it = std::search(hay.begin(), hay.end(), needle.begin(), needle.end(),
        [](char a, char b) { return tolower((unsigned char)a) == tolower((unsigned char)b); });
    return it == hay.end() ? std::string::npos : (size_t)(it - hay.begin());
}
// splice the fragment's Config* lines into iniPath as a managed block. 1=changed, 0=identical, -1=no fragment.
// dryRun = report what WOULD happen without writing (used by the /confirm prompt check).
static int PatchGameIniFromFragment(const fs::path& fragPath, const fs::path& iniPath,
                                    bool dryRun = false) {
    std::error_code ec;
    if (!fs::exists(fragPath, ec)) return -1;
    std::vector<std::string> cfg;
    { std::ifstream f(fragPath); std::string line;
      while (std::getline(f, line)) {
          if (!line.empty() && line.back() == '\r') line.pop_back();
          if (line.empty() || line[0] == '[') continue;              // skip blanks + section header
          if (line.rfind("ConfigOverrideNPCSpawnEntriesContainer", 0) == 0 ||
              line.rfind("ConfigAddNPCSpawnEntriesContainer", 0) == 0 ||
              line.rfind("NPCReplacements", 0) == 0) cfg.push_back(line);
      } }
    if (cfg.empty()) return -1;
    std::string block = std::string(kIniBegin) + "\n";
    for (auto& l : cfg) block += l + "\n";
    block += std::string(kIniEnd) + "\n";

    std::string txt;
    { std::ifstream f(iniPath, std::ios::binary); if (f) { std::stringstream ss; ss << f.rdbuf(); txt = ss.str(); } }

    // ALREADY APPLIED? Decide on the Config LINES, not our comment markers. ARK rewrites Game.ini
    // and strips comments, so after the restart the markers are gone even though the settings are
    // live - marker-based detection made /confirm think it was still pending and re-prompt (and
    // would have re-restarted) forever.
    bool allPresent = true;
    for (auto& l : cfg) if (!HasIniLine(txt, l)) { allPresent = false; break; }
    if (allPresent) return 0;

    std::string before = txt;
    for (auto& l : cfg) RemoveIniLine(txt, l);            // drop stragglers so we never duplicate
    size_t b = txt.find(kIniBegin);                       // remove any existing managed block
    if (b != std::string::npos) {
        size_t e = txt.find(kIniEnd, b);
        if (e != std::string::npos) {
            e += strlen(kIniEnd);
            if (e < txt.size() && txt[e] == '\r') ++e;
            if (e < txt.size() && txt[e] == '\n') ++e;
            txt.erase(b, e - b);
        }
    }
    size_t sec = CiFind(txt, kIniSection);                // insert under the section header (or append)
    if (sec == std::string::npos) {
        if (!txt.empty() && txt.back() != '\n') txt += "\n";
        txt += std::string(kIniSection) + "\n" + block;
    } else {
        size_t nl = txt.find('\n', sec);
        size_t at = (nl == std::string::npos) ? txt.size() : nl + 1;
        txt.insert(at, block);
    }
    if (txt == before) return 0;                          // already applied -> no restart needed
    if (dryRun) return 1;                                 // "would change" probe (prompt check)
    std::ofstream f(iniPath, std::ios::binary);
    if (!f) return -1;
    f << txt;
    return 1;
}
static std::string WideToUtf8(const wchar_t* w) {
    if (!w) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    std::string s(n > 0 ? n - 1 : 0, '\0');
    if (n > 0) WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], n, nullptr, nullptr);
    return s;
}
// Find the host's launcher by walking up from ...\ShooterGame\Binaries\Win64 to the server root and
// above (so E:\ARK\Server\... finds E:\ARK\Server\start_ase_server.bat or E:\ARK\start_ase_server.bat).
// Empty if absent -> the relauncher falls back to replaying our command line.
static fs::path FindRestartScript() {
    std::error_code ec;
    // Anchor on the EXE's own folder as well as the cwd: start_ase_server.bat launches
    // "%EXE%" without cd'ing, so the server's cwd is whatever folder the bat was run from.
    fs::path starts[2] = { fs::current_path(ec), {} };
    char exe[MAX_PATH]{};
    if (GetModuleFileNameA(nullptr, exe, MAX_PATH))
        starts[1] = fs::path(exe).parent_path();          // ...\ShooterGame\Binaries\Win64
    for (const fs::path& start : starts) {
        fs::path d = start;
        for (int i = 0; i < 6 && !d.empty(); ++i) {
            fs::path p = d / "start_ase_server.bat";
            if (fs::exists(p, ec)) return p;
            if (!d.has_parent_path() || d.parent_path() == d) break;
            d = d.parent_path();
        }
    }
    return {};
}

// The running map = the leading token of ARK's option string ("TheIsland?listen?SessionName=...").
// Parsed from our own command line so a scripted restart returns to the SAME map, not the script's
// default. Returns "" if it can't be identified - the launcher script then keeps its own MAP.
static std::string CurrentMapName() {
    std::string cl = WideToUtf8(GetCommandLineW());
    size_t i = 0;
    if (!cl.empty() && cl[0] == '"') {                    // skip a quoted argv[0]
        size_t q = cl.find('"', 1);
        i = (q == std::string::npos) ? cl.size() : q + 1;
    } else {
        size_t s = cl.find(' ');
        i = (s == std::string::npos) ? cl.size() : s;
    }
    while (i < cl.size() && (cl[i] == ' ' || cl[i] == '\t')) ++i;
    if (i < cl.size() && cl[i] == '"') ++i;
    std::string tok;
    for (; i < cl.size() && cl[i] != '"' && cl[i] != ' ' && cl[i] != '?'; ++i) tok += cl[i];
    if (!tok.empty() && tok[0] == '-') return "";         // a flag, not the map option string
    return tok;
}

// The command line gives ARK's own map name ("TheIsland", "ScorchedEarth_P", "Ragnarok"); our data
// files use short keys ("island", "scorched", "ragnarok"). Translate, tolerating the "_P" suffix
// several maps carry. Returns "" when the map is not one we know, which disables map filtering
// rather than guessing.
static std::string CurrentMapKey() {
    std::string m = CurrentMapName();
    for (auto& c : m) c = (char)std::tolower((unsigned char)c);
    if (m.size() > 2 && m.compare(m.size() - 2, 2, "_p") == 0) m.erase(m.size() - 2);
    static const std::pair<const char*, const char*> tbl[] = {
        {"theisland", "island"},         {"scorchedearth", "scorched"},
        {"aberration", "aberration"},    {"extinction", "extinction"},
        {"genesis", "genesis1"},         {"gen2", "genesis2"},
        {"thecenter", "center"},         {"ragnarok", "ragnarok"},
        {"valguero", "valguero"},        {"crystalisles", "crystalisles"},
        {"lostisland", "lostisland"},    {"fjordur", "fjordur"},
    };
    for (auto& kv : tbl) if (m == kv.first) return kv.second;
    return std::string();
}

// spawn a detached cmd that waits for OUR pid to vanish, then relaunches the server.
// Returns true only if the helper actually started - the caller MUST NOT kill the server otherwise
// (that's how you get "it closed and never came back").
static bool SpawnRelauncher() {
    std::string cl = WideToUtf8(GetCommandLineW());
    std::error_code ec;
    std::string cwd = fs::current_path(ec).string();
    DWORD pid = GetCurrentProcessId();
    fs::path bat = PluginDir() / "ap_restart.bat";
    fs::path rlog = PluginDir() / "ap_restart.log";
    { std::ofstream f(bat);
      if (!f) { DebugLog("RESTART: cannot write " + bat.string()); return false; }
      f << "@echo off\r\n"
        << "echo [%date% %time%] relauncher up, waiting for pid " << pid << " >> \"" << rlog.string() << "\"\r\n"
        // Block on the pid with Wait-Process. The old `tasklist | find` poll HUNG when the helper
        // ran detached: `find` reads stdin, and a pipeline in a process with no console/valid
        // stdin blocks forever (symptom: a stuck black window titled `find /i "ShooterGameServer"`).
        // Wait-Process needs no stdin and no polling; it returns instantly if the pid is already gone.
        << "powershell -NoProfile -ExecutionPolicy Bypass -Command \"try { Wait-Process -Id "
        << pid << " -Timeout 300 -ErrorAction Stop } catch { }\"\r\n"
        << "echo [%date% %time%] server exited - relaunching >> \"" << rlog.string() << "\"\r\n";
      // Preferred: re-run the host's own launcher (start_ase_server.bat, found by walking up from
      // the binaries dir) - it already knows the ports/cluster/save-dir and needs no command-line
      // reconstruction. Pass the RUNNING map as arg 1 (the script accepts it, same as
      // switch_map.bat) so a restart never silently comes back on the script's default MAP.
      // Fall back to replaying our own command line.
      // Launch with CALL, not START. `start "" "x.bat" "arg"` mis-parses (cmd glued it into one
      // token: '...x.bat"  "TheIsland' is not recognized), and every START quoting variant is a
      // guess. CALL takes plain quoted args with no title/parsing rules, and because the helper
      // now owns a real console (CREATE_NEW_CONSOLE), the server runs in it exactly as if the bat
      // had been double-clicked - keeping its -log output visible. The map arg is omitted entirely
      // when unknown (an empty "" would blank MAP in the script).
      // Wipe wild creatures on THIS boot only, so the new biome rosters repopulate immediately.
      // -ForceRespawnDinos is ARK's own startup flag: no admin rights, no player needed, unlike an
      // in-game DestroyWildDinos (whose cheat manager is null for a non-admin controller).
      // Launcher path: hand it over as an env var the script turns into the flag. Replay path: we
      // own the command line, so append it directly.
      fs::path sp = FindRestartScript();
      std::string map = CurrentMapName();
      if (!sp.empty()) {
          std::string dir = sp.parent_path().string();
          DebugLog("RESTART: using launcher " + sp.string() + " map=" + map);
          f << "set \"ARKAP_FORCE_RESPAWN=1\"\r\n"
            << "cd /d \"" << (dir.empty() ? cwd : dir) << "\"\r\n"
            << "call \"" << sp.string() << "\"";
          if (!map.empty()) f << " \"" << map << "\"";
          f << "\r\n";
      } else {
          DebugLog("RESTART: no start_ase_server.bat found - replaying command line");
          f << "cd /d \"" << cwd << "\"\r\n" << cl << " -ForceRespawnDinos\r\n";
      }
      f << "echo [%date% %time%] start issued, errorlevel=%errorlevel% >> \"" << rlog.string() << "\"\r\n"; }
    std::string cmd = "cmd.exe /c \"" + bat.string() + "\"";
    std::vector<char> mut(cmd.begin(), cmd.end()); mut.push_back('\0');
    // CREATE_BREAKAWAY_FROM_JOB fails with ERROR_ACCESS_DENIED when the server runs inside a job
    // object that doesn't allow breakaway (service wrappers, some panels) - retry without it.
    // CREATE_NEW_CONSOLE (not DETACHED_PROCESS): a detached helper has no console and no valid std
    // handles, which is what made the old `tasklist | find` poll hang on stdin. Its own console
    // also means the relaunched server inherits a normal console and keeps its -log output, just
    // like double-clicking the launcher. The window doubles as visible "restarting..." feedback.
    const DWORD flags[2] = { CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB,
                             CREATE_NEW_CONSOLE };
    for (int i = 0; i < 2; ++i) {
        STARTUPINFOA si{}; si.cb = sizeof(si);
        PROCESS_INFORMATION pi{};
        std::vector<char> arg(mut);
        if (CreateProcessA(nullptr, arg.data(), nullptr, nullptr, FALSE, flags[i],
                           nullptr, nullptr, &si, &pi)) {
            CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
            DebugLog("RESTART: relauncher spawned (attempt " + std::to_string(i + 1) + ")");
            return true;
        }
        DebugLog("RESTART: CreateProcess attempt " + std::to_string(i + 1) +
                 " failed, GetLastError=" + std::to_string(GetLastError()));
    }
    return false;
}
static void SafeSaveWorld() {                            // no C++ objects -> __try is legal here
    __try { auto* gm = ArkApi::GetApiUtils().GetShooterGameMode(); if (gm) gm->SaveWorld(true); }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
}
static void DoApConfirm(AShooterPlayerController* pc) {
    // find a spawn fragment in ANY mailbox (the player who /connect'd with randomize_dino_spawns
    // wrote it; any player may /confirm).
    fs::path frag, ini = GameIniPath();
    for (auto& route : MailboxRoutes()) {
        fs::path p = g_ipc->DirFor(route) / "game_ini_fragment.txt";
        std::error_code ec; if (fs::exists(p, ec)) { frag = p; break; }
    }
    if (frag.empty()) { ChatNotify(L"ArkAP: no randomized-spawns config to apply (nothing pending)."); return; }
    int r = PatchGameIniFromFragment(frag, ini);
    if (r < 0) { ChatNotify(L"ArkAP: couldn't read the spawn fragment / Game.ini - nothing changed."); return; }
    if (r == 0) { ChatNotify(L"ArkAP: randomized spawns are already applied - no restart needed."); return; }
    DebugLog("CONFIRM: Game.ini patched from " + frag.string() + " -> restarting");
    // Start the relauncher BEFORE killing anything. If it can't start, stay up and say so -
    // never leave the host with a closed server and no way back.
    if (!SpawnRelauncher()) {
        ChatNotify(L"ArkAP: Game.ini updated, but the auto-restart helper could not start. "
                   L"The server is still running - restart it manually to apply randomized spawns.");
        DebugLog("CONFIRM: relauncher failed to spawn - NOT terminating (manual restart needed)");
        return;
    }
    ChatNotify(L"ArkAP: saving the world and restarting the server to apply randomized spawns "
               L"(back in ~15s). Wild creatures are wiped on that restart (-ForceRespawnDinos) so "
               L"the new rosters repopulate.");
    SafeSaveWorld();
    Sleep(750);                                           // let the save flush + helper spin up
    TerminateProcess(GetCurrentProcess(), 0);             // hard exit: ARK never rewrites Game.ini
}
static void ApConfirmChat(AShooterPlayerController* pc, FString*, EChatSendMode::Type) {
    __try { DoApConfirm(pc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- /hint <item> : reveal WHERE an item is, FREE (no in-game resource cost). Writes the item name
// to hint_out.jsonl; the connector / embedded client runs AP's !hint and relays the result (which
// reads "<item> is at <location> in <finder>'s world"). AP's own hint-point economy still applies
// server-side (set the room's hint_cost to 0 to make hints fully free there too). ---
// count a resource the player holds, by class-name substring. Used by the inventory "Collect N X"
// location checks (g_invChecks) - NOT by hints anymore.
// Standard ray-cast: count crossings of a horizontal ray to the player's right. Works for any
// simple polygon, convex or not, and tolerates a loop that closes slightly past its start (the
// crossing flips a sliver, never the whole region).
static bool PointInPolygon(double x, double y, const std::vector<std::pair<double, double>>& poly) {
    bool in = false;
    const size_t n = poly.size();
    if (n < 3) return false;
    for (size_t i = 0, j = n - 1; i < n; j = i++) {
        const double xi = poly[i].first,  yi = poly[i].second;
        const double xj = poly[j].first,  yj = poly[j].second;
        if (((yi > y) != (yj > y)) && x < (xj - xi) * (y - yi) / (yj - yi) + xi)
            in = !in;
    }
    return in;
}
// Inside ANY part of a multi-shape region. Parts are disjoint by construction, so the first hit
// is the answer - no need to keep testing.
static bool PointInAnyPart(double x, double y,
                           const std::vector<std::vector<std::pair<double, double>>>& parts) {
    for (auto& p : parts) if (PointInPolygon(x, y, p)) return true;
    return false;
}

static int CountResource(UPrimalInventoryComponent* inv, const std::string& cls) {
    int total = 0;
    for (UPrimalItem* it : inv->InventoryItemsField()) {
        if (!it) continue;
        FString fn; it->GetFullName(&fn, nullptr);
        if (fn.ToString().find(cls) != std::string::npos) total += it->ItemQuantityField();
    }
    return total;
}
// fuzzy-match a query against the AP item names. returns id (0 = none) + fills name.
static int MatchItem(const std::string& query, std::string& name) {
    std::string ql = query; for (auto& ch : ql) ch = (char)std::tolower((unsigned char)ch);
    for (auto& [id, nm] : g_tables.item_name) {
        std::string nl = nm; for (auto& ch : nl) ch = (char)std::tolower((unsigned char)ch);
        if (nl.find(ql) != std::string::npos) { name = nm; return id; }
    }
    return 0;
}
static std::string HintQuery(FString* message) {     // text after the command word
    if (!message) return "";
    std::string text = message->ToString();
    auto sp = text.find(' ');
    std::string q = (sp == std::string::npos) ? "" : text.substr(sp + 1);
    while (!q.empty() && (unsigned char)q.back() <= ' ') q.pop_back();
    while (!q.empty() && (unsigned char)q.front() <= ' ') q.erase(q.begin());
    return q;
}
// --- /apcheck [text] : show what the plugin actually counts for the inventory checks ---
// "I have 20 seeds but the check isn't checking" is unanswerable from chat: the player sees their
// stack, the plugin sees a class-name substring count over their OWN inventory only. This prints
// both sides so the mismatch is obvious (wrong container, wrong variant, or already checked).
static void DoApCheck(AShooterPlayerController* pc, FString* message) {
    if (!pc) return;
    AShooterCharacter* ch = pc->GetPlayerCharacter();
    UPrimalInventoryComponent* inv = ch ? ch->MyInventoryComponentField() : nullptr;
    if (!inv) { ChatNotify(L"ArkAP: no inventory - spawn in first."); return; }
    std::string q = HintQuery(message);
    for (auto& c : q) c = (char)std::tolower((unsigned char)c);
    std::string route = RouteFor(pc);
    int shown = 0, matched = 0;
    for (auto& ic : g_invChecks) {
        std::string name = ic.name.empty() ? ic.cls : ic.name;
        if (!q.empty()) {
            std::string nl = name; for (auto& c : nl) c = (char)std::tolower((unsigned char)c);
            if (nl.find(q) == std::string::npos) continue;
        }
        ++matched;
        int have = CountResource(inv, ic.cls);
        if (q.empty() && (have == 0 || g_state->AlreadyChecked(route, ic.loc))) continue;
        if (++shown > 8) break;
        // the CLASS is printed too: if the plugin folder still has an old locations.json the
        // string shown here will not match what /apdumpinv reports, which is the whole answer.
        std::string line = name + ": " + std::to_string(have) + " / " + std::to_string(ic.qty) +
                           "  [" + ic.cls + "]" +
                           (g_state->AlreadyChecked(route, ic.loc) ? "  (already sent)" : "");
        ChatNotify(ArkApi::Tools::Utf8Decode(line).c_str());
    }
    if (!shown)
        ChatNotify(matched ? L"ArkAP: nothing in YOUR inventory counts toward those checks - "
                             L"items in a box, crop plot or on a dino are not counted."
                           : L"ArkAP: no inventory check matches that text.");
}
static void ApCheckChat(AShooterPlayerController* pc, FString* message, EChatSendMode::Type) {
    __try { DoApCheck(pc, message); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- /apdumpinv : write the REAL class name of every item you are carrying ---
// Every inventory check and every filler give is a class-name string, and there is no reliable
// offline source for those - Plant Species X Seed is class Seed_DefensePlant, not
// Seed_PlantSpeciesX, which silently broke its check. Guessing has cost us twice now, so this
// dumps ground truth: carry one of whatever is in doubt, run the command, read the file.
// Output: ArkAP_item_classes.jsonl next to ArkAP.dll (appended, deduped per server run).
static void DoDumpInv(AShooterPlayerController* pc) {
    if (!pc) return;
    AShooterCharacter* ch = pc->GetPlayerCharacter();
    UPrimalInventoryComponent* inv = ch ? ch->MyInventoryComponentField() : nullptr;
    if (!inv) { ChatNotify(L"ArkAP: no inventory - spawn in first."); return; }
    static std::set<std::string> seen;
    int added = 0, total = 0;
    std::ofstream f(PluginDir() / "ArkAP_item_classes.jsonl", std::ios::app);
    for (UPrimalItem* it : inv->InventoryItemsField()) {
        if (!it) continue;
        ++total;
        FString fn; it->GetFullName(&fn, nullptr);
        std::string full = fn.ToString();
        std::string cls = full.substr(0, full.find(' '));      // "<Class> <path>"
        if (!seen.insert(cls).second) continue;
        ++added;
        if (f) f << "{\"class\": \"" << cls << "\", \"qty\": " << it->ItemQuantityField() << "}\n";
        DebugLog("ITEMCLASS " + cls);
    }
    std::wstring m = L"ArkAP: dumped " + std::to_wstring(added) + L" new class name(s) from " +
                     std::to_wstring(total) + L" stack(s) -> ArkAP_item_classes.jsonl";
    ChatNotify(m.c_str());
}
static void ApDumpInvChat(AShooterPlayerController* pc, FString*, EChatSendMode::Type) {
    __try { DoDumpInv(pc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- /apresync : forget which locations we believe we already sent, and re-scan ---
// The seed-change reset only fires when the seed CHANGES. A save that was already poisoned by a
// previous seed's checked set (the state file predates that fix) records the current seed, so the
// automatic path never runs for it and those locations stay permanently dead. This is the manual
// escape hatch. Only checked_ is cleared - received_ keeps the player's items/engrams - and the
// next tick re-reports everything they still satisfy. Anything AP already knows is deduped there,
// so running it is harmless.
static void DoApResync(AShooterPlayerController* pc) {
    if (!pc) return;
    std::string route = RouteFor(pc);
    g_state->ResetChecked(route);
    DebugLog("RESYNC cleared checked set" + std::string(route.empty() ? "" : " [" + route + "]"));
    ChatNotify(L"ArkAP: re-scanning. Levels, inventory and tame checks you already satisfy will "
               L"re-report over the next few seconds; anything Archipelago already has is ignored.");
}
static void ApResyncChat(AShooterPlayerController* pc, FString*, EChatSendMode::Type) {
    __try { DoApResync(pc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- /aprecover : rebuild the RECEIVED-items set from Archipelago ---
// If state.json is lost, received_ goes empty and taming/crates re-lock. Reasserting cannot fix
// that: the applied-index watermark says every item was already applied, so the plugin never
// re-processes them and the set never refills - the player stays locked out permanently. Clearing
// the watermark makes the next poll re-apply Archipelago's full item list, which rebuilds it.
// Filler effects re-fire as a side effect (a one-off shower of resources) - a fair price for
// getting taming back, and stated up front.
static void DoApRecover(AShooterPlayerController* pc) {
    if (!pc) return;
    std::string route = RouteFor(pc);
    std::error_code ec;
    fs::remove(WatermarkPath(route), ec);                   // re-apply from items_in.jsonl
    // NOT session.json - see the note in DoAutoRecoverLostState. Removing it fakes a seed change
    // and wipes this route's checks and boss defeats along with it.
    g_quietUntil[route] = std::time(nullptr) + 180;          // silent while the list comes back
    DebugLog("RECOVER cleared applied-index watermark" +
             std::string(route.empty() ? "" : " [" + route + "]"));
    ChatNotify(L"ArkAP: rebuilding your unlocks from Archipelago. Reconnect with /connect if "
               L"nothing arrives in ~10s. Filler items will re-trigger once - that is expected.");
}
static void ApRecoverChat(AShooterPlayerController* pc, FString*, EChatSendMode::Type) {
    __try { DoApRecover(pc); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

static void DoHint(AShooterPlayerController* pc, FString* message) {
    std::string q = HintQuery(message);
    if (q.empty()) { ChatNotify(L"Usage: /hint <item name>"); return; }
    std::string name; int id = MatchItem(q, name);
    if (!id) { ChatNotify((L"No item matches '" + ArkApi::Tools::Utf8Decode(q) + L"'").c_str()); return; }
    std::string route = RouteFor(pc);              // the asker's own slot receives the hint
    // A BUNDLED item (count-group member, S+ variant, structure-bundle member, mod-group member, or
    // a saddle bundled with its tame) is never a placed AP item, so hinting it fails with "item
    // doesn't exist in the multiworld". Redirect to whatever item actually unlocks it. The apworld
    // builds this map (hint_redirect) because it is the authority on what got pooled.
    std::string via;
    auto hrit = g_routeHintRedirect.find(route);
    if (hrit != g_routeHintRedirect.end()) {
        auto rit = hrit->second.find(id);
        if (rit != hrit->second.end()) {
            auto rn = g_tables.item_name.find(rit->second);
            if (rn != g_tables.item_name.end()) { via = name; name = rn->second; id = rit->second; }
        }
    }
    if (via.empty()) {                              // fallback for seeds without hint_redirect
        auto grit = g_routeItemGroups.find(route);
        if (grit != g_routeItemGroups.end())
            for (auto& [rep, members] : grit->second)
                if (std::find(members.begin(), members.end(), id) != members.end()) {
                    auto rn = g_tables.item_name.find(rep);
                    if (rn != g_tables.item_name.end()) { via = name; name = rn->second; id = rep; }
                    break;
                }
    }
    { std::ofstream f(g_ipc->DirFor(route) / "hint_out.jsonl", std::ios::app); if (f) f << name << "\n"; }
    std::wstring m = L"Revealing hint for " + ArkApi::Tools::Utf8Decode(name);
    if (!via.empty()) m += L" (the unlock that grants " + ArkApi::Tools::Utf8Decode(via) + L")";
    // list everything that unlock also grants, so the player knows what the hint really buys
    auto gsit = g_routeItemGroups.find(route);
    if (gsit != g_routeItemGroups.end()) {
        auto mit = gsit->second.find(id);
        if (mit != gsit->second.end() && !mit->second.empty()) {
            std::string also;
            for (int mem : mit->second) {
                auto mn = g_tables.item_name.find(mem);
                if (mn == g_tables.item_name.end() || mn->second == via) continue;
                if (!also.empty()) also += ", ";
                also += mn->second;
            }
            if (!also.empty()) m += L" [also unlocks " + ArkApi::Tools::Utf8Decode(also) + L"]";
        }
    }
    m += L"...";
    ChatNotify(m.c_str());
}
static void HintChat(AShooterPlayerController* pc, FString* m, EChatSendMode::Type) { __try { DoHint(pc, m); } __except (EXCEPTION_EXECUTE_HANDLER) {} }

// In-game chat versions (the dedicated-server console window isn't interactive here).
// Type "/dumpengrams", "/dumpnotes", or "/buildregistry" in chat.
static void DumpEngramsChat(AShooterPlayerController*, FString*, EChatSendMode::Type) { DumpEngrams(nullptr, nullptr, false); }
static void DumpNotesChat(AShooterPlayerController*, FString*, EChatSendMode::Type) { DumpNotes(nullptr, nullptr, false); }
static void BuildRegistryChat(AShooterPlayerController*, FString*, EChatSendMode::Type) { BuildEngramRegistry(); }

// One game-thread tick: poll the connector once Ready. Whole body is SEH-guarded so
// nothing here can take down the server while we stabilise.
// Drain hook-written note_queue.jsonl on the game thread (safe to message / report).
// queue lines are "<payload>\t<route>" (legacy lines have no tab -> route "").
static void SplitQueueLine(const std::string& line, std::string& payload, std::string& route) {
    auto tb = line.find('\t');
    if (tb == std::string::npos) { payload = line; route = ""; }
    else { payload = line.substr(0, tb); route = line.substr(tb + 1); }
}

static void DoProcessPending() {
    static std::set<std::string> processedNotes;              // "route|idx"
    std::vector<std::pair<int, std::string>> notes;
    {   std::ifstream f(PluginDir() / "note_queue.jsonl");
        std::string line, payload, route;
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            SplitQueueLine(line, payload, route);
            try { int idx = std::stoi(payload);
                  if (processedNotes.insert(route + "|" + payload).second) notes.emplace_back(idx, route); }
            catch (...) {}
        }
    }

    // Notes auto-granted on (re)spawn, not real collectibles -> never a check.
    // 1216 was caught by NOTEPOS: it fires at world (400000, 400000), which is lat/lon 100/100 -
    // the corner of the coordinate space, not anywhere a player can stand - and it arrives the
    // instant they join. It also appears on no map's note list in the wiki or the reference sheet.
    static const std::set<int> kSkipNotes = { 1214, 1216 };

    for (auto& [idx, route] : notes) {
        if (kSkipNotes.count(idx)) { DebugLog("NOTE idx=" + std::to_string(idx) + " skipped (spawn note)"); continue; }
        { std::ofstream f(PluginDir() / "ArkAP_note_hits.jsonl", std::ios::app);
          if (f) f << "{\"note_index\": " << idx << "}\n"; }
        auto it = g_tables.note_index_to_loc.find(idx);
        if (it != g_tables.note_index_to_loc.end()) {
            DebugLog("NOTE idx=" + std::to_string(idx) + " -> loc=" + std::to_string(it->second));
            ReportLocation(route, it->second);
        } else {
            DebugLog("NOTE idx=" + std::to_string(idx) + " (not mapped)");
        }
    }

    // per-species tame checks: drain tame_check_queue.jsonl ("tag\troute") -> "Tamed: X" loc.
    {   static std::set<std::string> processedTames;          // "route|tag"
        std::ifstream f(PluginDir() / "tame_check_queue.jsonl");
        std::string line, tag, route;
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            SplitQueueLine(line, tag, route);
            if (tag.empty() || !processedTames.insert(route + "|" + tag).second) continue;
            auto it = g_tameTagToTameLoc.find(tag);
            if (it != g_tameTagToTameLoc.end()) {
                DebugLog("TAME-CHECK tag=" + tag + " -> loc=" + std::to_string(it->second));
                ReportLocation(route, it->second);
            }
        }
    }

    // first-kill checks: drain kill_check_queue.jsonl ("tag\troute") -> "Killed: X" loc.
    {   static std::set<std::string> processedKills;          // "route|tag"
        std::ifstream f(PluginDir() / "kill_check_queue.jsonl");
        std::string line, tag, route;
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            SplitQueueLine(line, tag, route);
            if (tag.empty() || !processedKills.insert(route + "|" + tag).second) continue;
            auto it = g_killTagToLoc.find(tag);
            if (it != g_killTagToLoc.end()) {
                DebugLog("KILL-CHECK tag=" + tag + " -> loc=" + std::to_string(it->second));
                ReportLocation(route, it->second);
            }
        }
    }

    // level + inventory checks: PER CONNECTED PLAYER (their own route, level, and inventory).
    {   UWorld* world = ArkApi::GetApiUtils().GetWorld();
        if (world) for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
            auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
            if (!pc) continue;
            AShooterCharacter* ch = pc->GetPlayerCharacter();
            if (!ch) continue;
            std::string route = RouteFor(pc);
            auto* st = ch->MyCharacterStatusComponentField();
            int plvl = st ? st->BaseCharacterLevelField() + st->ExtraCharacterLevelField() : 0;
            if (plvl > 0) for (auto& [lvl, loc] : g_tables.level_to_loc)
                if (plvl >= lvl) ReportLocation(route, loc);
            if (!g_explore.empty() || !g_depth.empty()) {   // exploration: where is this player?
                FVector pos = ArkApi::GetApiUtils().GetPosition(pc);
                for (auto& da : g_depth)                    // deep water: purely a Z floor
                    if (!g_state->AlreadyChecked(route, da.loc) && pos.Z < da.zBelow) {
                        DebugLog("EXPLORE " + da.name + " (z " + std::to_string((long long)pos.Z) +
                                 " < " + std::to_string((long long)da.zBelow) + ") -> loc=" +
                                 std::to_string(da.loc));
                        ReportLocation(route, da.loc);
                    }
                for (auto& ea : g_explore)
                    if (!g_state->AlreadyChecked(route, ea.loc) &&
                        PointInAnyPart(pos.X, pos.Y, ea.parts)) {
                        DebugLog("EXPLORE " + ea.name + " -> loc=" + std::to_string(ea.loc) +
                                 (route.empty() ? "" : " [" + route + "]"));
                        ReportLocation(route, ea.loc);
                    }
            }
            UPrimalInventoryComponent* inv = ch->MyInventoryComponentField();
            if (inv) for (auto& ic : g_invChecks)
                if (!g_state->AlreadyChecked(route, ic.loc) && CountResource(inv, ic.cls) >= ic.qty)
                    ReportLocation(route, ic.loc);
        }
    }

    // collective counters: load once, then drain new events_queue.jsonl lines (persisted pos
    // so a restart neither loses nor double-counts events).
    static long long queuePos = 0;
    if (!g_countersLoaded) {
        g_countersLoaded = true;
        try { fs::path p = PluginDir() / "counters.json";
              if (fs::exists(p)) { nlohmann::json j; std::ifstream(p) >> j;
                  queuePos = j.value("queue_pos", 0ll);
                  // legacy flat totals -> the "" shared route
                  g_totalTames[""] = j.value("tames", 0); g_totalKills[""] = j.value("kills", 0);
                  g_totalBreeds[""] = j.value("breeds", 0);
                  g_totalDeaths[""] = j.value("deaths", 0);
                  for (auto& [name, pl] : j.value("players", nlohmann::json::object()).items()) {
                      g_totalTames[name] = pl.value("tames", 0);
                      g_totalKills[name] = pl.value("kills", 0);
                      g_totalBreeds[name] = pl.value("breeds", 0);
                      g_totalDeaths[name] = pl.value("deaths", 0);
                  }
              }
        } catch (...) {}
    }
    {   std::vector<std::string> lines;
        {   std::ifstream f(PluginDir() / "events_queue.jsonl");
            std::string line;
            while (std::getline(f, line)) if (!line.empty()) lines.push_back(line);
        }
        if ((long long)lines.size() < queuePos) queuePos = 0;   // queue reset -> resync
        if ((long long)lines.size() > queuePos) {
            for (size_t i = (size_t)queuePos; i < lines.size(); ++i) {
                std::string kind, route;
                SplitQueueLine(lines[i], kind, route);
                if (kind == "tame")       ++g_totalTames[route];
                else if (kind == "kill")  ++g_totalKills[route];
                else if (kind == "breed") ++g_totalBreeds[route];
                else if (kind == "death") ++g_totalDeaths[route];
            }
            queuePos = (long long)lines.size();
            try {
                nlohmann::json players = nlohmann::json::object();
                std::set<std::string> names;
                for (auto& [n, _] : g_totalTames)  names.insert(n);
                for (auto& [n, _] : g_totalKills)  names.insert(n);
                for (auto& [n, _] : g_totalBreeds) names.insert(n);
                for (auto& [n, _] : g_totalDeaths) names.insert(n);
                for (auto& n : names)
                    players[n] = { {"tames", g_totalTames[n]}, {"kills", g_totalKills[n]},
                                   {"breeds", g_totalBreeds[n]}, {"deaths", g_totalDeaths[n]} };
                nlohmann::json out; out["players"] = players; out["queue_pos"] = queuePos;
                std::ofstream(PluginDir() / "counters.json") << out.dump();
            } catch (...) {}
        }
    }

    // count milestones PER ROUTE. collective = that route's counters; species = distinct checked
    // "Tamed/Killed: X" locs in that route's state; notes = distinct checked note locs.
    for (auto& route : KnownRoutes()) {
        int tameSpecies = 0; for (auto& [t, loc] : g_tameTagToTameLoc) if (g_state->AlreadyChecked(route, loc)) ++tameSpecies;
        int killSpecies = 0; for (auto& [t, loc] : g_killTagToLoc)     if (g_state->AlreadyChecked(route, loc)) ++killSpecies;
        int noteCnt = 0; for (auto& [i, loc] : g_tables.note_index_to_loc) if (g_state->AlreadyChecked(route, loc)) ++noteCnt;
        int totTame = g_totalTames.count(route) ? g_totalTames[route] : 0;
        int totKill = g_totalKills.count(route) ? g_totalKills[route] : 0;
        int totBreed = g_totalBreeds.count(route) ? g_totalBreeds[route] : 0;
        int totDeath = g_totalDeaths.count(route) ? g_totalDeaths[route] : 0;
        int exploreCnt = 0;                          // how many regions this player has visited
        for (auto& ea : g_explore) if (g_state->AlreadyChecked(route, ea.loc)) ++exploreCnt;
        for (auto& da : g_depth)   if (g_state->AlreadyChecked(route, da.loc)) ++exploreCnt;
        for (auto& [tag, loc] : g_tables.milestone_tag_to_loc) {
            if (tag == "milestone_first_tame") {          // reliable: any tame (the collective counter)
                if (totTame >= 1) ReportLocation(route, loc);
                continue;
            }
            if (tag == "milestone_first_breed") {
                if (totBreed >= 1) ReportLocation(route, loc);
                continue;
            }
            auto us = tag.rfind('_');
            if (us == std::string::npos) continue;
            int n = 0; try { n = std::stoi(tag.substr(us + 1)); } catch (...) { continue; }
            if      (tag.rfind("milestone_tametotal_", 0) == 0 && totTame >= n) ReportLocation(route, loc);
            else if (tag.rfind("milestone_killtotal_", 0) == 0 && totKill >= n) ReportLocation(route, loc);
            else if (tag.rfind("milestone_breedtotal_", 0) == 0 && totBreed >= n) ReportLocation(route, loc);
            else if (tag.rfind("milestone_deaths_", 0) == 0 && totDeath >= n) ReportLocation(route, loc);
            else if (tag.rfind("milestone_explore_", 0) == 0 && exploreCnt >= n) ReportLocation(route, loc);
            else if (tag.rfind("milestone_tames_", 0) == 0 && tameSpecies >= n) ReportLocation(route, loc);
            else if (tag.rfind("milestone_kills_", 0) == 0 && killSpecies >= n) ReportLocation(route, loc);
            else if (tag.rfind("milestone_notes_", 0) == 0 && noteCnt >= n) ReportLocation(route, loc);
        }
    }
}
static void ProcessPendingChecks() {
    __try { DoProcessPending(); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// DeathLink in: each slot's connector appends to ITS death_in.jsonl -> kill that slot's player
// (route "" = everyone, the solo/shared behavior).
static void DoApplyDeaths() {
    static std::map<std::string, size_t> processed;
    static std::set<std::string> inited;
    for (auto& route : MailboxRoutes()) {
        size_t count = 0;
        {   std::ifstream f(g_ipc->DirFor(route) / "death_in.jsonl");
            std::string line;
            while (std::getline(f, line)) if (!line.empty()) ++count;
        }
        if (!inited.count(route)) {                  // first tick: swallow any stale backlog so a
            inited.insert(route);                    // server restart doesn't kill the player on boot
            processed[route] = count;
            if (count) DebugLog("DEATHLINK backlog skipped on startup: " + std::to_string(count));
            continue;
        }
        if (count <= processed[route]) { if (count < processed[route]) processed[route] = count; continue; }
        processed[route] = count;
        UWorld* world = ArkApi::GetApiUtils().GetWorld();
        if (!world) continue;
        g_suppressDeathUntil[route] = std::time(nullptr) + 5;   // the kill below must not rebroadcast
        int killed = 0;
        for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
            auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
            if (!pc) continue;
            if (!route.empty() && RouteFor(pc) != route) continue;   // only this slot's player
            AShooterCharacter* ch = pc->GetPlayerCharacter();
            if (!ch) continue;
            FDamageEvent dmg;                        // generic lethal damage via the real Die (trampoline,
            AShooterCharacter_Die_original(ch, 1000000.f, &dmg, nullptr, nullptr);  // so our hook doesn't re-fire)
            ++killed;
        }
        DebugLog("DEATHLINK received -> killed " + std::to_string(killed) + " player(s)" +
                 (route.empty() ? "" : " [" + route + "]"));
    }
}
static void ApplyDeaths() {
    __try { DoApplyDeaths(); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// Show item-flow lines the connectors queued (e.g. "Ghios sent Engram: Bow to Zero").
static void DoApplyMessages() {
    static std::map<std::string, size_t> processed;
    static std::set<std::string> inited;
    for (auto& route : MailboxRoutes()) {
        std::vector<std::string> lines;
        {   std::ifstream f(g_ipc->DirFor(route) / "msg_in.jsonl");
            std::string line;
            while (std::getline(f, line)) if (!line.empty()) lines.push_back(line);
        }
        if (!inited.count(route)) {                  // first tick: don't replay old chat history
            inited.insert(route);
            processed[route] = lines.size();
            continue;
        }
        auto& pos = processed[route];
        if (lines.size() <= pos) { if (lines.size() < pos) pos = lines.size(); continue; }
        for (size_t i = pos; i < lines.size(); ++i) {
            std::wstring w = ArkApi::Tools::Utf8Decode(lines[i]);
            ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), w.c_str());
        }
        pos = lines.size();
    }
}
static void ApplyMessages() {
    __try { DoApplyMessages(); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// Is anyone actually in-world? A PlayerController can exist while the player is still loading, so
// require a spawned character - otherwise chat lines go nowhere and console commands no-op. Every
// ONE-SHOT announcement must gate on this: the state that says "already handled" is only allowed
// to advance once there is somebody to receive it (this is what silently ate the /confirm prompt,
// the connect status and the wild-dino wipe on an auto-resumed server start).
static AShooterPlayerController* FirstReadyPlayer() {
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) return nullptr;
    for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (pc && pc->GetPlayerCharacter()) return pc;
    }
    return nullptr;
}

// Embedded AP client connection status -> chat. Each session overwrites conn_status.txt with
// "<seq>\t<message>"; we announce it whenever <seq> changes. Because it's a single overwritten
// line (not an append log), the CURRENT status is shown once after a server restart too (the
// resumed session's connect writes seq>=1, our first tick shows it) - so a player always learns
// whether the room is connected/disconnected without the msg_in boot-swallow eating it.
static void DoShowConnStatus() {
    static std::map<std::string, long long> shown;
    // Nobody in-world yet (server just came up and auto-resumed the session): do NOT consume the
    // seq here, or the player who joins a moment later never learns they're connected.
    if (!FirstReadyPlayer()) return;
    for (auto& route : MailboxRoutes()) {
        fs::path p = g_ipc->DirFor(route) / "conn_status.txt";
        std::error_code ec;
        if (!fs::exists(p, ec)) continue;
        std::string line;
        { std::ifstream f(p); if (!std::getline(f, line)) continue; }
        auto tab = line.find('\t');
        if (tab == std::string::npos) continue;
        long long seq = 0;
        try { seq = std::stoll(line.substr(0, tab)); } catch (...) { continue; }
        auto it = shown.find(route);
        if (it != shown.end() && it->second == seq) continue;   // already announced this state
        shown[route] = seq;
        // Tag the connect/disconnect line with the build too - it is the one message everybody
        // sees at the start of a session, so a screenshot of it is enough to answer "what are you
        // running?" without anyone digging for a log file.
        std::string msg = line.substr(tab + 1) + "  [ArkAP " + ARKAP_BUILD + "]";
        ChatNotify(ArkApi::Tools::Utf8Decode(msg).c_str());
    }
}
static void ShowConnStatus() { __try { DoShowConnStatus(); } __except (EXCEPTION_EXECUTE_HANDLER) {} }

// Tell a player their CURRENT AP connection state when they JOIN. DoShowConnStatus only fires on a
// status CHANGE (the conn_status seq) and records it for the whole server session, so a rejoin with
// nothing changed said nothing.
//
// Keyed on the PlayerController POINTER. That's the bit that makes join-vs-respawn work:
//   * a REJOIN builds a NEW controller  -> unseen pointer -> greet
//   * a RESPAWN reuses the SAME controller (only the character is replaced) -> already greeted
// Keying on the survivor NAME failed both ways: a logged-out controller lingers in the list (name
// never cleared, so rejoins were silent) and death briefly nulls the character (name dropped, so
// respawning falsely re-greeted). Hooking AShooterGameMode.HandleNewPlayer_Implementation was tried
// instead and never fired at all - no GREET line ever reached the log - so this stays on the tick.
// Stale pointers are only ever COMPARED against the live list, never dereferenced.
static void DoGreetPlayer(AShooterPlayerController* pc) {
    if (!pc) return;
    std::string status;
    if (g_ipc) {                                                 // this survivor's own mailbox
        fs::path box = g_ipc->DirFor(RouteFor(pc));
        std::ifstream f(box / "conn_status.txt");
        std::string line;
        if (std::getline(f, line)) {
            auto tab = line.find('\t');
            if (tab != std::string::npos) status = line.substr(tab + 1);
        }
        // conn_status.txt is written on connect and never again, so its "(N locations remaining)"
        // is frozen at connect time - three greets an hour apart all quoted the same number in
        // Lurch's log, which reads as "my checks aren't registering". Swap in the live count.
        std::error_code ec;
        if (!status.empty() && fs::exists(box / "remaining.json", ec)) {
            int live = -1;
            try { nlohmann::json j; std::ifstream(box / "remaining.json") >> j;
                  live = j.value("remaining", -1); } catch (...) {}
            size_t open = status.rfind(" (");
            if (live >= 0 && open != std::string::npos && status.back() == ')')
                status = status.substr(0, open) + " (" + std::to_string(live) +
                         " locations remaining)";
        }
    }
    if (status.empty())
        status = "AP: not connected - use /connect <host>:<port> <slot> to link this survivor.";
    status += "  [ArkAP " + std::string(ARKAP_BUILD) + "]";   // so a bug report names its build
    // pass the text as an ARGUMENT: FString::Format is fmt-style, so braces in a survivor name
    // would otherwise be treated as a format field.
    ArkApi::GetApiUtils().SendChatMessage(pc, FString(L"Archipelago"), L"{}",
                                          ArkApi::Tools::Utf8Decode(status));
    DebugLog("GREET (sent to client) " + ArkApi::GetApiUtils().GetCharacterName(pc).ToString()
             + " -> " + status);
}
static std::set<void*> g_greeted;                                // controller identity, never deref'd
static std::map<void*, long long> g_greetDue;                    // controller -> when to send
static const int GREET_DELAY_SEC = 8;                            // client needs time before chat shows

void ForgetGreeted(void* pc) {                                   // called from the Logout hook
    g_greeted.erase(pc);
    g_greetDue.erase(pc);
}

static void DoGreetJoiners() {
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) return;
    long long now = (long long)std::time(nullptr);
    std::set<void*> present;
    for (TWeakObjectPtr<APlayerController> wpc : world->PlayerControllerListField()) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (!pc) continue;
        // A controller LINGERS in this list after its player disconnects - that's why pointer
        // identity alone still missed rejoins (and why ARK reusing the object hid it completely).
        // A live player has a NetConnection; a disconnected one does not.
        if (!pc->NetConnectionField()) continue;                 // gone -> falls out of `present`
        present.insert((void*)pc);
        if (g_greeted.count((void*)pc)) continue;
        if (!pc->GetPlayerCharacter()) continue;                 // mid-load: greet on a later tick
        // Having a character is NOT the same as being able to see chat: the client is still
        // finishing its load, and a message sent now is accepted server-side but never displayed
        // (the log said GREET while the player saw nothing). Wait a few seconds after the
        // character appears, then send.
        auto due = g_greetDue.find((void*)pc);
        if (due == g_greetDue.end()) { g_greetDue[(void*)pc] = now + GREET_DELAY_SEC; continue; }
        if (now < due->second) continue;                         // not yet - try again next tick
        g_greetDue.erase(due);
        g_greeted.insert((void*)pc);
        DoGreetPlayer(pc);
    }
    for (auto it = g_greeted.begin(); it != g_greeted.end(); )   // disconnected -> greet on rejoin
        if (present.count(*it)) ++it; else it = g_greeted.erase(it);
    for (auto it = g_greetDue.begin(); it != g_greetDue.end(); ) // left before we got to greet them
        if (present.count(it->first)) ++it; else it = g_greetDue.erase(it);
}
static void GreetJoiners() { __try { DoGreetJoiners(); } __except (EXCEPTION_EXECUTE_HANDLER) {} }

// "/confirm is pending" prompt. This CANNOT be delivered through msg_in.jsonl: on a server start
// the resumed AP session writes the fragment (and its prompt) before the first ApplyMessages tick,
// and that tick marks everything already in the file as seen ("don't replay old chat history") -
// so the prompt was silently swallowed every time. Drive it from STATE instead: if a spawn
// fragment exists and Game.ini does not already contain it, say so - and only once a player is
// actually online, otherwise the chat line goes nowhere and the one-shot is wasted.
static void DoShowSpawnPrompt() {
    static bool announced = false;
    if (announced || !g_ipc) return;
    fs::path frag;
    for (auto& route : MailboxRoutes()) {
        fs::path p = g_ipc->DirFor(route) / "game_ini_fragment.txt";
        std::error_code ec;
        if (fs::exists(p, ec)) { frag = p; break; }
    }
    if (frag.empty()) return;
    if (PatchGameIniFromFragment(frag, GameIniPath(), true) != 1) return;   // nothing pending
    if (!FirstReadyPlayer()) return;                        // nobody to read it yet - try next tick
    announced = true;
    DebugLog("PROMPT: randomized spawns pending -> told players to /confirm");
    ChatNotify(L"ArkAP: randomize_dino_spawns is pending - type /confirm to apply it to Game.ini "
               L"and restart the server (back in ~15s). One-time per seed.");
}
static void ShowSpawnPrompt() { __try { DoShowSpawnPrompt(); } __except (EXCEPTION_EXECUTE_HANDLER) {} }



// A state-file problem is invisible in game: taming silently re-locks and every note re-reports.
// Say it out loud, once, as soon as there is somebody to hear it.
static void ShowStateWarning() {
    static bool shown = false;
    if (shown || !g_state || g_state->LoadError().empty()) return;
    if (!FirstReadyPlayer()) return;                // wait for someone in-world, else it is lost
    shown = true;
    std::wstring m = L"ArkAP: " + ArkApi::Tools::Utf8Decode(g_state->LoadError());
    ChatNotify(m.c_str());
    if (g_state->ReadOnly())
        ChatNotify(L"ArkAP: progress tracking is PAUSED so the file on disk is not overwritten. "
                   L"Restore state.json.bak (next to ArkAP.dll), or delete state.json and run /aprecover.");
}

static void DoTick() {
    static int tn = 0; ++tn;
    if (g_pollFaulted) { g_pollFaulted = false; DebugLog("!! FAULT in PollIncoming"); }
    if (g_reassertFaulted) { g_reassertFaulted = false; DebugLog("!! FAULT in ReassertEngrams"); }
    if (g_tickFaulted) { g_tickFaulted = false; DebugLog("!! FAULT in tick (outer)"); }
    if (tn <= 5 || tn % 60 == 0)
        DebugLog("tick " + std::to_string(tn) + " ready=" + (ServerReady() ? "1" : "0"));
    if (!ServerReady()) return;
    if (!g_registry_built) BuildEngramRegistry();   // SEH-guarded; builds once when ready
    DoGrantStarter();                               // free starter engrams (once, when flag known)
    g_fxBudget = FX_PER_TICK;                        // refill the per-tick filler budget (#5 throttle)
    AutoRecoverLostState();                         // watermark says applied but nothing owned
    ProcessSeedReset();                             // must run BEFORE PollIncoming: a new seed's
                                                    // items would otherwise be dropped as "already
                                                    // received" from the previous seed
    PollIncoming();
    RetryPendingFx();                               // deliver filler effects deferred while no player
    ReassertEngrams();                              // re-apply received engrams (join-timing safe)
    ProcessPendingChecks();                         // handle network-thread-queued note/tame checks
    ApplyDeaths();                                  // DeathLink: kill our player on a remote death
    ApplyMessages();                                // show connector item-flow lines in-game
    ShowConnStatus();                               // embedded /connect connect/disconnect -> chat
    GreetJoiners();                                 // on JOIN: tell THAT player their AP state
    ShowSpawnPrompt();                              // "/confirm pending" (state-based, not msg_in)
    ShowStateWarning();                             // corrupt/recovered state file -> tell somebody
    // refresh runtime flags the connector(s) relay - cheap, idempotent. PER-ROUTE now (each slot's
    // own bundle_saddles / free_starter), so a mixed multiplayer lobby doesn't leak one slot's
    // setting onto everyone.
    try {
        static std::map<std::string, size_t> s_lastGroupsN;   // diagnostics: log on change only
        for (auto& route : MailboxRoutes()) {
            fs::path p = g_ipc->DirFor(route) / "flags.json";
            if (!fs::exists(p)) {
                if (s_lastGroupsN.find(route) == s_lastGroupsN.end()) {   // log the miss once
                    s_lastGroupsN[route] = 0;
                    DebugLog("FLAGS route=[" + route + "] NO flags.json at " + p.string());
                }
                continue;
            }
            nlohmann::json j;
            try { std::ifstream(p) >> j; }
            catch (const std::exception& e) { DebugLog("FLAGS route=[" + route + "] parse FAIL: " + e.what()); continue; }
            g_routeBundleSaddles[route] = j.value("bundle_saddles", false);
            g_routeFreeStarter[route]   = j.value("free_starter_engrams", false);
            std::set<std::string> mods;
            for (auto& m : j.value("mod_ids", nlohmann::json::array()))
                if (m.is_string()) mods.insert(m.get<std::string>());
            g_routeMods[route] = mods;
            // count-grouping: {rep id (string) -> [member ids]}. NOTE: iterate the real member
            // (j["item_groups"]), NOT j.value(...).items() - .items() on the value() TEMPORARY
            // references a destroyed object (its lifetime isn't extended by the range-for), which
            // silently yields ZERO entries even when the key is present (v106 diag: key=1 parsed=0).
            std::map<int, std::vector<int>> groups;
            if (j.contains("item_groups") && j["item_groups"].is_object()) {
                for (auto& [k, v] : j["item_groups"].items()) {
                    int rep = 0; try { rep = std::stoi(k); } catch (...) { continue; }
                    for (auto& mid : v) if (mid.is_number_integer()) groups[rep].push_back(mid.get<int>());
                }
            }
            g_routeItemGroups[route] = groups;
            std::map<int, int> redir;                          // {unpooled id -> pooled id} for /hint
            if (j.contains("hint_redirect") && j["hint_redirect"].is_object()) {
                for (auto& [k, v] : j["hint_redirect"].items()) {
                    int mem = 0; try { mem = std::stoi(k); } catch (...) { continue; }
                    if (v.is_number_integer()) redir[mem] = v.get<int>();
                }
            }
            g_routeHintRedirect[route] = redir;
            if (s_lastGroupsN[route] != groups.size() || tn <= 3 || tn % 60 == 0) {   // periodic + on change
                s_lastGroupsN[route] = groups.size();
                std::error_code ec; auto sz = fs::file_size(p, ec);
                DebugLog("FLAGS route=[" + route + "] exists size=" + std::to_string((unsigned long long)sz) +
                         " item_groups_key=" + (j.contains("item_groups") ? "1" : "0") +
                         " parsed=" + std::to_string(groups.size()) + " (from " + p.string() + ")");
            }
        }
    } catch (...) {}

    // RECONCILE count-groups every tick: if a route owns a representative but not one of its folded
    // members, grant the member now. Idempotent + route-agnostic (unions all routes' item_groups,
    // since the map is identical per seed). Catches the cases the inline ApplyItem expansion can miss:
    // a rep that arrived in the connect backlog BEFORE flags.json loaded, a plugin updated mid-game,
    // or flags delivered under a different route than the items applied on. Cheap (map lookups).
    try {
        std::map<int, std::vector<int>> allGroups;
        for (auto& [r, g] : g_routeItemGroups)
            for (auto& [rep, mem] : g) allGroups[rep] = mem;
        static size_t s_lastAll = SIZE_MAX;
        if (s_lastAll != allGroups.size()) {                  // diagnostics: union size on change
            s_lastAll = allGroups.size();
            DebugLog("RECONCILE allGroups=" + std::to_string(allGroups.size()) +
                     " routeMaps=" + std::to_string(g_routeItemGroups.size()));
        }
        if (!allGroups.empty()) {
            std::set<std::string> routes;
            for (auto& r : g_state->Players()) routes.insert(r);
            routes.insert("");                              // the shared/root route too
            for (const std::string& route : routes)
                for (auto& [rep, members] : allGroups) {
                    if (!g_state->HasItem(route, rep)) continue;
                    for (int member : members) {
                        if (g_state->HasItem(route, member)) continue;
                        g_state->AddItem(route, member);
                        auto eit = g_itemToEngram.find(member);
                        if (eit != g_itemToEngram.end())
                            for (UClass* c : eit->second) GrantEngramToPlayers(route, c);
                        DebugLog("GROUP reconcile member=" + std::to_string(member) +
                                 " via rep=" + std::to_string(rep) + (route.empty() ? "" : " [" + route + "]"));
                    }
                }
        }
    } catch (...) {}
}
static void Tick() {
    __try { DoTick(); }
    __except (EXCEPTION_EXECUTE_HANDLER) { g_tickFaulted = true; }   // no objects in __except
}

// A reset tool deleted state.json but left the mailboxes behind - clear the stale history.
//
// docs/STATE_PERSISTENCE.md rule 2: an ABSENT state.json is a deliberate fresh start, never
// corruption. Rule 8 requires a reset to remove every ipc\<CharacterName> folder too, and
// tools/reset_ark_test.bat does. Third-party resetters (the community launcher, hand-deletion)
// generally delete a flat file list and never descend into the per-player mailboxes - so the old
// seed's items_in.jsonl and its watermark survive.
//
// That is not harmless. PollMailbox's REOWN backfill re-owns every line the route does not
// currently own, which after a wipe is ALL of them, so the previous seed's entire item list is
// restored seconds after boot and the "reset" looks like it did nothing.
//
// Losing state.json accidentally is still fully covered: AUTORECOVER sees a watermark with nothing
// owned and asks Archipelago to re-send the whole list, which is the documented recovery path and
// does not depend on this history.
static void PurgeStaleMailboxes() {
    if (!g_state || !g_ipc || !g_state->StartedFresh()) return;
    static const char* kStale[] = {
        "items_in.jsonl", "applied_index.json", "checks_out.jsonl", "boss_out.jsonl",
        "death_out.jsonl", "death_in.jsonl", "msg_in.jsonl", "hint_out.jsonl",
        "hint_status.json", "remaining.json", "seed_reset.json", "conn_status.txt",
        "session.json", "flags.json", "game_ini_fragment.txt",
    };
    std::error_code ec;
    std::vector<fs::path> boxes{ g_ipc->Root() };
    for (auto& e : fs::directory_iterator(g_ipc->Root(), ec))
        if (e.is_directory()) boxes.push_back(e.path());
    int removed = 0;
    std::string where;
    for (auto& box : boxes) {
        int n = 0;
        for (const char* f : kStale) {
            std::error_code ec2;
            if (fs::remove(box / f, ec2)) { ++n; ++removed; }
        }
        if (n) where += (where.empty() ? "" : ", ") + box.filename().string() +
                        "(" + std::to_string(n) + ")";
    }
    // ROOT watermark lives beside the dll, not in ipc\ - see WatermarkPath().
    if (fs::remove(PluginDir() / "applied_index.json", ec)) ++removed;
    if (removed)
        DebugLog("FRESH START: state.json was absent, so " + std::to_string(removed) +
                 " leftover mailbox file(s) from the previous seed were cleared [" + where +
                 "] - without this the REOWN backfill would have restored the old item list");
}

// ----------------------------------------------------------------- lifecycle
static void Load() {
    fs::path base = PluginDir();
    // build marker - lets us confirm which dll is actually loaded
    try { std::ofstream(base / "ArkAP_loaded.txt") << ARKAP_BUILD << "\n"; } catch (...) {}
    bool embeddedAp = true;                              // /connect kill-switch (see below)
    if (fs::exists(base / "ArkAP.config.json")) {
        try { nlohmann::json j; std::ifstream(base / "ArkAP.config.json") >> j;
            if (j.value("mode", "ap") == "offline") g_mode = Mode::Offline;
            g_multiplayer = j.value("multiplayer", false);   // per-player slots (see docs)
            embeddedAp = j.value("embedded_ap", true);       // false = disable /connect entirely
            g_gameIniOverride = j.value("game_ini_path", "");// /confirm target (blank = auto-derive)
        } catch (...) {}
    }
    g_tables.Load(base / "engrams.json", base / "locations.json", base / "mods");
    g_state = std::make_unique<State>(base, g_mode);
    g_state->Load();
    if (!g_state->LoadError().empty())          // never let a state problem pass unnoticed again
        DebugLog("!! STATE " + g_state->LoadError());
    g_ipc = std::make_unique<Ipc>(base / "ipc");
    PurgeStaleMailboxes();      // reset tool wiped state.json but left the mailboxes? clear them
                                // BEFORE the first poll, or the backfill restores the old seed
    // embedded AP client (/connect). Sessions run on their own threads and only touch
    // files/network - never ArkApi - so starting them from Load is safe. Kill-switch:
    // "embedded_ap": false in ArkAP.config.json disables it entirely (auto-resume included) -
    // the escape hatch if a persisted connection ever crashes the server at boot.
    if (embeddedAp)
        g_apManager = std::make_unique<ArkAP::APManager>(
            base,
            [](const std::string& s) { DebugLog(s); },
            [](int id) {
                auto it = g_tables.item_name.find(id);
                return it == g_tables.item_name.end() ? std::string() : it->second;
            },
            [](const std::string& route) { return g_ipc->DirFor(route); });

    // free starter engrams: resolve engrams.json "starter_engrams" ap_names -> item ids.
    try {
        nlohmann::json ej; std::ifstream(base / "engrams.json") >> ej;
        std::unordered_map<std::string, int> nameToId;
        for (auto& [id, nm] : g_tables.item_name) nameToId[nm] = id;
        for (auto& n : ej.value("starter_engrams", nlohmann::json::array())) {
            auto it = nameToId.find(n.get<std::string>());
            if (it != nameToId.end()) g_starterItemIds.insert(it->second);
        }
    } catch (...) {}

    // taming registry: DinoNameTag -> AP item id, straight from dinos.json (no game data needed).
    try {
        if (fs::exists(base / "dinos.json")) {
            nlohmann::json dj; std::ifstream(base / "dinos.json") >> dj;
            for (auto& d : dj.value("dinos", nlohmann::json::array())) {
                try {
                    std::string tag = d.at("dino_tag").get<std::string>();
                    // untameable kill-only entries have no id/ap_name/tame_loc/saddle -> guard them.
                    if (d.contains("id") && d["id"].is_number()) {
                        int id = d["id"].get<int>();
                        g_tameTagToItem[tag] = id;                          // taming gate item
                        if (d.contains("ap_name"))
                            g_tables.item_name[id] = d["ap_name"].get<std::string>();  // grant announce
                        if (d.contains("saddle_class") && d["saddle_class"].is_string()) {
                            auto eit = g_tables.engram_class_to_item.find(d["saddle_class"].get<std::string>());
                            if (eit != g_tables.engram_class_to_item.end()) g_tameItemToSaddleItem[id] = eit->second;
                        }
                    }
                    if (d.contains("tame_loc") && d["tame_loc"].is_number())
                        g_tameTagToTameLoc[tag] = d["tame_loc"].get<int>();
                    if (d.contains("kill_loc") && d["kill_loc"].is_number())
                        g_killTagToLoc[tag] = d["kill_loc"].get<int>();
                } catch (...) {}
            }
        }
    } catch (...) {}

    // crate registries: class name -> gated access item (beacons/cave/deep-sea) or artifact loc check.
    try {
        if (fs::exists(base / "crates.json")) {
            nlohmann::json cj; std::ifstream(base / "crates.json") >> cj;
            for (auto& c : cj.value("crate_items", nlohmann::json::array())) {
                int id = c.at("id").get<int>();
                g_tables.item_name[id] = c.at("ap_name").get<std::string>();   // announce on grant
                for (auto& cls : c.at("classes")) {
                    std::string cn = cls.get<std::string>();
                    g_crateGateClassToItem[cn] = id;
                    // Only non-beacon crates may be suffix-matched; see CrateHasLevelToken.
                    if (!CrateHasLevelToken(cn))
                        g_crateGateNormToItem[NormalizeCrateClass(cn)] = id;
                }
            }
            // artifact_locations intentionally NOT loaded - artifacts are no longer checks.
        }
    } catch (...) {}

    // filler/trap items: phase-1 effect = spawn wild dinos near the player.
    try {
        if (fs::exists(base / "filler.json")) {
            nlohmann::json fj; std::ifstream(base / "filler.json") >> fj;
            for (auto& f : fj.value("filler", nlohmann::json::array())) {
                int id = f.at("id").get<int>();
                g_tables.item_name[id] = f.value("ap_name", "Filler");
                auto& eff = f["effect"];
                std::string kind = eff.value("kind", "");
                if (kind == "spawn")
                    g_fillerSpawn[id] = { eff.value("blueprint", ""), eff.value("count", 1),
                                          eff.value("level", 30), eff.value("distance", 2500) };
                else if (kind == "give") {
                    std::vector<FillerGive> gives;
                    if (eff.contains("gives")) for (auto& g : eff["gives"])
                        gives.push_back({ g.value("gfi", ""), g.value("qty", 1),
                                          g.value("quality", 0), g.value("gfi_code", "") });
                    else gives.push_back({ eff.value("gfi", ""), eff.value("qty", 1),
                                           eff.value("quality", 0), eff.value("gfi_code", "") });
                    g_fillerGive[id] = gives;
                }
                else if (kind == "buff") {
                    std::string c = eff.value("command", "");
                    if (!c.empty()) g_fillerBuff[id] = c;
                }
            }
        }
    } catch (...) {}

    // boss registry: per-boss CLASS-name fragment + Gamma/Beta/Alpha check locs (tags are
    // "SpiderBoss_Gamma" etc). Unmatched boss deaths log "BOSS-DEATH unmatched name=X".
    {
        static const std::unordered_map<std::string, std::string> kBossClassFrag = {
            {"SpiderBoss", "SpiderL"},                  // Broodmother
            {"GorillaBoss", "Gorilla"},                 // Megapithecus
            {"DragonBoss", "Dragon_Character_BP_Boss"}, // Dragon
            {"Overseer", "EndBoss"},                    // Overseer = EndBoss_Character_C (confirmed)
        };
        std::unordered_map<std::string, BossEntry> byBase;
        for (auto& [tag, loc] : g_tables.boss_tag_to_loc) {
            auto us = tag.rfind('_');
            std::string base = (us == std::string::npos) ? tag : tag.substr(0, us);
            std::string diff = (us == std::string::npos) ? "" : tag.substr(us + 1);
            auto fit = kBossClassFrag.find(base);
            std::string frag = (fit != kBossClassFrag.end()) ? fit->second : base;
            auto& be = byBase[base];
            be.frag = frag; be.baseTag = base;
            if (diff == "Beta")       be.locBeta = loc;
            else if (diff == "Alpha") be.locAlpha = loc;
            else                      be.locGamma = loc;      // Gamma or legacy untagged
        }
        for (auto& [base, be] : byBase) {
            if (!be.locBeta)  be.locBeta = be.locGamma;       // legacy single-loc data: all -> same
            if (!be.locAlpha) be.locAlpha = be.locGamma;
            g_bosses.push_back(be);
        }
    }

    // alpha-predator kill checks + inventory "hold N" checks (locations.json).
    try {
        nlohmann::json lj; std::ifstream(base / "locations.json") >> lj;
        auto& lc = lj["location_categories"];
        for (auto& a : lc.value("alpha_kills", nlohmann::json::object())
                         .value("entries", nlohmann::json::array()))
            g_alphaFragToLoc.emplace_back(a.at("class_frag").get<std::string>(), a.at("id").get<int>());
        for (auto& d : lc.value("deaths", nlohmann::json::object())
                         .value("entries", nlohmann::json::array()))
            g_deathKindToLoc[d.at("kind").get<std::string>()] = d.at("id").get<int>();
        for (auto& ic : lc.value("inventory_checks", nlohmann::json::object())
                          .value("entries", nlohmann::json::array()))
            g_invChecks.push_back({ ic.at("id").get<int>(), ic.at("item_class").get<std::string>(),
                                    ic.value("qty", 1), ic.value("name", std::string()) });
    } catch (...) {}

    // which map are we on, and which location ids belong to it? Must load BEFORE the exploration
    // areas so those can be filtered as they are read.
    try {
        g_mapKey = CurrentMapKey();
        if (fs::exists(base / "maps.json")) {
            nlohmann::json mj; std::ifstream(base / "maps.json") >> mj;
            if (mj.contains("content") && mj["content"].is_object()) {
                for (auto& [key, buckets] : mj["content"].items()) {
                    if (!buckets.is_object()) continue;
                    bool mine = (key == "any") || (!g_mapKey.empty() && key == g_mapKey);
                    for (const char* which : {"items", "locations"}) {
                        if (!buckets.contains(which) || !buckets[which].is_array()) continue;
                        for (auto& v : buckets[which]) {
                            if (!v.is_number_integer()) continue;
                            int id = v.get<int>();
                            // Only LOCATION ids gate reporting. Item ids share the file but live in
                            // their own numeric blocks, and filtering those here would refuse to
                            // grant a perfectly valid item.
                            if (std::string(which) != "locations") continue;
                            g_mapKnownIds.insert(id);
                            if (mine) g_mapAllowedIds.insert(id);
                        }
                    }
                }
            }
        }
        // An unrecognised map means we cannot tell what belongs here - carry on unfiltered rather
        // than silently refusing every check.
        if (g_mapKey.empty()) {
            g_mapKnownIds.clear();
            g_mapAllowedIds.clear();
            DebugLog("MAPFILTER: running map not identified - filtering DISABLED");
        } else {
            DebugLog("MAPFILTER: map=" + g_mapKey + " allowed=" + std::to_string(g_mapAllowedIds.size()) +
                     " known=" + std::to_string(g_mapKnownIds.size()));
        }
    } catch (...) { g_mapKnownIds.clear(); g_mapAllowedIds.clear(); }

    // exploration areas (optional file - absent just means no exploration checks)
    try {
        if (fs::exists(base / "explore_areas.json")) {
            nlohmann::json xj; std::ifstream(base / "explore_areas.json") >> xj;
            int skipped = 0;
            for (auto& [key, r] : xj["regions"].items()) {
                // Regions carry their own map tag. Skip other maps' here as well as in
                // ReportLocation: the polygons are raw world coordinates, so an Island region would
                // otherwise be TESTED against a Scorched player's position and match on geometry.
                std::string rmap = r.value("map", std::string());
                if (!rmap.empty() && !g_mapKey.empty() && rmap != g_mapKey) { ++skipped; continue; }
                ExploreArea ea;
                ea.loc = r.at("id").get<int>();
                ea.name = r.value("name", key);
                // "polygons" (a list of shapes) wins when present; "polygon" is the single-shape
                // form every hand-drawn region uses and is still written for those, so older
                // tooling that only knows the singular keeps reading these files.
                auto addPart = [&ea](const nlohmann::json& pts) {
                    std::vector<std::pair<double, double>> part;
                    for (auto& p : pts) part.emplace_back(p.at(0).get<double>(), p.at(1).get<double>());
                    if (part.size() >= 3) ea.parts.push_back(std::move(part));
                };
                if (r.contains("polygons")) for (auto& poly : r["polygons"]) addPart(poly);
                else if (r.contains("polygon"))                       addPart(r["polygon"]);
                if (r.contains("z_below")) {            // depth region - no polygon
                    g_depth.push_back({ea.loc, ea.name, r["z_below"].get<double>()});
                } else if (!ea.parts.empty()) {
                    g_explore.push_back(std::move(ea));
                }
            }
            if (skipped)
                DebugLog("MAPFILTER: skipped " + std::to_string(skipped) +
                         " exploration region(s) belonging to other maps");
        }
    } catch (...) {}

    // tek grants: boss baseTag -> engram item ids (tek_grants.json; names resolved via item table).
    try {
        if (fs::exists(base / "tek_grants.json")) {
            std::unordered_map<std::string, int> nameToId;
            for (auto& [id, nm] : g_tables.item_name) nameToId[nm] = id;
            nlohmann::json tj; std::ifstream(base / "tek_grants.json") >> tj;
            for (auto& [bossTag, names] : tj.value("grants", nlohmann::json::object()).items())
                for (auto& n : names) {
                    auto it = nameToId.find(n.get<std::string>());
                    if (it != nameToId.end()) g_tekGrants[bossTag].push_back(it->second);
                }
        }
    } catch (...) {}

    ArkApi::GetHooks().SetHook("AShooterPlayerState.ServerUnlockEngram",
        &Hook_AShooterPlayerState_ServerUnlockEngram, &AShooterPlayerState_ServerUnlockEngram_original);
    ArkApi::GetHooks().SetHook("AShooterPlayerController.ServerUnlockPerMapExplorerNote_Implementation",
        &Hook_AShooterPlayerController_ServerUnlockPerMapExplorerNote_Implementation,
        &AShooterPlayerController_ServerUnlockPerMapExplorerNote_Implementation_original);
    ArkApi::GetHooks().SetHook("APrimalDinoCharacter.TameDino",
        &Hook_APrimalDinoCharacter_TameDino, &APrimalDinoCharacter_TameDino_original);
    ArkApi::GetHooks().SetHook("APrimalStructureItemContainer_SupplyCrate.BeginPlay",
        &Hook_APrimalStructureItemContainer_SupplyCrate_BeginPlay,
        &APrimalStructureItemContainer_SupplyCrate_BeginPlay_original);
    ArkApi::GetHooks().SetHook("APrimalDinoCharacter.Die",
        &Hook_APrimalDinoCharacter_Die, &APrimalDinoCharacter_Die_original);
    ArkApi::GetHooks().SetHook("AShooterCharacter.Die",
        &Hook_AShooterCharacter_Die, &AShooterCharacter_Die_original);
    ArkApi::GetHooks().SetHook("APrimalDinoCharacter.DoMate",
        &Hook_APrimalDinoCharacter_DoMate, &APrimalDinoCharacter_DoMate_original);
    ArkApi::GetHooks().SetHook("AShooterGameMode.Logout",
        &Hook_AShooterGameMode_Logout, &AShooterGameMode_Logout_original);

    ArkApi::GetCommands().AddConsoleCommand("ArkAP.DumpEngrams", &DumpEngrams);
    ArkApi::GetCommands().AddConsoleCommand("ArkAP.DumpNotes", &DumpNotes);
    ArkApi::GetCommands().AddConsoleCommand("ArkAP.BuildRegistry", &BuildRegistryCmd);
    ArkApi::GetCommands().AddChatCommand("/dumpengrams", &DumpEngramsChat);
    ArkApi::GetCommands().AddChatCommand("/dumpnotes", &DumpNotesChat);
    ArkApi::GetCommands().AddChatCommand("/dumpdinos", &DumpDinosChat);
    ArkApi::GetCommands().AddChatCommand("/dumppos", &DumpPosChat);   // exploration mapping
    ArkApi::GetCommands().AddChatCommand("/whoami", &WhoAmIChat);
    ArkApi::GetCommands().AddChatCommand("/buildregistry", &BuildRegistryChat);
    ArkApi::GetCommands().AddChatCommand("/hint", &HintChat);
    ArkApi::GetCommands().AddChatCommand("/buyhint", &HintChat);   // legacy alias -> now the same free reveal
    ArkApi::GetCommands().AddChatCommand("/connect", &ApConnectChat);
    ArkApi::GetCommands().AddChatCommand("/disconnect", &ApDisconnectChat);
    ArkApi::GetCommands().AddChatCommand("/apstatus", &ApStatusChat);
    ArkApi::GetCommands().AddChatCommand("/apcheck", &ApCheckChat);
    ArkApi::GetCommands().AddChatCommand("/apdumpinv", &ApDumpInvChat);
    ArkApi::GetCommands().AddChatCommand("/apresync", &ApResyncChat);
    ArkApi::GetCommands().AddChatCommand("/aprecover", &ApRecoverChat);
    ArkApi::GetCommands().AddChatCommand("/confirm", &ApConfirmChat);

    // 1s game-thread tick (reliable ArkApi timer; API::Timer registered at DLL-load didn't fire).
    ArkApi::GetCommands().AddOnTimerCallback("ArkAP_tick", []() { Tick(); });

    DebugLog(std::string("LOAD ") + ARKAP_BUILD + " mode=" + (g_mode == Mode::Offline ? "offline" : "ap") +
             std::string(" multiplayer=") + (g_multiplayer ? "1" : "0") +
             " engram_classes=" + std::to_string(g_tables.engram_class_to_item.size()) +
             " items=" + std::to_string(g_tables.item_name.size()) +
             " note_locs=" + std::to_string(g_tables.note_index_to_loc.size()) +
             " tame_dinos=" + std::to_string(g_tameTagToItem.size()) +
             " tame_saddles=" + std::to_string(g_tameItemToSaddleItem.size()) +
             " crate_gates=" + std::to_string(g_crateGateClassToItem.size()) +
             " bosses=" + std::to_string(g_bosses.size()) +
             " alphas=" + std::to_string(g_alphaFragToLoc.size()) +
             " tek_bosses=" + std::to_string(g_tekGrants.size()) +
             " inv_checks=" + std::to_string(g_invChecks.size()) +
             // canary: a data file that predates the Seed_DefensePlant fix still says
             // "Seed_PlantSpeciesX" here, which is otherwise invisible until a check silently
             // fails to fire.
             " seedclass=" + [] {
                 for (auto& ic : g_invChecks) if (ic.loc == 8757313) return ic.cls;
                 return std::string("?");
             }() +
             " explore=" + std::to_string(g_explore.size()) +
             " depth=" + std::to_string(g_depth.size()) +
             " deaths=" + std::to_string(g_deathKindToLoc.size()) +
             " hooks+timer registered");

    // resume /connect sessions persisted in ap_connections.json (after everything above is
    // initialised - the sessions' threads read g_tables via the itemName callback).
    if (g_apManager) g_apManager->ResumePersisted(g_multiplayer);
}

static void Unload() {
    ArkApi::GetHooks().DisableHook("AShooterPlayerState.ServerUnlockEngram",
        &Hook_AShooterPlayerState_ServerUnlockEngram);
    ArkApi::GetHooks().DisableHook("AShooterPlayerController.ServerUnlockPerMapExplorerNote_Implementation",
        &Hook_AShooterPlayerController_ServerUnlockPerMapExplorerNote_Implementation);
    ArkApi::GetHooks().DisableHook("APrimalDinoCharacter.TameDino",
        &Hook_APrimalDinoCharacter_TameDino);
    ArkApi::GetHooks().DisableHook("APrimalStructureItemContainer_SupplyCrate.BeginPlay",
        &Hook_APrimalStructureItemContainer_SupplyCrate_BeginPlay);
    ArkApi::GetHooks().DisableHook("APrimalDinoCharacter.Die",
        &Hook_APrimalDinoCharacter_Die);
    ArkApi::GetHooks().DisableHook("AShooterCharacter.Die",
        &Hook_AShooterCharacter_Die);
    ArkApi::GetHooks().DisableHook("APrimalDinoCharacter.DoMate",
        &Hook_APrimalDinoCharacter_DoMate);
    ArkApi::GetHooks().DisableHook("AShooterGameMode.Logout", &Hook_AShooterGameMode_Logout);
    ArkApi::GetCommands().RemoveOnTimerCallback("ArkAP_tick");
    ArkApi::GetCommands().RemoveConsoleCommand("ArkAP.DumpEngrams");
    ArkApi::GetCommands().RemoveConsoleCommand("ArkAP.DumpNotes");
    ArkApi::GetCommands().RemoveConsoleCommand("ArkAP.BuildRegistry");
    ArkApi::GetCommands().RemoveChatCommand("/dumpengrams");
    ArkApi::GetCommands().RemoveChatCommand("/dumpnotes");
    ArkApi::GetCommands().RemoveChatCommand("/dumpdinos");
    ArkApi::GetCommands().RemoveChatCommand("/dumppos");
    ArkApi::GetCommands().RemoveChatCommand("/whoami");
    ArkApi::GetCommands().RemoveChatCommand("/buildregistry");
    ArkApi::GetCommands().RemoveChatCommand("/hint");
    ArkApi::GetCommands().RemoveChatCommand("/buyhint");
    ArkApi::GetCommands().RemoveChatCommand("/connect");
    ArkApi::GetCommands().RemoveChatCommand("/disconnect");
    ArkApi::GetCommands().RemoveChatCommand("/apstatus");
    ArkApi::GetCommands().RemoveChatCommand("/apcheck");
    ArkApi::GetCommands().RemoveChatCommand("/apdumpinv");
    ArkApi::GetCommands().RemoveChatCommand("/apresync");
    ArkApi::GetCommands().RemoveChatCommand("/aprecover");
    ArkApi::GetCommands().RemoveChatCommand("/confirm");
    if (g_state) g_state->Save();
}

// AseApi calls this exported symbol BEFORE FreeLibrary (outside the loader lock) - the only
// safe place to JOIN the embedded AP client's threads. Joining inside DllMain(PROCESS_DETACH)
// can deadlock on the loader lock during a hot plugin unload.
extern "C" __declspec(dllexport) void Plugin_Unload() {
    try { g_apManager.reset(); } catch (...) {}
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
    // Never let an exception escape DllMain (-> ERROR_DLL_INIT_FAILED / 1114).
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        try { Load(); } catch (const std::exception& e) { DebugLog(std::string("Load threw: ") + e.what()); }
        catch (...) { DebugLog("Load threw unknown exception"); }
        break;
    case DLL_PROCESS_DETACH:
        // If Plugin_Unload already ran, g_apManager is gone. Otherwise (process exit) the OS
        // has terminated the session threads - RELEASE the manager instead of destroying it,
        // because ~APSession would join() under the loader lock.
        g_apManager.release();
        try { Unload(); } catch (...) {}
        break;
    }
    return TRUE;
}
