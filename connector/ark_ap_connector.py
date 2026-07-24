#!/usr/bin/env python3
"""ARK <-> Archipelago bridge.

Talks the AP network protocol over websocket on one side, and file-based IPC to
the ArkServerApi plugin (plugin/ArkAP) on the other.

File IPC (in --ipc-dir, shared with the plugin):
  checks_out.jsonl  the PLUGIN appends one JSON object per line when a location is checked:
                        {"loc_id": 8740000}
  items_in.jsonl    THIS appends one line per received item; the plugin tails + applies:
                        {"item_id": 8730001, "from": "PlayerName"}

The connector keeps no persisted state: on (re)connect AP resends everything, and the
plugin dedups via its own state.json, so a restart safely re-delivers.

Run on the Server PC while you play:
  python ark_ap_connector.py --server archipelago.gg:38281 --slot YourSlot \
      --ipc-dir "E:/ARK/Server/ShooterGame/Binaries/Win64/ArkApi/Plugins/ArkAP/ipc"

Deps: pip install -r requirements.txt   (websockets)
"""
import argparse
import asyncio
import json
import os
import time
import traceback
from typing import Set

import websockets  # type: ignore

GAME = "ARK Survival Evolved"

# Goal = defeat the required bosses. The plugin signals each defeat by base-tag (e.g. "SpiderBoss")
# to boss_out.jsonl; the apworld sends the required tag set as slot_data.goal_boss_tags, and we
# send the AP goal (StatusUpdate CLIENT_GOAL) once all required tags have appeared. Boss kills are
# NOT AP check locations - so nothing can get stranded behind a hard/near-impossible boss kill.
CLIENT_GOAL = 30   # AP ClientStatus.CLIENT_GOAL


def _sanitize_route(s: str) -> str:
    """Mirror the plugin's SanitizeRoute (PluginMain.cpp) so the per-player mailbox
    folder name the connector creates matches the route the plugin writes to:
    keep ASCII alphanumerics + space/dash/underscore, trim edge spaces, cap at 40."""
    out = "".join(c for c in s if (c.isascii() and c.isalnum()) or c in " -_")
    out = out.strip(" ")
    if len(out) > 40:
        out = out[:40]
    return out or "_unnamed"


