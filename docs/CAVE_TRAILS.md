# Cave trails: drawing cave maps from where you walk

Wiki cave maps are hand drawn and often missing or wrong. `/aptrail` records your exact path through
a cave, and `tools/cave_trail_map.py` turns one or more recordings into a guide map that someone
who has never been in the cave can follow. The map shows:

- the cave floor;
- the route to each artifact, coloured by what you do on each stretch (walk, swim, drop, climb);
- entrance arrows, artifact icons, crates and buttons;
- a legend, compass and scale bar.

It also gives pixel positions for PopTracker's cave tabs.

## 1. Record in game (plugin v211+ for the coloured routes)

| Command | What it does |
|---|---|
| `/aptrail start <cave name>` | Start recording you. Restarts if you were already recording. |
| `/aptrail entrance <name>` | Mark a cave entrance where you stand (`North`, `Waterfall`, ...). |
| `/aptrail route <artifact> [from <entrance>]` | Start **the** line to that artifact, from where you stand. With `from`, the entrance is marked here too. |
| `/aptrail arrived` | At the artifact: end the route and mark the artifact here. |
| `/aptrail route cancel` | Drop the route in progress (took a wrong turn - start it again). |
| `/aptrail swim` | You are going underwater: from here the line is drawn as water. (v217+) |
| `/aptrail fly` | You are taking off on a flyer: from here the line is drawn as "flyer needed". (v217+) |
| `/aptrail wire` | Stepping onto a thin wire / beam: from here it is drawn as a 1 m strip with a yellow-black striped line, on top of anything underneath. Also `beam`, `tightrope`. (v220+) |
| `/aptrail lava` | Walking the **safe ground** of a lava room (islands, ledges, walkways): what you walk is drawn as floor with lava all round it. Walk every bit of safe ground, then `/aptrail walk`. Also `safe`. (v222+) |
| `/aptrail walk` | Back on foot: from here the line is a normal path again. (v217+) |
| `/aptrail room` | (Optional) A lap round a big room starts here. (v218+) |
| `/aptrail fillroom` | Back where the lap started: fill everything inside the lap as floor. Without `/aptrail room`, the lap is closed at the last time your trail passed within 6 m of here (after at least 15 m of walking). (v218+) |
| `/aptrail fillroom lava` | Same, but the room is **lava** (or `water`): filled in red (or blue), and the lap is only its outline, not drawn as floor. Then walk the safe ground through it; that is drawn as floor on top. (v219+) |
| `/aptrail room cancel` | Forget the `/aptrail room` start. |
| `/aptrail mark <label>` | Drop a labelled point where you stand (see the keywords below). |
| `/aptrail pause` | Stop recording for now (going to sort loot, fetch a tame, take a break). The cave name, swim/fly/wire mode, open route and open room are all kept. (v221+) |
| `/aptrail resume` | Carry on recording. The map never draws a line between where you paused and where you resume, so it is fine to move in between. If a route or room lap is open, resume where you paused or it gets a gap. Also `continue`. (v221+) Starting `/aptrail route` or `/aptrail room` while paused resumes by itself (v223+); a route with no trail points is not drawn. |
| `/aptrail stop` | Finish and save. Chat shows the file name, points, routes and distance. |
| `/aptrail` | Show whether you are recording, and the usage. |

- Your position is sampled every 0.25 s, together with how you are moving: walking, falling,
  swimming, or on a climbing pick. When riding, it is how the dino is moving. Nothing is written
  while you stand still.
- **Say when you swim or fly.** Run `/aptrail swim` as you go under, `/aptrail fly` as you take
  off, and `/aptrail walk` when you are back on foot. A recording starts as walking. The game
  cannot be trusted for this: a flying mount reports itself as *swimming*, so a cave explored on a
  flyer came out as one big lake. As soon as a recording uses these commands, only they decide
  what is water or flight.
