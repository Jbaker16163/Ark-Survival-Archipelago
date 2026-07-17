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
static std::map<std::string, std::time_t> g_suppressDeathUntil;  // per-route anti-loop for DeathLink kills
// live COLLECTIVE counters per route (every tame/kill/breed, repeats included). Persisted in
// counters.json. Hooks append "<kind>\t<route>" lines to events_queue.jsonl on the net thread;
// the game tick drains new lines (persisted queue_pos) into these totals.
static std::map<std::string, int> g_totalTames, g_totalKills, g_totalBreeds;
static bool  g_countersLoaded = false;
static bool  g_registry_built = false;
static bool  g_tickFaulted = false;    // set by Tick's __except, logged next tick
static bool  g_pollFaulted = false;
static bool  g_reassertFaulted = false;

// engram registry, built once the server is ready
static std::unordered_map<UClass*, int> g_engramClassToItem;          // item blueprint class -> AP item id
static std::unordered_map<int, UClass*>  g_itemToEngram;              // AP item id -> engram item class (POD value)
static std::set<int> g_starterItemIds;  // free starter engram item ids (from engrams.json starter_engrams)
static bool g_freeStarter = false;      // grant the starter engrams free (from flags.json)

// taming registry: DinoNameTag -> AP item id (loaded straight from dinos.json, no game data)
static std::unordered_map<std::string, int> g_tameTagToItem;
static std::unordered_map<std::string, int> g_tameTagToTameLoc;   // DinoNameTag -> "Tamed: X" check loc
static std::unordered_map<std::string, int> g_killTagToLoc;       // DinoNameTag -> "Killed: X" check loc

// saddle bundling: tame item id -> its saddle ENGRAM item id; gated by g_bundleSaddles (from flags.json)
static std::unordered_map<int, int> g_tameItemToSaddleItem;
static bool g_bundleSaddles = false;

// trap filler: item id -> dino spawn spec (effect = spawn wild dinos at <distance> in front)
struct TrapSpawn { std::string blueprint; int count; int level; int distance; };
static std::unordered_map<int, TrapSpawn> g_fillerSpawn;
static std::set<APrimalDinoCharacter*> g_trapDinos;   // spawned trap dinos -> the tame gate refuses them

// good filler: item id -> one or more GFI give specs (effect = give item(s) to the player)
struct FillerGive { std::string gfi; int qty; int quality; };
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
struct InvCheck { int loc; std::string cls; int qty; };
static std::vector<InvCheck> g_invChecks;

namespace fs = std::filesystem;

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
}

// ----------------------------------------------------------------- hooks
DECLARE_HOOK(AShooterPlayerState_ServerUnlockEngram, void, AShooterPlayerState*, TSubclassOf<UPrimalItem>, bool, bool);
DECLARE_HOOK(AShooterPlayerController_ServerUnlockPerMapExplorerNote_Implementation, void, AShooterPlayerController*, int);
DECLARE_HOOK(APrimalDinoCharacter_TameDino, void, APrimalDinoCharacter*, AShooterPlayerController*, bool, int, bool, bool, bool);
DECLARE_HOOK(APrimalStructureItemContainer_SupplyCrate_BeginPlay, void, APrimalStructureItemContainer_SupplyCrate*);
DECLARE_HOOK(APrimalDinoCharacter_Die, bool, APrimalDinoCharacter*, float, FDamageEvent*, AController*, AActor*);
DECLARE_HOOK(AShooterCharacter_Die, bool, AShooterCharacter*, float, FDamageEvent*, AController*, AActor*);
DECLARE_HOOK(APrimalDinoCharacter_DoMate, void, APrimalDinoCharacter*, APrimalDinoCharacter*);

// dino name tag as std::string ("" on fault). Has FString objects -> kept out of any __try.
static std::string DinoTag(APrimalDinoCharacter* dino) {
    FString fs;
    dino->DinoNameTagField().ToString(&fs);
    return fs.ToString();
}