class Bridge:
    def __init__(self, ipc_dir: str, slot: str, password: str | None,
                 boss_goal_count: int = 4, data_dir: str | None = None,
                 death_link: bool = True, game_ini: str | None = None,
                 multiplayer: bool = False):
        # ipc_root = the plugin's shared ipc folder (holds the json data files + applied_index.json).
        # In multiplayer, each connector gets its own ipc_root/<slot> mailbox, created here at
        # startup - so everyone points ipc_dir at the same root and only `slot` differs. The plugin
        # routes by sanitized survivor name, so your ARK survivor name must equal your slot (the
        # folder name must match what /whoami shows).
        self.ipc_root = ipc_dir
        self.multiplayer = multiplayer
        if multiplayer:
            ipc_dir = os.path.join(ipc_dir, _sanitize_route(slot))
        self.ipc_dir = ipc_dir
        self.slot = slot
        self.password = password
        self.game_ini = game_ini or ""      # optional Game.ini path for the spawn randomizer
        # required bosses to win: the base-tags the plugin signals on defeat (boss_out.jsonl).
        # Boss kills are the goal, NOT AP check locations. slot_data sets the real set on connect;
        # empty until then (goal never fires before the yaml's goal is known).
        self.goal_boss_tags: set = set()
        self.goaled = False
        self.death_link = death_link             # slot_data overrides on connect
        self._death_link_cli_off = not death_link  # --no-death-link forces off regardless of yaml
        self.checks_out = os.path.join(ipc_dir, "checks_out.jsonl")
        self.items_in = os.path.join(ipc_dir, "items_in.jsonl")
        self.death_out = os.path.join(ipc_dir, "death_out.jsonl")   # plugin -> here: our player died
        self.death_in = os.path.join(ipc_dir, "death_in.jsonl")     # here -> plugin: a remote death, kill us
        self.msg_in = os.path.join(ipc_dir, "msg_in.jsonl")         # here -> plugin: text to show in-game
        self.hint_out = os.path.join(ipc_dir, "hint_out.jsonl")     # plugin -> here: /buyhint item names
        self.hint_status = os.path.join(ipc_dir, "hint_status.json")  # here -> plugin: AP hint points + cost
        self.boss_out = os.path.join(ipc_dir, "boss_out.jsonl")     # plugin -> here: a boss base-tag per defeat
        self._seed = ""
        self._hint_cost_pct = 10
        self._total_locs = 0
        self._hint_points = 0
        # death_out/hint_out persist across connector restarts, but our line counters don't -
        # start them at the CURRENT end of each file so the backlog is never replayed. (A zero
        # start re-broadcast every historical death as a fresh DeathLink on reconnect = killed
        # the whole room, and re-sent every old !hint.)
        self.deaths_sent = self._line_count(self.death_out)   # lines of death_out already broadcast
        self.hints_sent = self._line_count(self.hint_out)
        if self.deaths_sent:
            print(f"[connector] skipping DeathLink backlog: {self.deaths_sent} old death(s)")
        self.my_slot: int | None = None
        self.slot_game: dict[int, str] = {}     # slot -> game name
        self.slot_pname: dict[int, str] = {}    # slot -> display name
        self.game_items: dict[str, dict[int, str]] = {}   # game -> {item id -> name}
        self.game_locs: dict[str, dict[int, str]] = {}    # game -> {location id -> name}
        self._room_games: list = []
        os.makedirs(self.ipc_dir, exist_ok=True)
        self.sent_checks: Set[int] = set()
        # In-memory only (resets each run) so a connector restart always re-delivers
        # everything AP resends; the PLUGIN dedups via its own state. No persistence.
        self.received: Set[int] = set()
        self.players: dict[int, str] = {}   # slot id -> display name
        # id -> readable name, read from the plugin's data files (default: next to the ipc dir).
        self.item_names, self.loc_names = self._load_names(data_dir or os.path.dirname(self.ipc_root))

    @staticmethod
    def _line_count(path: str) -> int:
        """Non-empty lines currently in a jsonl file (0 if absent) - matches how the
        _maybe_* consumers count, so 'skip the existing backlog' lines up exactly."""
        try:
            with open(path, encoding="utf-8") as fh:
                return sum(1 for ln in fh if ln.strip())
        except OSError:
            return 0

    @staticmethod
    def _load_names(data_dir: str) -> tuple[dict[int, str], dict[int, str]]:
        items: dict[int, str] = {}
        locs: dict[int, str] = {}

        def read(name: str):
            for d in (data_dir, os.path.join(data_dir, "data")):
                p = os.path.join(d, name)
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as fh:
                        return json.load(fh)
            return None

        e = read("engrams.json")
        if e:
            for g in e.get("engrams", []):
                items[g["id"]] = g["ap_name"]
            for s in e.get("special_items", []):
                items[s["id"]] = s["ap_name"]
        for fn, key in (("dinos.json", "dinos"), ("crates.json", "crate_items")):
            j = read(fn)
            if j:
                for x in j.get(key, []):
                    if x.get("id") is not None and x.get("ap_name"):
                        items[x["id"]] = x["ap_name"]   # untameable kill-only dinos have neither
        items.setdefault(8739500, "Bonus Resources")   # filler

        loc = read("locations.json")
        if loc:
            cats = loc.get("location_categories", {})
            for k in ("dossiers", "bosses", "milestones"):
                for x in cats.get(k, {}).get("entries", []):
                    locs[x["id"]] = x["name"]
        cr = read("crates.json")
        if cr:
            for a in cr.get("artifact_locations", []):
                locs[a["id"]] = a["name"]
        print(f"[connector] loaded {len(items)} item names + {len(locs)} location names from {data_dir}")
        return items, locs

    def _queue_msg(self, text: str) -> None:
        print(f"[connector] {text}")
        with open(self.msg_in, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    # tell the plugin the current AP hint points + per-hint cost (so /buyhint won't charge
    # resources unless the AP hint can actually be afforded).
    def _write_hint_status(self) -> None:
        import math
        cost = math.ceil(self._hint_cost_pct / 100 * self._total_locs)
        with open(self.hint_status, "w", encoding="utf-8") as fh:
            json.dump({"hint_points": self._hint_points, "hint_cost": cost}, fh)

    def item_name(self, item_id: int) -> str:
        return self.item_names.get(item_id, f"item {item_id}")

    def loc_name(self, loc_id: int) -> str:
        return self.loc_names.get(loc_id, f"location {loc_id}")

    # ---- Lua -> AP : read new location checks appended by the mod ----
    def poll_new_checks(self) -> list[int]:
        # Whole-file read each poll; dedup via sent_checks (robust vs offset bugs).
        if not os.path.exists(self.checks_out):
            return []
        out: list[int] = []
        with open(self.checks_out, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    loc = int(json.loads(line)["loc_id"])
                except (ValueError, KeyError):
                    continue
                if loc not in self.sent_checks:
                    self.sent_checks.add(loc)
                    out.append(loc)
        return out

    # On a NEW seed (a regen), the AP item indices restart at 0, so the plugin's persisted index
    # watermark + the old items_in.jsonl would make it skip everything. Clear both once per seed.
    def _reset_on_new_seed(self) -> None:
        sess = os.path.join(self.ipc_dir, "session.json")
        last = None
        try:
            if os.path.exists(sess):
                last = json.load(open(sess, encoding="utf-8")).get("seed")
        except Exception:
            pass
        if not self._seed or self._seed == last:
            return
        for f in (self.items_in, self.boss_out,
                  os.path.join(os.path.dirname(self.ipc_root), "applied_index.json")):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        self.received.clear()
        try:
            json.dump({"seed": self._seed}, open(sess, "w", encoding="utf-8"))
        except Exception:
            pass
        print(f"[connector] new seed '{self._seed}' - cleared items_in.jsonl + applied_index.json")

    # ---- AP -> Lua : append received items for the mod to apply ----
    # index = the item's absolute position in the AP received-items list. It uniquely identifies
    # each COPY (the pool holds many copies of e.g. "Bonus Resources" with the same item_id), so
    # dedup happens on index - the plugin then re-applies filler effects per copy.
    def push_item(self, item_id: int, source: str = "", location_id: int | None = None,
                  index: int | None = None) -> None:
        key = index if index is not None else item_id
        if key in self.received:
            return
        self.received.add(key)
        where = f" from {self.loc_name(location_id)}" if location_id else ""
        by = f" ({source})" if source else ""
        print(f"[connector] received {self.item_name(item_id)}{where}{by} -> items_in.jsonl")
        rec = {"item_id": item_id, "from": source}
        if index is not None:
            rec["index"] = index
        with open(self.items_in, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    # ---- main loop (auto-reconnects; AP re-sends state on each connect, plugin dedups via the
    # item index watermark, and deaths_sent/hints_sent live on this instance so an in-process
    # reconnect never replays the DeathLink/hint backlog) ----
    #
    # server-hosted rooms (archipelago.gg/uploads) sit behind a TLS-terminating proxy that
    # expects wss:// on every room port, not just :443 - hitting it with plain ws:// gets a
    # real HTTP response back instead of a WS handshake ("did not receive a valid HTTP
    # response"). Self-hosted rooms (raw IP, no proxy) are usually plain ws:// only. Rather than
    # guess from the address, try wss:// first each cycle (matching the official AP clients'
    # behavior) and fall back to ws:// immediately if that specific attempt fails - then
    # remember whichever scheme actually worked so later reconnects don't pay the retry cost.
    async def run(self, server: str) -> None:
        delay = 5
        scheme_order = ["wss", "ws"]
        while True:
            # connect phase: try each scheme with no delay between them - a wrong scheme
            # fails the handshake near-instantly, so this doesn't meaningfully slow anything down.
            ws = None
            connect_errors = []
            for scheme in list(scheme_order):
                uri = f"{scheme}://{server}"
                try:
                    ws = await websockets.connect(uri, max_size=None)
                    if scheme_order[0] != scheme:          # this scheme won - prefer it next time
                        scheme_order.remove(scheme)
                        scheme_order.insert(0, scheme)
                    break
                except SystemExit:
                    raise
                except Exception as e:
                    connect_errors.append(f"{scheme}://: {e}")

            if ws is None:
                print(f"[connector] connection failed ({'; '.join(connect_errors)}); "
                      f"reconnecting in {delay}s...")
            else:
                delay = 5                                  # connected -> reset the backoff
                try:
                    await self._handshake(ws)
                    await asyncio.gather(self._pump_incoming(ws), self._pump_checks(ws))
                except SystemExit:
                    raise
                except Exception as e:
                    print(f"[connector] connection lost ({e}); reconnecting in {delay}s...")
                finally:
                    await ws.close()

            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)                         # 5 -> 10 -> 20 -> 40 -> 60s cap

    async def _handshake(self, ws) -> None:
        # Server sends RoomInfo first; grab the game list (for the datapackage), then Connect.
        room = json.loads(await ws.recv())[0]
        self._room_games = room.get("games", [])
        self._hint_cost_pct = room.get("hint_cost", 10)   # % of total locations per hint
        self._seed = room.get("seed_name", "")            # identifies THIS multiworld/regen
        self._reset_on_new_seed()
        await ws.send(json.dumps([{
            "cmd": "Connect",
            "game": GAME,
            "name": self.slot,
            "password": self.password or "",
            "uuid": "ark-asa-connector",
            "version": {"major": 0, "minor": 6, "build": 7, "class": "Version"},
            "items_handling": 0b111,
            "tags": ["DeathLink"],          # always tagged; behavior is gated by self.death_link (slot_data)
            "slot_data": True,
        }]))
        # item-name maps for every game, so we can name cross-game items in the "sent to X" line.
        await ws.send(json.dumps([{"cmd": "GetDataPackage", "games": self._room_games}]))

    async def _pump_incoming(self, ws) -> None:
        async for raw in ws:
            for msg in json.loads(raw):
                try:
                    self._handle(msg)
                except Exception:
                    print("[connector] error handling message:")
                    traceback.print_exc()

    def _handle(self, msg) -> None:
        cmd = msg.get("cmd")
        if cmd == "ReceivedItems":
            base = msg.get("index", 0)               # absolute index of the first item in this batch
            for i, it in enumerate(msg["items"]):
                src = self.players.get(it.get("player"), "")
                self.push_item(int(it["item"]), src, it.get("location"), base + i)
        elif cmd == "DataPackage":
            for game, gd in msg.get("data", {}).get("games", {}).items():
                self.game_items[game] = {v: k for k, v in gd.get("item_name_to_id", {}).items()}
                self.game_locs[game] = {v: k for k, v in gd.get("location_name_to_id", {}).items()}
        elif cmd == "Connected":
            self.players = {p["slot"]: (p.get("alias") or p.get("name", ""))
                            for p in msg.get("players", [])}
            self.my_slot = msg.get("slot")
            for s, info in (msg.get("slot_info") or {}).items():
                self.slot_game[int(s)] = info.get("game", "")
                self.slot_pname[int(s)] = info.get("name", str(s))
            for p in msg.get("players", []):       # prefer alias if set
                self.slot_pname[p["slot"]] = p.get("alias") or p.get("name", self.slot_pname.get(p["slot"], ""))
            sd = msg.get("slot_data") or {}
            # goal = defeat these boss base-tags (plugin signals defeats to boss_out.jsonl).
            self.goal_boss_tags = set(sd.get("goal_boss_tags") or [])
            if "death_link" in sd:                 # yaml option overrides the CLI default
                self.death_link = bool(sd["death_link"]) and not self._death_link_cli_off
            # relay plugin-side flags (the plugin reads ipc/flags.json)
            with open(os.path.join(self.ipc_dir, "flags.json"), "w", encoding="utf-8") as fh:
                json.dump({"bundle_saddles": bool(sd.get("bundle_saddles", False)),
                           "free_starter_engrams": bool(sd.get("free_starter_engrams", False))}, fh)
            self._total_locs = len(msg.get("checked_locations", [])) + len(msg.get("missing_locations", []))
            self._hint_points = msg.get("hint_points", 0)
            self._write_hint_status()
            try:
                self._write_spawn_ini(sd.get("npc_replacements") or [],
                                      sd.get("spawn_additions") or [],
                                      sd.get("spawn_overrides") or [])
            except Exception as ex:
                print(f"[connector] spawn-randomizer ini write failed: {ex}")
            print(f"[connector] connected as '{self.slot}' "
                  f"({len(msg.get('missing_locations', []))} locations remaining); "
                  f"goal = defeat {len(self.goal_boss_tags)} boss(es) "
                  f"[{', '.join(sorted(self.goal_boss_tags))}]")
        elif cmd == "RoomUpdate":
            if "hint_points" in msg:
                self._hint_points = msg["hint_points"]
                self._write_hint_status()
        elif cmd == "Bounced":
            # DeathLink: a remote player died -> kill ours (ignore our own echo).
            if self.death_link and "DeathLink" in msg.get("tags", []):
                data = msg.get("data", {})
                if data.get("source") != self.slot:
                    src = data.get("source", "someone")
                    cause = data.get("cause") or f"{src} died"
                    print(f"[connector] DEATHLINK from {src}: {cause} -> killing Ghios")
                    self._queue_msg(f"DeathLink: {cause}")     # show who/why in ARK chat
                    with open(self.death_in, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(data) + "\n")
        elif cmd == "ConnectionRefused":
            raise SystemExit(f"AP refused connection: {msg.get('errors')}")
        elif cmd == "PrintJSON":
            # when OUR check releases someone else's item, show it in ARK ("<us> sent X to Y").
            if msg.get("type") in ("ItemSend", "ItemCheat"):
                item = msg.get("item") or {}
                sender = item.get("player")
                receiver = msg.get("receiving")
                if sender == self.my_slot and receiver != self.my_slot and receiver is not None:
                    game = self.slot_game.get(receiver, "")
                    iname = self.game_items.get(game, {}).get(item.get("item"), f"item {item.get('item')}")
                    rname = self.slot_pname.get(receiver, str(receiver))
                    self._queue_msg(f"{self.slot} sent {iname} to {rname}")
            elif msg.get("type") == "Hint":
                item = msg.get("item") or {}
                recv, finder = msg.get("receiving"), item.get("player")
                if recv == self.my_slot or finder == self.my_slot:   # our hint
                    iname = self.game_items.get(self.slot_game.get(recv, ""), {}).get(item.get("item"), f"item {item.get('item')}")
                    lname = self.game_locs.get(self.slot_game.get(finder, ""), {}).get(item.get("location"), f"location {item.get('location')}")
                    fname = self.slot_pname.get(finder, str(finder))
                    found = "already found" if msg.get("found") else "not found yet"
                    self._queue_msg(f"Hint: {iname} is at {lname} in {fname}'s world ({found})")

    async def _pump_checks(self, ws) -> None:
        while True:
            new = self.poll_new_checks()
            if new:
                named = ", ".join(self.loc_name(c) for c in new)
                print(f"[connector] sending checks: {named}")
                await ws.send(json.dumps([{"cmd": "LocationChecks", "locations": new}]))
            await self._maybe_deathlink(ws)
            await self._maybe_hint(ws)
            await self._maybe_goal(ws)
            await asyncio.sleep(0.5)

    # randomize_dino_spawns: slot_data carries container OVERRIDES ("spawn_overrides":
    # [[container, [classes...]], ...] -> ConfigOverrideNPCSpawnEntriesContainer lines: each
    # biome container's spawn roster is REPLACED by its seeded species hand, at natural density).
    # Legacy shapes still render for old seeds: "spawn_additions" ->
    # ConfigAddNPCSpawnEntriesContainer, "npc_replacements" -> NPCReplacements pairs.
    # Always writes ipc/game_ini_fragment.txt; if connector.ini sets game_ini=<path to Game.ini>,
    # also patches that file in place, managing ONLY a marked block (everything else in the file
    # is left alone). Patch while the ARK server is STOPPED (it rewrites Game.ini on shutdown);
    # a server start applies the change.
    _INI_SECTION = "[/script/shootergame.shootergamemode]"
    _INI_BEGIN = "; === ArkAP NPCReplacements BEGIN (auto-managed, do not edit) ==="
    _INI_END = "; === ArkAP NPCReplacements END ==="
    _ADD_WEIGHT = 0.2          # additions: EntryWeight per invader (natives typically total ~1.0+)
    _ADD_MAX_PCT = 0.05        # additions: population cap per invader species per container

    @classmethod
    def _addition_line(cls, container: str, classes) -> str:
        entries = ",".join(
            f'(AnEntryName="AP_{c}",EntryWeight={cls._ADD_WEIGHT},NPCsToSpawnStrings=("{c}"))'
            for c in classes)
        limits = ",".join(
            f'(NPCClassString="{c}",MaxPercentageOfDesiredNumToAllow={cls._ADD_MAX_PCT})'
            for c in classes)
        return (f'ConfigAddNPCSpawnEntriesContainer=('
                f'NPCSpawnEntriesContainerClassString="{container}",'
                f'NPCSpawnEntries=({entries}),NPCSpawnLimits=({limits}))')

    @staticmethod
    def _override_line(container: str, entries) -> str:
        parts = []
        for e in entries:
            cls, w = (e, 1.0) if isinstance(e, str) else (e[0], e[1])  # "cls" or [cls, weight]
            parts.append(f'(AnEntryName="AP_{cls}",EntryWeight={w},NPCsToSpawnStrings=("{cls}"))')
        return (f'ConfigOverrideNPCSpawnEntriesContainer=('
                f'NPCSpawnEntriesContainerClassString="{container}",'
                f'NPCSpawnEntries=({",".join(parts)}))')

    def _write_spawn_ini(self, pairs, additions=(), overrides=()) -> None:
        lines = [f'NPCReplacements=(FromClassName="{a}",ToClassName="{b}")' for a, b in pairs]
        lines += [self._addition_line(container, classes) for container, classes in additions]
        lines += [self._override_line(container, classes) for container, classes in overrides]
        frag = os.path.join(self.ipc_dir, "game_ini_fragment.txt")
        if lines:
            with open(frag, "w", encoding="utf-8") as fh:
                fh.write(self._INI_SECTION + "\n" + "\n".join(lines) + "\n")
            parts = []
            if overrides: parts.append(f"{len(overrides)} container overrides")
            if additions: parts.append(f"{len(additions)} container additions")
            if pairs:     parts.append(f"{len(pairs)} legacy replacements")
            print(f"[connector] spawn randomizer: {' + '.join(parts)} -> {frag}")
        elif os.path.exists(frag):
            os.remove(frag)
        if not self.game_ini:
            if lines:
                print("[connector] set game_ini in connector.ini to auto-apply "
                      "(or paste the fragment into Game.ini yourself), then restart the ARK server")
            return
        try:
            with open(self.game_ini, encoding="utf-8") as fh:
                txt = fh.read()
        except OSError:
            txt = ""
        import re
        txt = re.sub(re.escape(self._INI_BEGIN) + r".*?" + re.escape(self._INI_END) + r"\n?",
                     "", txt, flags=re.S)                       # drop any previous managed block
        if lines:
            block = self._INI_BEGIN + "\n" + "\n".join(lines) + "\n" + self._INI_END + "\n"
            idx = txt.lower().find(self._INI_SECTION.lower())
            if idx == -1:                                       # no section yet -> append one
                txt = (txt.rstrip() + "\n\n" if txt.strip() else "") + self._INI_SECTION + "\n" + block
            else:                                               # insert right under the section header
                at = txt.find("\n", idx) + 1
                txt = txt[:at] + block + txt[at:]
        with open(self.game_ini, "w", encoding="utf-8") as fh:
            fh.write(txt)
        print(f"[connector] patched {self.game_ini} "
              f"({'applied ' + str(len(lines)) + ' replacements' if lines else 'removed old block'})"
              " - restart the ARK server to take effect")

    # /buyhint: the plugin queued an item name -> ask AP to hint it (via the !hint chat command).
    async def _maybe_hint(self, ws) -> None:
        if not os.path.exists(self.hint_out):
            return
        with open(self.hint_out, encoding="utf-8") as fh:
            lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
        if len(lines) < self.hints_sent:
            self.hints_sent = len(lines)
        for item in lines[self.hints_sent:]:
            self.hints_sent += 1
            print(f"[connector] hint request -> !hint {item}")
            await ws.send(json.dumps([{"cmd": "Say", "text": f"!hint {item}"}]))

    # broadcast a DeathLink when our player dies (the plugin appended to death_out.jsonl).
    async def _maybe_deathlink(self, ws) -> None:
        if not self.death_link or not os.path.exists(self.death_out):
            return
        with open(self.death_out, encoding="utf-8") as fh:
            total = sum(1 for line in fh if line.strip())
        if total < self.deaths_sent:           # file was reset -> resync
            self.deaths_sent = total
        for _ in range(total - self.deaths_sent):
            self.deaths_sent += 1
            print(f"[connector] Ghios died -> broadcasting DeathLink")
            await ws.send(json.dumps([{
                "cmd": "Bounce",
                "tags": ["DeathLink"],
                "data": {"time": time.time(), "source": self.slot, "cause": f"{self.slot} died in ARK"},
            }]))

    # win when every required boss base-tag has appeared in boss_out.jsonl (plugin-signalled) ->
    # tell AP we reached the goal. Boss kills are the goal, not AP check locations.
    def _defeated_bosses(self) -> set:
        out: set = set()
        try:
            with open(self.boss_out, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        out.add(line)
        except OSError:
            pass
        return out

    async def _maybe_goal(self, ws) -> None:
        if self.goaled or not self.goal_boss_tags:
            return
        if self.goal_boss_tags <= self._defeated_bosses():
            self.goaled = True
            print(f"[connector] all goal bosses defeated ({', '.join(sorted(self.goal_boss_tags))}) "
                  f"-> GOAL reached, sending CLIENT_GOAL")
            await ws.send(json.dumps([{"cmd": "StatusUpdate", "status": CLIENT_GOAL}]))


def _load_config(path: str) -> dict:
    """Read [connector] from an ini file (server/slot/password/ipc_dir/multiplayer/data_dir/
    boss_goal_count/death_link). Returns {} if the file is absent. CLI flags override these."""
    import configparser
    if not path or not os.path.exists(path):
        return {}
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    if not cp.has_section("connector"):
        return {}
    return dict(cp.items("connector"))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="ARK <-> Archipelago connector. Set options in connector.ini or pass flags "
                    "(flags override the ini).")
    import sys
    # next to the exe when frozen (PyInstaller), else next to this script.
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    default_ini = os.path.join(base, "connector.ini")
    ap.add_argument("--config", default=default_ini, help="path to connector.ini (default: next to this script)")
    ap.add_argument("--server", help="host:port, e.g. archipelago.gg:38281")
    ap.add_argument("--slot")
    ap.add_argument("--password", default=None)
    ap.add_argument("--ipc-dir")
    ap.add_argument("--multiplayer", action="store_true",
                    help="multiplayer: use ipc_dir/<slot> as this player's mailbox (auto-created). "
                         "Your ARK survivor name must equal your slot. Omit for solo.")
    ap.add_argument("--boss-goal-count", type=int, default=None,
                    help="fallback goal: first N bosses (overridden by the yaml goal via slot_data)")
    ap.add_argument("--data-dir", default=None,
                    help="folder with engrams/locations/dinos/crates.json for naming "
                         "(default: the folder containing the ipc dir)")
    ap.add_argument("--no-death-link", action="store_true",
                    help="disable DeathLink (default: on)")
    ap.add_argument("--game-ini", default=None,
                    help="path to the server's Game.ini - auto-applies randomize_dino_spawns "
                         "(omit to only write ipc/game_ini_fragment.txt)")
    a = ap.parse_args()

    cfg = _load_config(a.config)
    server = a.server or cfg.get("server")
    slot = a.slot or cfg.get("slot")
    password = a.password if a.password is not None else (cfg.get("password") or None)
    ipc_dir = getattr(a, "ipc_dir", None) or cfg.get("ipc_dir")
    data_dir = a.data_dir or cfg.get("data_dir") or None
    boss_goal = a.boss_goal_count if a.boss_goal_count is not None else int(cfg.get("boss_goal_count", 4))
    # death_link: default on; ini can set false; --no-death-link forces off.
    death_link = str(cfg.get("death_link", "true")).strip().lower() not in ("false", "0", "no", "off")
    if a.no_death_link:
        death_link = False

    missing = [n for n, v in (("server", server), ("slot", slot), ("ipc_dir", ipc_dir)) if not v]
    if missing:
        ap.error(f"missing required setting(s): {', '.join(missing)} - set them in "
                 f"{a.config} (or pass --{missing[0].replace('_', '-')} ...)")

    game_ini = a.game_ini or cfg.get("game_ini") or None
    multiplayer = a.multiplayer or str(cfg.get("multiplayer", "false")).strip().lower() \
        in ("true", "1", "yes", "on")

    if multiplayer:
        print(f"[connector] multiplayer: slot '{slot}' -> mailbox "
              f"{os.path.join(ipc_dir, _sanitize_route(slot))} "
              f"(your ARK survivor name must be '{slot}')")

    bridge = Bridge(ipc_dir, slot, password, boss_goal, data_dir, death_link=death_link,
                    game_ini=game_ini, multiplayer=multiplayer)
    asyncio.run(bridge.run(server))


if __name__ == "__main__":
    main()