- Several players can record at the same time; each gets their own file.
- Logging out pauses the recording. It continues if you come back before `/aptrail stop`.

### Mark keywords

The first word of `/aptrail mark` picks the symbol. The rest is shown next to it.

| You type | On the map |
|---|---|
| `/aptrail mark crate blue` | A crate icon (`white`, `green`, `blue`, `purple`, `yellow`, `red`). `Red Cave Drop` or `blue crate` anywhere in the label works too. |
| `/aptrail mark button opens the gate` | Red hexagon, "opens the gate" |
| `/aptrail mark climb use the pick` | Red triangle |
| `/aptrail mark drop` | Orange down-triangle |
| `/aptrail mark water dive here` | Blue circle |
| `/aptrail mark danger lava` | Yellow warning triangle |
| `/aptrail mark artifact Clever` | The artifact's icon (prefer `/aptrail arrived` at the end of a route) |
| anything else | White dot with your text |

### Tips for a good map

- Walk **every** branch, including dead ends. The cave floor is drawn only where someone walked.
- **Big rooms:** run `/aptrail room` at a spot on the wall, walk all the way round the edge, and
  run `/aptrail fillroom` when you are back at that spot. The inside is drawn as floor at the
  lap's average height (water if you were mostly swimming). No need to walk back and forth.
  - Keep close to the wall: the fill is exactly the shape you walked.
  - A room with a pit or pillar in the middle is filled over it - mark it (`/aptrail mark danger pit`).
  - Forgot `/aptrail room`? `/aptrail fillroom` alone works if you end the lap within 6 m of
    where it began.
- **Thin wires / beams** (fall off and you end up back at the start):
  1. At the wire: `/aptrail mark danger thin wire, falling sends you back` and `/aptrail wire`.
  2. Walk it, then `/aptrail walk` once you are on solid ground.
  3. Fell off during a route? `/aptrail route cancel`, go back and start the route again. Only a
     clean run should be the route.
  4. Worth doing once on purpose while exploring (not in a route): fall off and walk back, so the
     map shows where a fall takes you.
  - No need to type anything when you fall. A fall off the wire of 3 m or more ends the wire where
    you land: from there it is walking again (until your next `/aptrail` command). The map shows an
    orange drop symbol labelled "Fall off the wire: N m down", and the way back as the lower,
    darker floor it really is. The symbol shows even when the map has routes.
  - If a fall teleports you back to the start instead, that also ends the wire, and a drop mark
    "fall off the wire = back to start" is put where you were last on the wire.
  - The wire is always drawn above the floor, so a passage below it never hides it.
- **Lava rooms where you walk the safe parts** (an island you grapple to, rings and walkways with
  lava between): `/aptrail lava` as you step onto the safe ground, walk all of it (every ring,
  spoke and island), then `/aptrail walk` when you leave. The map draws lava all round what you
  walked. You do not walk the lava's edge for this, and no `room`/`fillroom` is needed.
- **`/aptrail fillroom` checks the lap at once** (v222+). Chat says the lap's size, e.g. "room
  filled (lap 64 m round, about 300 m2)", or **NOT FILLED** when there is no lap back to where
  you stand, or when it is a tiny loop under 25 m2 without `/aptrail room`. Redo it right there.
- **Lava rooms you go round the edge of** (a safe path through a room of lava):
  1. `/aptrail fly` (or walk, if the edge is safe), `/aptrail room`, go round the lava's edge.
  2. Back at the start: `/aptrail fillroom lava`. The whole room turns to lava on the map.
  3. `/aptrail walk` and walk the safe ground through the middle. It is drawn as floor on top of
     the lava, so the map shows exactly where it is safe to stand.
  - `/aptrail fillroom water` does the same for a lake with a path through or round it.