// gate worker (objects live here, not in the __try-bearing hook). Returns true if the tame must be BLOCKED.
static bool DoTameGate(APrimalDinoCharacter* dino, AShooterPlayerController* forPc) {
    std::string tag = DinoTag(dino);
    if (tag.empty()) return false;
    std::string route = RouteFor(forPc);
    DebugLog("TAME tag=" + tag + (route.empty() ? "" : " by=" + route));   // harvest the real DinoNameTags
    { std::ofstream f(PluginDir() / "dino_queue.jsonl", std::ios::app); if (f) f << tag << "\n"; }
    auto it = g_tameTagToItem.find(tag);
    return it != g_tameTagToItem.end() && !g_state->HasItem(route, it->second);   // tracked + not unlocked FOR THIS PLAYER
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
    std::string route = RouteFor(forPc);
    { std::ofstream f(PluginDir() / "tame_check_queue.jsonl", std::ios::app);
      if (f) f << tag << "\t" << route << "\n"; }
    QueueCountEvent("tame", route);                 // collective count (drained on the game tick)
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
    auto gate = g_crateGateClassToItem.find(name);             // beacon / cave / deep-sea -> GATE
    if (gate != g_crateGateClassToItem.end() && !g_state->HasItemAny(gate->second)) {
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
static void HarvestDinoClass(const std::string& name) {
    if (name.find("_Character_BP") == std::string::npos) return;   // only real dino BP classes
    if (!g_seenDinoClasses.insert(name).second) return;            // once each
    std::ofstream f(PluginDir() / "ArkAP_dino_classes.jsonl", std::ios::app);
    if (f) f << "{\"class\": \"" << name << "\"}\n";
}

static void DoBossDeath(APrimalDinoCharacter* dino, AActor* damageCauser) {
    FString fn; dino->GetFullName(&fn, nullptr);
    std::string full = fn.ToString();
    std::string name = full.substr(0, full.find(' '));          // class name prefix
    if (name.empty()) return;
    HarvestDinoClass(name);                                     // ground-truth spawn class names
    for (auto& b : g_bosses) {
        if (name.find(b.frag) != std::string::npos) {
            // difficulty from the class name: _Easy = Gamma, _Medium = Beta, else Alpha (incl _Hard).
            int loc = (name.find("_Easy") != std::string::npos) ? b.locGamma
                    : (name.find("_Medium") != std::string::npos) ? b.locBeta : b.locAlpha;
            DebugLog("BOSS-KILL name=" + name + " loc=" + std::to_string(loc));
            // boss fights are team efforts: credit EVERY known slot (solo: just "").
            for (auto& r : KnownRoutes()) ReportLocation(r, loc);
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
void Hook_APrimalDinoCharacter_DoMate(APrimalDinoCharacter* _this, APrimalDinoCharacter* WithMate) {
    APrimalDinoCharacter_DoMate_original(_this, WithMate);
    if (!_this) return;
    __try { DoBreedCount(_this); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

bool Hook_APrimalDinoCharacter_Die(APrimalDinoCharacter* _this, float KillingDamage,
        FDamageEvent* DamageEvent, AController* Killer, AActor* DamageCauser) {
    bool ret = APrimalDinoCharacter_Die_original(_this, KillingDamage, DamageEvent, Killer, DamageCauser);
    __try { DoBossDeath(_this, DamageCauser); } __except (EXCEPTION_EXECUTE_HANDLER) {}
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
    auto sit = g_suppressDeathUntil.find(route);
    if (sit != g_suppressDeathUntil.end() && std::time(nullptr) < sit->second) return;   // incoming-link kill -> don't echo
    DebugLog("PLAYER death -> death_out.jsonl" + (route.empty() ? std::string() : " route=" + route));
    std::ofstream f(g_ipc->DirFor(route) / "death_out.jsonl", std::ios::app);
    if (f) f << "{\"death\":1}\n";
}
bool Hook_AShooterCharacter_Die(AShooterCharacter* _this, float KillingDamage,
        FDamageEvent* DamageEvent, AController* Killer, AActor* DamageCauser) {
    __try { ResolveDyingRoute(_this); } __except (EXCEPTION_EXECUTE_HANDLER) {}   // BEFORE Die: controller still owns the character
    bool ret = AShooterCharacter_Die_original(_this, KillingDamage, DamageEvent, Killer, DamageCauser);
    __try { DoPlayerDeath(_this); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return ret;
}

// ----------------------------------------------------------------- reporting / applying
void ReportLocation(const std::string& route, int loc_id) {
    if (loc_id == 0) { DebugLog("REPORT skip: loc_id=0 (unmapped)"); return; }
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

// grant an engram: route "" = every connected player (solo/shared); otherwise only that player.
static void GrantEngramToPlayers(const std::string& route, UClass* engramClass) {
    if (!engramClass) return;
    TSubclassOf<UPrimalItem> engram; engram.uClass = engramClass;   // rebuild from the class
    UWorld* world = ArkApi::GetApiUtils().GetWorld();
    if (!world) { DebugLog("grant: no world"); return; }
    auto& world_players = world->PlayerControllerListField();
    int seen = 0, granted = 0, had = 0;
    for (TWeakObjectPtr<APlayerController> wpc : world_players) {
        auto* pc = static_cast<AShooterPlayerController*>(wpc.Get());
        if (!pc) continue;
        if (!route.empty() && RouteFor(pc) != route) continue;   // multiplayer: this slot's player only
        ++seen;
        auto* ps = pc->GetShooterPlayerState();
        if (!ps) continue;
        if (ps->HasEngram(engram)) { ++had; continue; }
        g_applying = true;
        ps->ServerUnlockEngram(engram, true, true);
        g_applying = false;
        ++granted;
    }
    if (granted > 0)   // quiet: only log when we actually unlocked something
        DebugLog("grant: controllers=" + std::to_string(seen) +
                 " already=" + std::to_string(had) + " granted=" + std::to_string(granted) +
                 (route.empty() ? "" : " [" + route + "]"));
}

// trap effect: SpawnDino "<blueprint>" <distance> <yOffset> 5000 <level> = a WILD dino <distance>
// units in front of the TARGET player, dropped from Z+5000. Pack members spread via yOffset.
// Returns false when the target player isn't in-world yet (caller queues a retry).
static bool DoSpawnTrap(const std::string& route, const TrapSpawn& t) {
    AShooterPlayerController* pc = PcForRoute(route);
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
static bool DoGiveFiller(const std::string& route, const std::vector<FillerGive>& gives) {
    AShooterPlayerController* pc = PcForRoute(route);
    // require a LIVE character: GiveItem to a dead/dying pawn lands in the corpse's inventory
    // (= lost unless looted). Deferring returns it to g_pendingFx until after respawn.
    if (!pc || !pc->GetPlayerCharacter() || pc->GetPlayerCharacter()->IsDead()) return false;
    for (auto& g : gives) {
        TArray<UPrimalItem*> out;
        FString bp(ArkApi::Tools::Utf8Decode(g.gfi).c_str());
        bool ok = pc->GiveItem(&out, &bp, g.qty, (float)g.quality, false, false, 0.f);
        DebugLog("GIVE ok=" + std::string(ok ? "1" : "0") + " qty=" + std::to_string(g.qty) + " bp=" + g.gfi);
    }
    return true;
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
    AShooterPlayerController* pc = PcForRoute(route);
    if (!pc || !pc->GetPlayerCharacter() || pc->GetPlayerCharacter()->IsDead()) return false;
    FString cmd(ArkApi::Tools::Utf8Decode(cmdStr).c_str()); FString res;
    pc->ConsoleCommand(&res, &cmd, true);
    DebugLog("BUFF applied cmd=" + cmdStr + (route.empty() ? "" : " [" + route + "]"));
    return true;
}
static bool BuffFiller(const std::string& route, int item_id) {
    auto it = g_fillerBuff.find(item_id);
    if (it == g_fillerBuff.end()) return true;         // not a buff item -> nothing to do
    bool ok = true;
    __try { ok = DoBuffFiller(route, it->second); } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return ok;
}

// filler effects that arrived while the target player wasn't in-world - retried each tick.
static std::vector<std::pair<std::string, int>> g_pendingFx;   // (route, item id)
static void RetryPendingFx() {
    if (g_pendingFx.empty()) return;
    std::vector<std::pair<std::string, int>> again;
    for (auto& [route, id] : g_pendingFx)
        if (!SpawnTrap(route, id) || !GiveFiller(route, id) || !BuffFiller(route, id))
            again.emplace_back(route, id);
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
static void ApplyStructureBundle(const std::string& route, int bundle_id,
                                 const std::string& material, const std::string& from) {
    int members = 0;
    for (auto& [item_id, cls] : g_tables.item_to_engram_class) {
        if (cls.find("PrimalItemStructure_") == std::string::npos) continue;
        auto nit = g_tables.item_name.find(item_id);
        if (nit == g_tables.item_name.end() || !NameHasWord(nit->second, material)) continue;
        g_state->AddItem(route, item_id);                // persists -> gate + reassert keep it
        auto eit = g_itemToEngram.find(item_id);
        if (eit != g_itemToEngram.end()) GrantEngramToPlayers(route, eit->second);
        ++members;
    }
    std::wstring msg = L"Unlocked ALL " + ArkApi::Tools::Utf8Decode(material) +
                       L" structures (" + std::to_wstring(members) + L" engrams)";
    if (!from.empty()) msg += L" - by " + ArkApi::Tools::Utf8Decode(from);
    ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), msg.c_str());
    DebugLog("BUNDLE structures material=" + material + " members=" + std::to_string(members));
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
    // the pool holds many COPIES of the same filler id; each copy (new index) re-fires its
    // effect. Everything else dedups by id.
    if (!is_new && !is_filler) return;             // already received (non-filler)

    // announce known items (skip unknown ids)
    auto nameIt = g_tables.item_name.find(item_id);
    if (nameIt != g_tables.item_name.end()) {
        std::wstring msg = L"Unlocked " + ArkApi::Tools::Utf8Decode(nameIt->second);
        if (!route.empty()) msg += L" for " + ArkApi::Tools::Utf8Decode(route);
        if (!from.empty()) msg += L" - by " + ArkApi::Tools::Utf8Decode(from);
        ArkApi::GetApiUtils().SendChatMessageToAll(FString(L"Archipelago"), msg.c_str());
    }

    auto it = g_itemToEngram.find(item_id);       // engram item -> push the unlock now
    if (it != g_itemToEngram.end()) GrantEngramToPlayers(route, it->second);
    // taming / supply / boss / map items: gating reads State on demand, nothing to push.

    // filler effects; if the target player isn't in-world yet, queue a retry.
    bool trapOk = SpawnTrap(route, item_id);      // trap filler -> spawn dinos near that player
    bool giveOk = GiveFiller(route, item_id);     // good filler -> give item(s) to that player
    bool buffOk = BuffFiller(route, item_id);     // buff/debuff filler -> ForceGiveBuff on that player
    if (!trapOk || !giveOk || !buffOk) {
        g_pendingFx.emplace_back(route, item_id);
        DebugLog("FX deferred (target player not in-world) id=" + std::to_string(item_id));
    }

    // bundle_saddles: a tame unlock also grants the dino's saddle engram.
    if (g_bundleSaddles) {
        auto sit = g_tameItemToSaddleItem.find(item_id);
        if (sit != g_tameItemToSaddleItem.end()) {
            g_state->AddItem(route, sit->second);     // record so the gate + reassert keep it
            auto eit = g_itemToEngram.find(sit->second);
            if (eit != g_itemToEngram.end()) GrantEngramToPlayers(route, eit->second);
            DebugLog("BUNDLE saddle item=" + std::to_string(sit->second) + " with tame=" + std::to_string(item_id));
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
        if (eit != g_itemToEngram.end()) GrantEngramToPlayers("", eit->second);
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
static void DoReassert() {
    for (auto& route : g_state->Players())
        for (auto& [item_id, cls] : g_itemToEngram)
            if (g_state->HasItem(route, item_id))
                GrantEngramToPlayers(route, cls);
}

// free starter engrams: mark the configured starter engrams as owned (reassert then grants them
// in-game). Multiplayer: every connected player gets them in their own bucket.
static std::set<std::string> g_starterGrantedRoutes;
static void DoGrantStarter() {
    if (!g_freeStarter || g_starterItemIds.empty()) return;
    for (auto& route : KnownRoutes()) {
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
static void PollMailbox(const std::string& route) {
    int watermark = LoadWatermark(route);
    static std::map<std::string, std::set<int>> processedIds;   // legacy (index-less) lines only
    fs::path path = g_ipc->DirFor(route) / "items_in.jsonl";

    std::string content;
    { std::ifstream f(path, std::ios::binary);
      if (f) { std::stringstream ss; ss << f.rdbuf(); content = ss.str(); } }
    if (content.empty()) return;

    // multiplayer misconfig tripwire: items in the ROOT mailbox are SHARED (unlock for every
    // player). In multiplayer each slot's connector must point at ipc\<CharacterName> instead.
    if (g_multiplayer && route.empty()) {
        static bool warned = false;
        if (!warned) {
            warned = true;
            DebugLog("!! MULTIPLAYER WARNING: items arriving in the ROOT ipc mailbox are shared "
                     "with ALL players. Point each slot's connector at ipc\\<CharacterName>.");
            ChatNotify(L"ArkAP: items arrived in the SHARED root mailbox - in multiplayer, each "
                       L"connector must use ipc\\<CharacterName>. Check connector.ini ipc_dir.");
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
            if (idx <= watermark) continue;       // this copy already applied (persisted)
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
}

// every mailbox: the root (route "") + one subfolder per multiplayer slot.
static std::vector<std::string> MailboxRoutes() {
    std::vector<std::string> routes = { "" };
    if (!g_multiplayer) return routes;
    std::error_code ec;
    for (auto& e : fs::directory_iterator(g_ipc->Root(), ec))
        if (e.is_directory()) routes.push_back(e.path().filename().string());
    return routes;
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

static void DoBuildRegistry() {
    UPrimalGameData* gd = GameData();
    if (!gd) return;
    g_engramClassToItem.clear();
    g_itemToEngram.clear();
    for (UPrimalEngramEntry* e : gd->EngramBlueprintEntriesField()) {
        if (!e) continue;
        TSubclassOf<UPrimalItem> sub = e->BluePrintEntryField();
        UClass* cls = sub.uClass;
        if (!cls) continue;
        std::string name = ClassShortName(cls);   // item blueprint class path
        auto it = g_tables.engram_class_to_item.find(name);
        if (it != g_tables.engram_class_to_item.end()) {
            g_engramClassToItem[cls] = it->second;
            g_itemToEngram[it->second] = cls;
        }
    }
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

static void DoDumpEngrams() {
    UPrimalGameData* gd = GameData();
    if (!gd) return;
    nlohmann::json out = nlohmann::json::array();
    for (UPrimalEngramEntry* e : gd->EngramBlueprintEntriesField()) {
        if (!e) continue;
        FString ename; e->NameField().ToString(&ename);
        out.push_back({ {"entry_name", ename.ToString()},
                        {"item_class", ClassShortName(e->BluePrintEntryField().uClass)},
                        {"level", e->GetRequiredLevel()} });
    }
    std::ofstream(PluginDir() / "ArkAP_engrams_dump.json") << out.dump(2);
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
        HarvestDinoClass(full.substr(0, full.find(' ')));
    }
    int total = (int)g_seenDinoClasses.size();
    std::wstring m = L"Harvested " + std::to_wstring(total - before) + L" new dino classes (" +
                     std::to_wstring(total) + L" total) -> ArkAP_dino_classes.jsonl";
    ChatNotify(m.c_str());
    DebugLog("DUMPDINOS new=" + std::to_string(total - before) + " total=" + std::to_string(total));
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

// --- embedded AP client: /connect <slot> <host:port> [password] / /disconnect / /apstatus ---
// The session runs on its own threads inside the plugin and drives the SAME mailbox files the
// external connector uses - so /connect and the external connector are interchangeable per slot
// (don't run both for the same player at once: they'd double-send).
static void DoApConnect(AShooterPlayerController* pc, FString* message) {
    if (!g_apManager) { ChatNotify(L"ArkAP: embedded AP client not initialised."); return; }
    std::vector<std::string> tok;
    { std::istringstream ss(message ? message->ToString() : std::string());
      std::string t; while (ss >> t) tok.push_back(t); }          // tok[0] = "/connect"
    if (tok.size() < 3) {
        ChatNotify(L"Usage: /connect <slot> <host:port> [password]  "
                   L"e.g. /connect Alice archipelago.gg:38281");
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
    std::string reply = g_apManager->Connect(route, tok[1], tok[2], password);
    ChatNotify(ArkApi::Tools::Utf8Decode(reply).c_str());
    DebugLog("APCONNECT slot=" + tok[1] + " server=" + tok[2] +
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
    if (!g_apManager) return;
    ChatNotify(ArkApi::Tools::Utf8Decode(g_apManager->StatusAll()).c_str());
}
static void ApStatusChat(AShooterPlayerController*, FString*, EChatSendMode::Type) {
    __try { DoApStatus(); } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// --- /hint : quote the resource cost ; /buyhint : pay resources + reveal (connector runs AP !hint) ---
struct ResCost { std::string cls; std::string label; int qty; };
static std::vector<ResCost> HintCost(int id) {
    const ResCost pearl{ "PrimalItemResource_BlackPearl", "Black Pearl", 1 };
    if (id >= 8730000 && id < 8731000) return { {"PrimalItemResource_Wood","Wood",375},{"PrimalItemResource_Stone","Stone",188},{"PrimalItemResource_Thatch","Thatch",188}, pearl };       // engram
    if (id >= 8732000 && id < 8733000) return { {"PrimalItemResource_Wood","Wood",750},{"PrimalItemResource_Stone","Stone",375},{"PrimalItemResource_Fibers","Fiber",750},{"PrimalItemResource_Thatch","Thatch",375}, pearl }; // tame
    if (id >= 8733000 && id < 8734000) return { {"PrimalItemResource_Wood","Wood",1500},{"PrimalItemResource_Stone","Stone",750},{"PrimalItemResource_Oil_Base","Oil",375}, pearl };       // crate access
    if (id >= 8739000) return { {"PrimalItemResource_Wood","Wood",188},{"PrimalItemResource_Thatch","Thatch",188}, pearl };                                                                // filler
    return { {"PrimalItemResource_Wood","Wood",375},{"PrimalItemResource_Stone","Stone",188}, pearl };
}
static std::string CostLabel(const std::vector<ResCost>& c) {
    std::string s; for (size_t i = 0; i < c.size(); ++i) { if (i) s += ", "; s += std::to_string(c[i].qty) + " " + c[i].label; }
    return s;
}
// count a resource the player holds, by class-name substring (UNVERIFIED inventory API).
static int CountResource(UPrimalInventoryComponent* inv, const std::string& cls) {
    int total = 0;
    for (UPrimalItem* it : inv->InventoryItemsField()) {
        if (!it) continue;
        FString fn; it->GetFullName(&fn, nullptr);
        if (fn.ToString().find(cls) != std::string::npos) total += it->ItemQuantityField();
    }
    return total;
}
static void RemoveResource(UPrimalInventoryComponent* inv, const std::string& cls, int amount) {
    // collect matches first (RemoveItemFromInventory mutates the array -> don't iterate it live)
    std::vector<UPrimalItem*> matches;
    for (UPrimalItem* it : inv->InventoryItemsField()) {
        if (!it) continue;
        FString fn; it->GetFullName(&fn, nullptr);
        if (fn.ToString().find(cls) != std::string::npos) matches.push_back(it);
    }
    for (UPrimalItem* it : matches) {
        if (amount <= 0) break;
        int q = it->ItemQuantityField();
        int take = q < amount ? q : amount;
        if (take >= q) it->RemoveItemFromInventory(true, false);        // whole stack gone (no lingering 0-qty)
        else           it->SetQuantity(q - take, false);                // partial: set remaining
        amount -= take;
    }
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
static void DoHintQuote(FString* message) {
    std::string q = HintQuery(message);
    if (q.empty()) { ChatNotify(L"Usage: /hint <item name>"); return; }
    std::string name; int id = MatchItem(q, name);
    if (!id) { ChatNotify((L"No item matches '" + ArkApi::Tools::Utf8Decode(q) + L"'").c_str()); return; }
    std::wstring m = L"Hint for " + ArkApi::Tools::Utf8Decode(name) + L" costs " +
                     ArkApi::Tools::Utf8Decode(CostLabel(HintCost(id))) + L". Type /buyhint " +
                     ArkApi::Tools::Utf8Decode(name) + L" to pay + reveal.";
    ChatNotify(m.c_str());
}
static void DoHintBuy(AShooterPlayerController* pc, FString* message) {
    std::string q = HintQuery(message);
    if (q.empty()) { ChatNotify(L"Usage: /buyhint <item name>"); return; }
    std::string name; int id = MatchItem(q, name);
    if (!id) { ChatNotify((L"No item matches '" + ArkApi::Tools::Utf8Decode(q) + L"'").c_str()); return; }
    std::string route = RouteFor(pc);              // the buyer's own slot pays + receives the hint
    // don't spend resources if AP would reject the hint for lack of hint points.
    try { fs::path hs = g_ipc->DirFor(route) / "hint_status.json";
        if (fs::exists(hs)) { nlohmann::json j; std::ifstream(hs) >> j;
            int pts = j.value("hint_points", 0), cost = j.value("hint_cost", 0);
            if (pts < cost) {
                std::wstring m = L"Not enough AP hint points (have " + std::to_wstring(pts) +
                                 L", need " + std::to_wstring(cost) + L"). No resources taken.";
                ChatNotify(m.c_str()); return;
            }
        }
    } catch (...) {}
    UPrimalInventoryComponent* inv = pc ? (pc->GetPlayerCharacter() ? pc->GetPlayerCharacter()->MyInventoryComponentField() : nullptr) : nullptr;
    if (!inv) { ChatNotify(L"Can't read your inventory."); return; }
    auto cost = HintCost(id);
    for (auto& c : cost) {                          // verify the player has everything first
        int have = CountResource(inv, c.cls);
        if (have < c.qty) {
            std::wstring m = L"Need " + std::to_wstring(c.qty) + L" " + ArkApi::Tools::Utf8Decode(c.label) +
                             L" (you have " + std::to_wstring(have) + L"). Cost: " + ArkApi::Tools::Utf8Decode(CostLabel(cost));
            ChatNotify(m.c_str());
            return;
        }
    }
    for (auto& c : cost) RemoveResource(inv, c.cls, c.qty);   // charge
    { std::ofstream f(g_ipc->DirFor(route) / "hint_out.jsonl", std::ios::app); if (f) f << name << "\n"; }
    ChatNotify((L"Paid " + ArkApi::Tools::Utf8Decode(CostLabel(cost)) + L". Revealing hint for " +
                ArkApi::Tools::Utf8Decode(name) + L"...").c_str());
}
static void HintQuoteChat(AShooterPlayerController*, FString* m, EChatSendMode::Type) { __try { DoHintQuote(m); } __except (EXCEPTION_EXECUTE_HANDLER) {} }
static void HintBuyChat(AShooterPlayerController* pc, FString* m, EChatSendMode::Type) { __try { DoHintBuy(pc, m); } __except (EXCEPTION_EXECUTE_HANDLER) {} }

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

    // notes auto-granted on (re)spawn, not real collectibles -> never a check.
    static const std::set<int> kSkipNotes = { 1214 };

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
                  for (auto& [name, pl] : j.value("players", nlohmann::json::object()).items()) {
                      g_totalTames[name] = pl.value("tames", 0);
                      g_totalKills[name] = pl.value("kills", 0);
                      g_totalBreeds[name] = pl.value("breeds", 0);
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
            }
            queuePos = (long long)lines.size();
            try {
                nlohmann::json players = nlohmann::json::object();
                std::set<std::string> names;
                for (auto& [n, _] : g_totalTames)  names.insert(n);
                for (auto& [n, _] : g_totalKills)  names.insert(n);
                for (auto& [n, _] : g_totalBreeds) names.insert(n);
                for (auto& n : names)
                    players[n] = { {"tames", g_totalTames[n]}, {"kills", g_totalKills[n]},
                                   {"breeds", g_totalBreeds[n]} };
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
    PollIncoming();
    RetryPendingFx();                               // deliver filler effects deferred while no player
    ReassertEngrams();                              // re-apply received engrams (join-timing safe)
    ProcessPendingChecks();                         // handle network-thread-queued note/tame checks
    ApplyDeaths();                                  // DeathLink: kill our player on a remote death
    ApplyMessages();                                // show connector item-flow lines in-game
    // refresh runtime flags the connector(s) relay (bundle_saddles) - cheap, idempotent.
    // Multiplayer: any slot's flags.json turns a feature on (v1 simplification - per-slot
    // flag splits are rare; revisit if a mixed lobby needs per-player bundle_saddles).
    try {
        bool bundle = false, starter = false;
        for (auto& route : MailboxRoutes()) {
            fs::path p = g_ipc->DirFor(route) / "flags.json";
            if (!fs::exists(p)) continue;
            nlohmann::json j; std::ifstream(p) >> j;
            bundle  |= j.value("bundle_saddles", false);
            starter |= j.value("free_starter_engrams", false);
        }
        g_bundleSaddles = bundle;
        g_freeStarter = starter;
    } catch (...) {}
}
static void Tick() {
    __try { DoTick(); }
    __except (EXCEPTION_EXECUTE_HANDLER) { g_tickFaulted = true; }   // no objects in __except
}

// ----------------------------------------------------------------- lifecycle
static const char* ARKAP_BUILD = "v81-route-guard";

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
        } catch (...) {}
    }
    g_tables.Load(base / "engrams.json", base / "locations.json");
    g_state = std::make_unique<State>(base, g_mode);
    g_state->Load();
    g_ipc = std::make_unique<Ipc>(base / "ipc");
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
                for (auto& cls : c.at("classes")) g_crateGateClassToItem[cls.get<std::string>()] = id;
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
                        gives.push_back({ g.value("gfi", ""), g.value("qty", 1), g.value("quality", 0) });
                    else gives.push_back({ eff.value("gfi", ""), eff.value("qty", 1), eff.value("quality", 0) });
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
        for (auto& ic : lc.value("inventory_checks", nlohmann::json::object())
                          .value("entries", nlohmann::json::array()))
            g_invChecks.push_back({ ic.at("id").get<int>(), ic.at("item_class").get<std::string>(),
                                    ic.value("qty", 1) });
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

    ArkApi::GetCommands().AddConsoleCommand("ArkAP.DumpEngrams", &DumpEngrams);
    ArkApi::GetCommands().AddConsoleCommand("ArkAP.DumpNotes", &DumpNotes);
    ArkApi::GetCommands().AddConsoleCommand("ArkAP.BuildRegistry", &BuildRegistryCmd);
    ArkApi::GetCommands().AddChatCommand("/dumpengrams", &DumpEngramsChat);
    ArkApi::GetCommands().AddChatCommand("/dumpnotes", &DumpNotesChat);
    ArkApi::GetCommands().AddChatCommand("/dumpdinos", &DumpDinosChat);
    ArkApi::GetCommands().AddChatCommand("/whoami", &WhoAmIChat);
    ArkApi::GetCommands().AddChatCommand("/buildregistry", &BuildRegistryChat);
    ArkApi::GetCommands().AddChatCommand("/hint", &HintQuoteChat);
    ArkApi::GetCommands().AddChatCommand("/buyhint", &HintBuyChat);
    ArkApi::GetCommands().AddChatCommand("/connect", &ApConnectChat);
    ArkApi::GetCommands().AddChatCommand("/disconnect", &ApDisconnectChat);
    ArkApi::GetCommands().AddChatCommand("/apstatus", &ApStatusChat);

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
             " hooks+timer registered");

    // resume /connect sessions persisted in ap_connections.json (after everything above is
    // initialised - the sessions' threads read g_tables via the itemName callback).
    if (g_apManager) g_apManager->ResumePersisted();
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
    ArkApi::GetCommands().RemoveOnTimerCallback("ArkAP_tick");
    ArkApi::GetCommands().RemoveConsoleCommand("ArkAP.DumpEngrams");
    ArkApi::GetCommands().RemoveConsoleCommand("ArkAP.DumpNotes");
    ArkApi::GetCommands().RemoveConsoleCommand("ArkAP.BuildRegistry");
    ArkApi::GetCommands().RemoveChatCommand("/dumpengrams");
    ArkApi::GetCommands().RemoveChatCommand("/dumpnotes");
    ArkApi::GetCommands().RemoveChatCommand("/dumpdinos");
    ArkApi::GetCommands().RemoveChatCommand("/whoami");
    ArkApi::GetCommands().RemoveChatCommand("/buildregistry");
    ArkApi::GetCommands().RemoveChatCommand("/hint");
    ArkApi::GetCommands().RemoveChatCommand("/buyhint");
    ArkApi::GetCommands().RemoveChatCommand("/connect");
    ArkApi::GetCommands().RemoveChatCommand("/disconnect");
    ArkApi::GetCommands().RemoveChatCommand("/apstatus");
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