- **Routes:**
  - Stand in the entrance and run `/aptrail route Clever from North`.
  - Walk the way you would want someone to go, then run `/aptrail arrived` at the artifact.
  - Caves with several entrances: record one route per entrance (`... from Waterfall`).
  - `Clever`, `artifact Clever` and `Artifact of the Clever` all mean the same artifact.
  - Exploring and routes can be mixed in one recording. Only the part between `route` and
    `arrived` counts as the route.
- **Drops and climbs are still found automatically** (on foot):
  - A fall that loses at least 3 m becomes an orange "drop down" stretch.
  - Time on a climbing pick becomes red.
  - Both get a symbol on the map, because they are near vertical and their line would not show
    from above. The symbol is labelled with the height, floor to floor (`12 m down`, `5 m up`).
- **Floor height** is shown by colour: slate (lowest), brown, tan, sand, cream (highest). The
  scale covers that cave's own floor (flying is left out), at least 10 m, and the legend says its
  range in metres from the entrance (`Cave floor: -385 m to +0 m from the entrance`). The floor is
  drawn in 10 height layers, lowest first, each with its dark edge: where a higher passage runs over
  a lower one it has a dark rim, and a slope shows rims like steps, the higher side on top.
- **Rooms vs passages:** each walk is drawn as a passage about 5 m wide (at least ~13 px on the
  picture). Passes walked side by side less than about 12 m apart are filled in as one room, so a
  room you criss-crossed reads as a room, while passages further apart stay separate.
- Several sessions are fine. Run `/aptrail start` with the same cave name each time and render all
  the files together.
- Teleporting, dying or respawning mid-recording is fine. A jump of more than 2500 units in one
  sample is treated as a gap, not as a passage.

## 2. Collect the files

Files are written on the ARK server, in the plugin folder:

    ShooterGame/Binaries/Win64/ArkApi/Plugins/ArkAP/trails/<map>/<cave>_<date>-<time>_<steamid>.jsonl

Copy them to this repo, e.g. `trails/ragnarok/`. They are plain text, one JSON object per line:
- a `start` line names the map and cave;
- each point line is `{"t", "x", "y", "z", "m", "c"}`: world units, `m` the movement mode
  (1 walk, 3 fall, 4 swim), and `c` present while on a climbing pick;
- `mark`, `route_start`, `route_end`, `route_cancel` and `stop` lines carry the marks, routes and
  totals;
- `mode` lines (`"mode": "swim" | "fly" | "wire" | "lava" | "walk"`) are the `/aptrail swim|fly|wire|lava|walk` switches;
- `room_start`, `fillroom` (with `"fill": "lava" | "water"` for a hazard room) and `room_cancel` lines are the room commands;
- `pause` and `resume` lines are `/aptrail pause | resume`. No points are written in between, and the
  line breaks at the resume.

Trails are not run state. The reset script and a new seed never delete them.

## 3. Render

```bash
python tools/cave_trail_map.py trails/ragnarok/life_s_labyrinth_*.jsonl --out build/caves/life_s_labyrinth.png
```

The map is top-down with north up, and has a legend panel on the left.

| Layer | What it shows |
|---|---|
| Cave floor | A wide band along every walk. Lighter means higher, so stacked passages can be told apart. Swum stretches are dark blue (water). |
| Routes | Bold lines on top: **green** walk, **blue** swim, **purple** fly (flyer needed), **yellow-black stripes** thin wire, **orange** drop down, **red** climb. Each route's length is listed under "Routes" in the legend. A cancelled or unfinished route is not drawn. |
| Entrances | A blue arrow pointing in, in the direction its route starts, plus the entrance name and its in-game GPS `(lat, lon)`, e.g. `Entrance: Stair Entrance  (24.7, 25.0)`. Mark the entrance at the cave mouth so the coordinates are where someone outside would head for. |
| Artifacts | Their own icon in a white circle, plus "Artifact of the X". |
| Marks | The symbols from the keyword table. Labels are placed so they do not overlap each other. |
| Extras | Title (the cave name, or `--title`), the map name, a compass, a scale bar in metres. The legend lists only what the map uses. |

Options:

| Option | Effect |
|---|---|
| `--routes-only` | Floor and lines from the routes only, leaving out other walking. Same scale and pin pixels as the full image. |
| `--route-colours entrance` | One colour per entrance instead of the walk/swim/drop/climb colours. |
| `--corridor 800` | Wider cave floor, in world units. The default 500 is 5 m. |
| `--size` | Map area, longest side, default 2000. |
| `--pad` | Border around the map. |
| `--no-legend` | Map only, no legend panel. |
| `--jump` | Teleport threshold, in world units. |
| `--text-scale 0.8` | Make every text (map labels, legend, title) smaller (`0.8`) or bigger (`1.3`). Default 1. |
| `--edits FILE` / `--no-edits` | Use another hand-edits file, or none (default: `<trails dir>/<cave>.edits.json` when it exists, see "Editing a map by hand"). |
| `--no-modes` | Ignore the game's movement modes: floor shaded by height, drops guessed. For a recording made before v217 (no `/aptrail swim|fly`) on a flying mount, which records as **swimming** and otherwise turns the whole map into water. `/aptrail swim|fly|walk` switches still apply. |

Recordings made before v211 have no movement modes. They still render: drops are guessed from
height, and swims and climbs show as plain walking.

A `.json` beside the image holds:
- the world-to-pixel transform;
- every mark, in pixels and world units, plus `gps` `[lat, lon]`;
- `pins` for each artifact;
- `routes`, each with its pixel polyline and length.

### Editing a map by hand

```bash
python tools/cave_map_edit.py
```

With no cave name this opens the **gallery** (`http://127.0.0.1:8765/`): every cave under
`trails/`, grouped by map, with its picture, entrances, artifacts and routes, how many recordings
and hand edits it has, and **Edit**, **Open PNG** and **Download** buttons. Click a picture to see
it full screen; the box at the top filters by cave, map or artifact. Pictures are rendered again by
themselves when a recording, its edits or the renderer is newer than the PNG, and recordings
copied into `trails/` while it runs show up when you reload the page.

```bash
python tools/cave_map_edit.py jungle_dungeon
```

With a cave name (`jungle_dungeon` or `"Jungle Dungeon"`, or file paths) it opens that cave's
editor straight away. Both work from any folder; Ctrl+C in the terminal stops it. The editor's
**All maps** button goes back to the gallery.

| Tool | Key | What it does |
|---|---|---|
| Erase trail | E | Removes the recorded trail where you paint, **floor and line**: a wrong turn, a fall, a bit flown outside the cave. The line breaks there. |
| Erase line | X | Removes only the green (swim, fly...) pathway line where you paint. The floor stays. Use this to clean up lines you do not want shown as the way. |
| Lava | L | Paints lava. It goes under every floor, so paint freely round the safe ground. |
| Water | W | Paints water, the same way. |
| Floor | F | Paints cave floor (with the dark edge), e.g. a bit of a room you did not walk. Each painted spot takes the height (colour) of the nearest point recorded in game. |
| Pathway | R | Draws a route by hand, for one that was not recorded right (e.g. walked while paused). Brush: drag freehand. Polygon: click the bends, Enter or double-click to finish. The start snaps to an entrance and the end to an artifact when you finish near them, then it asks which artifact and which entrance. Drawn like a recorded route (green, listed under Routes with its length), with no floor under it. Where it goes along a recorded thin wire from one end to the other (within 6 m of both ends, never more than 8 m off it), that stretch is drawn as the wire; just crossing a wire stays green. |
| Place | C | Puts a mark where you click. Pick in the list first: a crate colour (red, yellow, blue, green, purple, white) or Note (asks for the text, which works like `/aptrail mark` text, so `danger ...` gets the danger symbol). |
| Remove mark | M | Click a crate, note, entrance or other mark to remove it. Click its red X to bring it back. A mark you placed in the editor is deleted outright. |
| Brush / Polygon | B / P | (Erase, paint and Pathway.) Brush: drag to paint, size slider or `[` `]`. Polygon: click the corners, double-click or Enter to close, Backspace removes a corner, Esc cancels. |

- **Save & render** (Ctrl+S) saves the edits and draws the map again; the page shows the result.
  The PNG is written to `build/caves/<cave>.png` (or `--out`) on every save; **Download PNG**
  saves a copy wherever you like.
- Undo / Redo (Ctrl+Z / Ctrl+Y). The Edits list on the right shows every edit; hover to find it
  on the map, Delete to remove it. "Show edits" hides the overlay to see the clean map.
- **Swim = lava** (checkbox): everything swum through in this cave is drawn as lava instead of
  water, for a cave whose "water" is lava you cross on a mount (Valguero's Great Trench). Saved as
  `"swim_is": "lava"` in the edits file.
- Pan with right-drag (or Space + drag), zoom with the mouse wheel, `0` fits the map.
- **The recordings are never changed.** Edits are saved in world units beside them, as
  `trails/<map>/<cave>.edits.json` (the previous one is kept as `.edits.json.bak`), and
  `cave_trail_map.py` applies that file on every render, `--routes-only` too. So they survive
  re-renders and new recordings of the same cave. `--no-edits` renders without them.
- Options after `--` go to the renderer: `... -- --corridor 300 --text-scale 0.8`. `--out` and
  `--port` are the editor's own.

### Drawing on an existing cave image

This needs two or more marks whose pixel on that image is known:

```bash
python tools/cave_trail_map.py trails/ragnarok/life_s_labyrinth_*.jsonl \
    --base tools/reference/poptracker_art/maps/Ragnarok_Life_s_Labyrinth.png \
    --pins ragnarok --out build/caves/life_s_labyrinth_overlay.png
```

- `--pins <map>` anchors your artifact marks to that map's `_cave_pins` in
  `tools/reference/poptracker_logic.json`.
- `--anchor "entrance North=512,900"` pins any other mark. It can be repeated.
- The script fits one scale and offset and prints each anchor's error in pixels. A large error
  means a mark or a pin is in the wrong place.

## 4. Use it in PopTracker

```bash
python tools/cave_maps_to_poptracker.py
python tools/poptracker_add_map.py --all
```

The first command does it for every recorded cave (the ones in the gallery):
- renders it if it is out of date, with your hand edits;
- copies the picture to `tools/reference/poptracker_art/maps/<Map>_<Cave>.png` (e.g.
  `Ragnarok_Frozen_Dungeon.png`), which replaces that cave tab's "Coming Soon" card;
- writes each artifact's pixel into that map's `artifacts._cave_pins` in
  `tools/reference/poptracker_logic.json`, so the tab gets a pin right on the artifact icon.

Only caves that hold an artifact in that file's `artifacts._caves` have a tab. Every artifact listed
for the cave must be marked on the map (`/aptrail arrived`, or `artifact <Name>` placed in the
editor), or the cave is skipped with the reason; an artifact mark that belongs to another cave is
reported. `--dry-run` shows what it would do. The second command rebuilds the pack into
`build/poptracker_pack`; install it as usual.

An artifact spot listed in `artifacts._no_cave_tab` keeps its pin on the map and gets no tab.
Ragnarok's Pit of Dreen (open ocean, no cave) does have a tab: the in-game GPS screenshot
(47.3, 2.2) with a title box, `Ragnarok_Pit_of_Dreen.png` (original in `_source/maps/`), and its
Devourer pin in `_cave_pins` was placed by hand in that box.

A cave image from somewhere else (like Life's Labyrinth) still works by hand: save it under the
same name pattern (original in `_source/maps/`), add its pins to `_cave_pins`, and say where they
came from in `_source`.
