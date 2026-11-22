"""Draw a readable cave guide map from /aptrail recordings.

The plugin's `/aptrail start <cave>` writes the player's position every 0.25 s to
<plugin>/trails/<map>/<cave>_<date>_<steamid>.jsonl, with how they were moving (walking, falling,
swimming, on a climbing pick) and every mark, entrance and route. Copy those files here and run:

    python tools/cave_trail_map.py trails/ragnarok/life_s_labyrinth_*.jsonl --out build/cave.png

The picture is meant to be read by someone who has never been in the cave:

  * the cave floor is a wide band along everywhere anyone walked (slate low ... cream high, in
    height layers with a dark rim on the higher one), water in blue;
  * each route (/aptrail route <artifact> from <entrance> ... /aptrail arrived) is drawn on top,
    coloured by what you do there: green walk, blue swim, orange drop down, red climb;
  * entrances get a blue arrow pointing in, artifacts their own icon, and marks with a known first
    word an icon too: crate [colour], button, climb, drop, water, danger (anything else is a note);
  * title, legend (only what the map uses), route lengths, compass and scale bar.

Several recordings of the same cave are merged, so walk it in as many sessions as you like.

Placing the drawing:
  * default: top-down, north up, fitted into --size px with the legend in a panel on the left.
  * on an existing cave image: --base <image> plus 2+ anchors tying a mark to a pixel, either
    --anchor "artifact Clever=1005,264" (repeatable) or --pins <map> for the _cave_pins of
    tools/reference/poptracker_logic.json. A uniform scale + offset is fitted by least squares and
    each anchor's error is printed.

Also writes <out>.json: the world->pixel transform, marks and routes in pixels, and "pins"
({"Clever": [x, y]} per artifact mark) ready for _cave_pins. See docs/CAVE_TRAILS.md.
"""
import argparse
import glob
import json
import math
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGIC = os.path.join(ROOT, "tools", "reference", "poptracker_logic.json")
ART_RE = re.compile(r"^\s*artifact(?:\s+of\s+the)?\s+(\w+)\s*$", re.I)
UNITS_PER_M = 100.0                      # ARK world units are centimetres

# route colours by what the player was doing on that stretch
KIND_COLOUR = {"path": (70, 205, 80), "water": (40, 150, 255), "drop": (255, 140, 0),
               "climb": (235, 45, 45), "fly": (190, 110, 255), "wire": (255, 215, 0)}
KIND_LABEL = {"path": "Pathway", "water": "Underwater / swim", "drop": "Drop down",
              "climb": "Climb (pick / grapple)", "fly": "Fly (flyer needed)",
              "wire": "Thin wire - falling sends you back"}
# /aptrail swim | fly | walk (plugin v217+): the recorder's own word for each stretch
MANUAL_KIND = {"swim": "water", "fly": "fly", "walk": None, "wire": "wire", "lava": None}
# /aptrail lava (v222): walking the SAFE ground of a lava room - drawn as floor, with lava this far
# beyond the floor's edge all round it
LAVA_AROUND = 600
ROOM_MIN_AREA = 25 * 100 * 100          # an automatic fillroom lap must enclose 25 m2, or it is a wiggle
WIRE_WIDTH = 100                         # /aptrail wire: the floor strip is 1 m, not the corridor
# one colour per entrance, for --route-colours entrance
ENTRANCE_COLOURS = [(255, 200, 0), (255, 60, 220), (0, 230, 255), (255, 255, 255), (255, 120, 0),
                    (150, 255, 60)]
# floor height ramp, lowest -> highest: slate, brown, tan, sand, cream. No blue (water) and no
# orange/red (lava), so height never reads as a hazard.
FLOOR_RAMP = [(82, 78, 118), (112, 90, 78), (150, 118, 82), (206, 178, 126), (248, 238, 208)]
ROOM_GAP_FILL = 1200     # gaps up to ~12 m between passes walked side by side fill in as room
ARTIFACT_SIZE = 0.5      # artifact icon size as a share of the map icon size (was 0.95; user 2026-11-18: too big)
ARTIFACT_OUTLINE = 3     # px of white outline round an artifact icon (follows the art, no disc)
FLOOR_MIN_SPAN = 2000   # the colour scale spans at least 20 m (a flat cave keeps mid tones)
FLOOR_LEVEL_MIN = 300   # a height layer is at least 3 m: in a nearly flat cave (Snow Mountains, 8 m;
                        # The Monkey's Puzzle, 7 m) ten layers were under 1 m each, so every bump of
                        # the floor got its own dark outline and the map looked choppy (2026-11-18)
FLOOR_LEDGE = 500       # a layer's dark rim is drawn over lower floor only when that floor is >5 m (and
                        # more than one layer step) lower
FLOOR_LEVELS = 10       # the floor is drawn in this many height layers, lowest first, each with its
                        # dark edge: a higher passage over a lower one gets a rim, a slope gets steps
FLOOR_EDGE = (52, 44, 34)
WATER_FLOOR = (38, 78, 135)
LAVA_FLOOR = (196, 58, 18)
BG = (14, 14, 18)
ARROW = (30, 140, 255)
DROP_MIN = 300                           # a fall must lose this much height to count as a drop
ROOM_CLOSE = 600                         # /aptrail fillroom: the lap closes within 6 m of here
ROOM_MIN_LAP = 1500                      # ...and only after at least 15 m of walking
# mark keywords -> (legend text, drawer name)
MARK_KINDS = {"crate": ("Cave drop (crate)", "crate"), "drop": ("Drop down here", "drop"),
              "button": ("Button", "button"), "climb": ("Climb here", "climb"),
              "water": ("Water / dive", "water"), "swim": ("Water / dive", "water"),
              "danger": ("Danger", "danger")}
CRATE_COLOURS = ("white", "green", "blue", "purple", "yellow", "red")


# ---------------------------------------------------------------------------------- loading ----
def load_trails(paths, jump):
    """-> (segments [[pt]], marks [dict], headers, routes).

    pt = {"x", "y", "z", "m", "c"} (m = movement mode or None, c = climbing). A route is the walk
    between `route_start` and `route_end`: {"artifact", "from", "parts": [[pt]], "distance"}. One
    cancelled or never finished is dropped (and said so)."""
    segs, marks, heads, routes, rooms = [], [], [], [], []
    for p in paths:
        cur, last, route = [], None, None
        fall_top = None                   # /aptrail wire: height where a fall off it began
        on_wire = 0                       # ...and points walked on it since /aptrail wire
        paused = False                    # /aptrail pause until /aptrail resume
        lap, room_at = [], None           # every point of this file in order; /aptrail room index
        # A recording that uses /aptrail swim|fly|walk says itself what each stretch is: the game's
        # movement mode is then ignored for swim/fly (a flying mount reads as SWIMMING). Points
        # before the first such command are walking. Climbing pick + falls stay automatic.
        with open(p, encoding="utf-8") as fh:
            manual = "walk" if '"type": "mode"' in fh.read().replace('"type":"mode"', '"type": "mode"') else None
        with open(p, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    print("  %s:%d: not JSON, skipped (file cut off mid-write?)" % (p, n))
                    continue
                kind = o.get("type")
                here = {"x": o.get("x"), "y": o.get("y"), "z": o.get("z"), "m": o.get("m"),
                        "c": bool(o.get("c")), "k": manual}
                if kind == "mode":
                    manual = o.get("mode") if o.get("mode") in MANUAL_KIND else "walk"
                    fall_top, on_wire = None, 0
                    here["k"] = manual
                    if not paused:                # the switch point itself starts the new stretch
                        cur.append(here)
                        if route:
                            route["parts"][-1].append(here)
                        last = here
                elif kind == "pause":
                    paused = True
                elif kind == "resume":
                    # /aptrail pause | resume (v221): you may have moved anywhere in between, so the
                    # line breaks here like a teleport - never drawn as a passage
                    paused = False
                    if len(cur) > 1:
                        segs.append(cur)
                    cur, last, fall_top = [], None, None
                    if route and route["parts"][-1]:
                        route["parts"].append([])
                elif kind == "room_start":
                    room_at = len(lap)
                elif kind == "room_cancel":
                    room_at = None
                elif kind == "fillroom":
                    ring = room_lap(lap, room_at, here)
                    if ring and room_at is None and lap_area(ring) < ROOM_MIN_AREA:
                        print("  %s: /aptrail fillroom at (%d, %d) - the lap back to here only encloses "
                              "%.0f m2, not filled (say /aptrail room at the edge first; for the safe "
                              "ground of a lava room use /aptrail lava)"
                              % (os.path.basename(p), here["x"], here["y"], lap_area(ring) / 1e4))
                        ring, room_at = None, None
                        continue
                    room_at = None
                    fill = o.get("fill") if o.get("fill") in ("lava", "water") else "floor"
                    if ring:
                        # A lava/water room is a hazard you go AROUND (often flying), so its lap
                        # is only the outline: not drawn as floor. Walk the safe ground after
                        # the fill and that walk is drawn as floor on top of it.
                        if fill != "floor":
                            for q in ring:
                                q["hide"] = True
                        rooms.append({"pts": ring, "fill": fill})
                    else:
                        print("  %s: /aptrail fillroom at (%d, %d) - no lap back to here found "
                              "(walk the whole edge and finish where you started), not filled"
                              % (os.path.basename(p), here["x"], here["y"]))
                elif kind == "start":
                    heads.append(o)
                elif kind == "mark":
                    marks.append({"label": o["label"], "x": o["x"], "y": o["y"], "z": o["z"],
                                  "kind": o.get("kind", ""), "name": o.get("name", ""),
                                  "file": os.path.basename(p)})
                elif kind == "route_start":
                    # while paused no trail is written, so the start is not joined to anything
                    route = {"artifact": o["artifact"], "from": o.get("from") or "",
                             "parts": [[] if paused else [here]], "file": os.path.basename(p),
                             "samples": 0}
                elif kind == "route_end" and route:
                    if not paused:
                        route["parts"][-1].append(here)
                    route["distance"] = o.get("distance", 0)
                    route["parts"] = [q for q in route["parts"] if len(q) > 1]
                    if not route["samples"] or not route["parts"]:
                        # only its start and end are known: a straight line between them would be
                        # a guess, not the way, so nothing is drawn
                        print("  %s: route to %s%s has no trail points (recorded while paused - "
                              "/aptrail resume was never said?), not drawn. Record it again."
                              % (os.path.basename(p), route["artifact"],
                                 " from " + route["from"] if route["from"] else ""))
                    else:
                        routes.append(route)
                    route = None
                elif kind == "route_cancel":
                    if route:
                        print("  %s: route to %s cancelled (%s), not drawn"
                              % (os.path.basename(p), route["artifact"], o.get("why", "")))
                    route = None
                elif kind is None and "x" in o:
                    # a jump this far in one 0.25 s sample is a teleport, respawn or log-in at
                    # another spot - never a passage, so do not draw across it
                    if last and math.dist((last["x"], last["y"], last["z"]),
                                          (here["x"], here["y"], here["z"])) > jump:
                        if len(cur) > 1:
                            segs.append(cur)
                        cur = []
                        if route:
                            route["parts"].append([])
                        if manual == "wire" and on_wire:
                            # sent back to the start mid-wire (a fall that teleports): off the wire
                            marks.append({"label": "drop fall off the wire = back to start",
                                          "x": last["x"], "y": last["y"], "z": last["z"],
                                          "kind": "", "name": "", "file": os.path.basename(p)})
                            print("  %s: sent elsewhere while on the wire at (%d, %d): marked as a "
                                  "fall, the rest is walking until the next /aptrail command"
                                  % (os.path.basename(p), last["x"], last["y"]))
                            manual = here["k"] = "walk"
                        fall_top = None
                    elif manual == "wire" and last:
                        on_wire += 1
                        # Fell off the wire: from where you land you are walking again (nobody types
                        # /aptrail walk mid-fall), so the way back is drawn as the lower floor it
                        # really is, not as more wire. The fall itself stays a drop.
                        if here["m"] == 3:
                            if fall_top is None:
                                fall_top = last["z"]
                        else:
                            if fall_top is not None and fall_top - here["z"] >= DROP_MIN:
                                print("  %s: fell off the wire at (%d, %d), %d m down: walking from "
                                      "there until the next /aptrail command"
                                      % (os.path.basename(p), here["x"], here["y"],
                                         round((fall_top - here["z"]) / UNITS_PER_M)))
                                manual = here["k"] = "walk"
                            fall_top = None
                    cur.append(here)
                    lap.append(here)
                    if route:
                        route["parts"][-1].append(here)
                        route["samples"] += 1
                    last = here
        if len(cur) > 1:
            segs.append(cur)
        if route:
            print("  %s: route to %s never finished (no /aptrail arrived), not drawn"
                  % (os.path.basename(p), route["artifact"]))
    return segs, marks, heads, routes, rooms


def lap_area(ring):
    """Area (world units^2) inside a closed lap, by the shoelace formula."""
    return abs(sum(p["x"] * q["y"] - q["x"] * p["y"]
                   for p, q in zip(ring, ring[1:] + ring[:1]))) / 2


def room_lap(lap, start, end):
    """The edge of a room for /aptrail fillroom: the points from `start` (an /aptrail room) to now,
    or, without one, back to the last time the trail passed within ROOM_CLOSE of where fillroom
    was said - looking only beyond the first ROOM_MIN_LAP of walking, so the steps just taken do
    not count as a lap. None when there is no lap to close."""
    if start is not None:
        ring = lap[start:] + [end]
        return ring if len(ring) >= 4 else None
    walked = 0.0
    for i in range(len(lap) - 1, 0, -1):
        walked += math.dist((lap[i]["x"], lap[i]["y"]), (lap[i - 1]["x"], lap[i - 1]["y"]))
        if walked >= ROOM_MIN_LAP and math.dist((lap[i - 1]["x"], lap[i - 1]["y"]),
                                                (end["x"], end["y"])) <= ROOM_CLOSE:
            return lap[i - 1:] + [end]
    return None


def classify(seq):
    """Kind of each step seq[i] -> seq[i+1]: path / water / drop / climb.

    Swimming (mode 4) and climbing come straight from the recording. A drop is a run of falling
    (mode 3; for recordings without modes, a fast steep loss of height) that loses at least
    DROP_MIN - so the little fall of every jump stays a path."""
    n = len(seq) - 1
    kinds = ["path"] * n
    falling = [False] * n
    for i in range(n):
        a, b = seq[i], seq[i + 1]
        man = MANUAL_KIND.get(a.get("k")) if a.get("k") else None
        if man and man != "wire":                     # /aptrail swim | fly: the recorder said so
            kinds[i] = man
        elif a["c"] or b["c"]:
            kinds[i] = "climb"
        elif a.get("k") is None and (a["m"] == 4 or b["m"] == 4):   # automatic only without commands
            kinds[i] = "water"
        else:
            if man == "wire":                         # still a fall if you come off it
                kinds[i] = "wire"
            dz = b["z"] - a["z"]
            if b["m"] is not None:
                falling[i] = b["m"] == 3 and dz < 0
            else:
                falling[i] = dz < -150 and math.hypot(b["x"] - a["x"], b["y"] - a["y"]) < 2 * -dz
    i = 0
    while i < n:
        if falling[i] and kinds[i] in ("path", "wire"):
            j = i
            while j < n and falling[j] and kinds[j] in ("path", "wire"):
                j += 1
            # the step that lands is still part of the fall (the last sample in the air can be
            # metres above the floor), so the drop's height is floor to floor
            if j < n and kinds[j] in ("path", "wire") and seq[j + 1]["z"] < seq[j]["z"]:
                j += 1
            if seq[i]["z"] - seq[j]["z"] >= DROP_MIN:
                for k in range(i, j):
                    kinds[k] = "drop"
            i = j
        else:
            i += 1
    return kinds


def draw_wire(draw, line, width):
    """A thin wire as a yellow-and-black striped line, so it never reads as a normal path."""
    if len(line) < 2:
        return
    draw.line(line, fill=(0, 0, 0), width=width + 4, joint="curve")
    dash, on, left = max(6.0, width * 1.6), True, max(6.0, width * 1.6)
    for a, b in zip(line, line[1:]):
        seg = math.dist(a, b)
        pos = 0.0
        while seg - pos > 1e-6:
            step = min(left, seg - pos)
            if on:
                t0, t1 = pos / seg, (pos + step) / seg
                draw.line([(a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                           (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)],
                          fill=KIND_COLOUR["wire"], width=width)
            pos += step
            left -= step
            if left <= 1e-6:
                on, left = not on, dash


def lined(run):
    """The bits of a run that keep their pathway line (erase_line takes it off, floor stays)."""
    out, cur = [], []
    for p in run:
        if p.get("noline"):
            if len(cur) > 1:
                out.append(cur)
            cur = []
        else:
            cur.append(p)
    if len(cur) > 1:
        out.append(cur)
    return out


def runs(seq, kinds):
    """Split seq into [(kind, [pt, ...])] stretches of one kind (shared end points)."""
    out = []
    for i, k in enumerate(kinds):
        if out and out[-1][0] == k:
            out[-1][1].append(seq[i + 1])
        else:
            out.append((k, [seq[i], seq[i + 1]]))
    return out


# ---------------------------------------------------------------------------------- drawing ----
def font(size, bold=False):
    for nm in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(nm, size)
        except OSError:
            pass
    return ImageFont.load_default()


def floor_colour(t):
    """Colour of the floor at relative height t (0 = lowest, 1 = highest)."""
    t = max(0.0, min(1.0, t)) * (len(FLOOR_RAMP) - 1)
    i = min(int(t), len(FLOOR_RAMP) - 2)
    return lerp(FLOOR_RAMP[i], FLOOR_RAMP[i + 1], t - i)


def lerp(c1, c2, f):
    f = max(0.0, min(1.0, f))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * f) for i in range(3))


def find_icon(*names):
    """First existing image among the art folders, matched case-insensitively."""
    dirs = [os.path.join(ROOT, "tools", "reference", "poptracker_art", "engrams"),
            os.path.join(ROOT, "build", "poptracker_pack", "images", "engrams"),
            os.path.join(ROOT, "build", "poptracker_pack", "images", "crates")]
    try:
        base = json.load(open(os.path.join(ROOT, "tools", "reference", "poptracker_maps.json"),
                              encoding="utf-8")).get("_base_pack")
        if base:
            dirs += [os.path.join(base, "images", "engrams"), os.path.join(base, "images", "crates")]
    except (OSError, ValueError):
        pass
    want = {n.lower() for n in names}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if os.path.splitext(f)[0].lower() in want:
                return os.path.join(d, f)
    return None


def paste_icon(canvas, path, u, v, size):
    im = Image.open(path).convert("RGBA")
    im.thumbnail((size, size), Image.LANCZOS)
    canvas.paste(im, (int(u - im.width / 2), int(v - im.height / 2)), im)


def text(draw, xy, s, f, fill=(255, 255, 255), anchor=None):
    draw.text(xy, s, fill=fill, font=f, stroke_width=max(2, f.size // 9), stroke_fill=(0, 0, 0),
              anchor=anchor)


def poly_regular(cx, cy, r, sides, rot=0.0):
    return [(cx + r * math.cos(rot + 2 * math.pi * i / sides),
             cy + r * math.sin(rot + 2 * math.pi * i / sides)) for i in range(sides)]


def draw_symbol(draw, kind, u, v, r):
    """Mark symbols; the legend uses the same function so the two always match."""
    if kind == "button":
        draw.polygon(poly_regular(u, v, r, 6, math.pi / 6), fill=(230, 30, 30), outline=(0, 0, 0))
    elif kind == "climb":
        draw.polygon([(u, v - r), (u + r, v + r * 0.8), (u - r, v + r * 0.8)], fill=KIND_COLOUR["climb"],
                     outline=(0, 0, 0))
    elif kind == "drop":
        draw.polygon([(u - r, v - r * 0.8), (u + r, v - r * 0.8), (u, v + r)], fill=KIND_COLOUR["drop"],
                     outline=(0, 0, 0))
    elif kind == "water":
        draw.ellipse((u - r, v - r, u + r, v + r), fill=KIND_COLOUR["water"], outline=(0, 0, 0))
    elif kind == "danger":
        draw.polygon([(u, v - r), (u + r, v + r * 0.8), (u - r, v + r * 0.8)], fill=(255, 220, 0),
                     outline=(0, 0, 0))
        draw.line([(u, v - r * 0.35), (u, v + r * 0.25)], fill=(0, 0, 0), width=max(2, int(r / 4)))
    else:                                               # note
        draw.ellipse((u - r * 0.6, v - r * 0.6, u + r * 0.6, v + r * 0.6), fill=(255, 255, 255),
                     outline=(0, 0, 0))


def draw_arrow(draw, tail, head, width, colour=ARROW):
    """A fat arrow from tail to head (the head touches the entrance)."""
    dx, dy = head[0] - tail[0], head[1] - tail[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    hw, hl = width * 1.6, width * 2.4
    bx, by = head[0] - ux * hl, head[1] - uy * hl
    shaft = [(tail[0] + nx * width / 2, tail[1] + ny * width / 2),
             (bx + nx * width / 2, by + ny * width / 2), (bx + nx * hw, by + ny * hw), head,
             (bx - nx * hw, by - ny * hw), (bx - nx * width / 2, by - ny * width / 2),
             (tail[0] - nx * width / 2, tail[1] - ny * width / 2)]
    draw.polygon(shaft, fill=colour, outline=(0, 0, 0))


def nice_metres(target):
    for m in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if m >= target:
            return m
    return 1000


def fit_anchors(pairs):
    """[(world (x, y), pixel (u, v))] -> (scale, ox, oy) minimising squared pixel error."""
    n = len(pairs)
    mx = sum(w[0] for w, _ in pairs) / n
    my = sum(w[1] for w, _ in pairs) / n
    mu = sum(q[0] for _, q in pairs) / n
    mv = sum(q[1] for _, q in pairs) / n
    num = sum((w[0] - mx) * (q[0] - mu) + (w[1] - my) * (q[1] - mv) for w, q in pairs)
    den = sum((w[0] - mx) ** 2 + (w[1] - my) ** 2 for w, _ in pairs)
    if den == 0:
        sys.exit("anchors are all at the same world spot - need two that are apart")
    s = num / den
    return s, mu - s * mx, mv - s * my


# ------------------------------------------------------------------------------------ edits ----
# Hand edits made in tools/cave_map_edit.py live in <trails dir>/<cave>.edits.json, in WORLD units,
# so they survive re-renders and new recordings, and the recordings themselves are never changed:
#   {"cave": "Jungle Dungeon", "edits": [
#     {"op": "erase" | "erase_line" | "lava" | "water" | "floor", "shape": "brush", "width": 400, "pts": [[x, y], ...]},
#     {"op": "erase" | "lava" | "water" | "floor", "shape": "poly", "pts": [[x, y], ...]},
#     {"op": "hide_mark", "label": "...", "x": 0, "y": 0},
#     {"op": "add_mark", "label": "Red Cave Crate", "x": 0, "y": 0},
#     {"op": "route", "artifact": "Hunter", "from": "Flyer Entrance", "pts": [[x, y], ...]}]}
# A top-level "where": "Irradiated zone, Aberrant region" is printed after the map name under the
# title (the editor's "Where" box).
# A top-level "swim_is": "lava" draws everything swum through as LAVA instead of water (a cave
# whose "water" is lava you cross on a mount, like Valguero's Great Trench). Set by the editor's
# "Swim = lava" checkbox.
# erase removes recorded trail points (the line breaks there); erase_line only takes the pathway
# line off there (the floor stays); lava/water/floor paint the map (lava/water cover walked floor
# too; strokes apply in order, so a later floor stroke shows over an earlier lava one);
# add_mark places a mark (a crate, a note) like /aptrail mark would; route is a pathway drawn by
# hand (for a route that was never recorded right) - drawn like a recorded one, no floor under it.
# A route with "join": true bridges a gap in the recorded route of the same artifact + entrance
# (e.g. a grappling hook swing recorded while paused): same line, no extra legend row.
PAINT_OPS = ("lava", "water", "floor")
SWIM_IS_LAVA = False          # set by load_edits from the edits file's "swim_is"
CAVE_WHERE = ""               # set by load_edits from the edits file's "where" (shown under the map name)
FLY_GROUND_SNAP = None        # world units, or None: a flight this close to recorded ground is
                              # floored at that ground's height (None = always the nearest ground,
                              # the user's rule 2026-11-18: "closest thing that was captured")


def slug(s):
    """Same as the plugin's TrailSlug: lower-case letters/digits, anything else one underscore."""
    out = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return out or "cave"


def default_edits_path(paths, heads):
    cave = next((h.get("cave") for h in heads if h.get("cave")), None)
    if not cave or not paths:
        return None
    return os.path.join(os.path.dirname(os.path.abspath(paths[0])), slug(cave) + ".edits.json")


def load_edits(path):
    """-> list of well-formed edits (anything malformed is skipped)."""
    global SWIM_IS_LAVA, CAVE_WHERE
    SWIM_IS_LAVA, CAVE_WHERE = False, ""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except ValueError as e:
        print("  %s: not valid JSON (%s) - edits ignored" % (path, e))
        return []
    good = []
    for e in (raw.get("edits") if isinstance(raw, dict) else None) or []:
        if not isinstance(e, dict):
            continue
        op = e.get("op")
        xy = isinstance(e.get("x"), (int, float)) and isinstance(e.get("y"), (int, float))
        if op == "hide_mark" and xy:
            good.append(e)
        elif op == "add_mark" and xy and isinstance(e.get("label"), str) and e["label"].strip():
            good.append(dict(e, label=e["label"].strip()))
        elif op == "route":
            pts = [q for q in e.get("pts") or [] if isinstance(q, list) and len(q) == 2
                   and all(isinstance(c, (int, float)) for c in q)]
            if len(pts) >= 2 and isinstance(e.get("artifact"), str) and e["artifact"].strip():
                good.append(dict(e, artifact=e["artifact"].strip(), pts=[tuple(q) for q in pts],
                                 **{"from": str(e.get("from") or "").strip()}))
        elif op in PAINT_OPS + ("erase", "erase_line") and e.get("shape") in ("brush", "poly"):
            pts = [q for q in e.get("pts") or [] if isinstance(q, list) and len(q) == 2
                   and all(isinstance(c, (int, float)) for c in q)]
            need = 1 if e["shape"] == "brush" else 3
            if len(pts) >= need and (e["shape"] == "poly" or isinstance(e.get("width"), (int, float))):
                good.append(dict(e, pts=[tuple(q) for q in pts]))
    known = ("hide_mark", "add_mark", "route", "erase", "erase_line") + PAINT_OPS
    unknown = sorted({str(e.get("op")) for e in (raw.get("edits") if isinstance(raw, dict) else None) or []
                      if isinstance(e, dict) and e.get("op") not in known})
    if unknown:
        print("  WARNING edits of a kind this renderer does not know were ignored: %s "
              "(restart tools/cave_map_edit.py if it was running before an update)" % ", ".join(unknown))
    skipped = len((raw.get("edits") if isinstance(raw, dict) else None) or []) - len(good)
    if skipped and not unknown:
        print("  WARNING %d malformed edit(s) ignored" % skipped)
    print("  edits: %d from %s" % (len(good), os.path.basename(path)))
    SWIM_IS_LAVA = isinstance(raw, dict) and raw.get("swim_is") == "lava"
    if isinstance(raw, dict) and isinstance(raw.get("where"), str):
        CAVE_WHERE = raw["where"].strip()
    if SWIM_IS_LAVA:
        print("  swim_is lava: everything swum through is drawn as lava")
    return good


def _in_poly(x, y, poly):
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _near_line(x, y, line, r):
    if len(line) == 1:
        return math.dist((x, y), line[0]) <= r
    for (x1, y1), (x2, y2) in zip(line, line[1:]):
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if not L2 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
        if math.dist((x, y), (x1 + t * dx, y1 + t * dy)) <= r:
            return True
    return False


def in_shape(x, y, e):
    if e["shape"] == "poly":
        return _in_poly(x, y, e["pts"])
    return _near_line(x, y, e["pts"], e["width"] / 2)


def shape_extent(e):
    """World points that bound a painted shape (brush strokes widened by their radius)."""
    r = e.get("width", 0) / 2 if e["shape"] == "brush" else 0
    return [(x + dx, y + dy) for x, y in e["pts"] for dx, dy in ((-r, -r), (r, r))]


def nearest_z(x, y, segs, marks):
    """Height of the nearest recorded point (a hand-placed thing has no height of its own)."""
    best, z = None, 0.0
    for p in [p for sg in segs for p in sg] + marks:
        d = (p["x"] - x) ** 2 + (p["y"] - y) ** 2
        if best is None or d < best:
            best, z = d, p["z"]
    return z


def _dist_to_line(x, y, line):
    best = float("inf")
    for a, b in zip(line, line[1:]):
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        L2 = dx * dx + dy * dy
        t = 0.0 if not L2 else max(0.0, min(1.0, ((x - a["x"]) * dx + (y - a["y"]) * dy) / L2))
        best = min(best, math.dist((x, y), (a["x"] + t * dx, a["y"] + t * dy)))
    return best


WIRE_SNAP_END = 600       # a drawn pathway within 6 m of both ends of a recorded wire...
WIRE_SNAP_ALONG = 800     # ...and never more than 8 m off it in between, crosses on that wire


def wire_on_drawn(part, segs):
    """Mark the stretch of a hand-drawn pathway that goes along a recorded thin wire as wire, so it
    is drawn striped like one (a pathway drawn over it would hide the wire). Only a stretch that
    runs from one end of the wire to the other counts - crossing a wire is not taking it."""
    wires = []
    for sg in segs:
        cur = []
        for p in sg:
            if p.get("k") == "wire":
                cur.append(p)
            elif cur:
                wires.append(cur)
                cur = []
        if cur:
            wires.append(cur)
    for w in wires:
        if len(w) < 2:
            continue
        a, b = w[0], w[-1]
        near_a = [i for i, q in enumerate(part) if math.dist((q["x"], q["y"]), (a["x"], a["y"])) <= WIRE_SNAP_END]
        near_b = [i for i, q in enumerate(part) if math.dist((q["x"], q["y"]), (b["x"], b["y"])) <= WIRE_SNAP_END]
        best = None
        for i in near_a:
            for j in near_b:
                lo, hi = min(i, j), max(i, j)
                if hi - lo < 1 or (best and hi - lo >= best[1] - best[0]):
                    continue
                if all(_dist_to_line(q["x"], q["y"], w) <= WIRE_SNAP_ALONG for q in part[lo:hi + 1]):
                    best = (lo, hi)
        if best:
            for q in part[best[0]:best[1]]:
                q["k"] = "wire"
            print("  edits: drawn pathway goes along the thin wire (%d m) - drawn as wire there"
                  % round(math.dist((a["x"], a["y"]), (b["x"], b["y"])) / UNITS_PER_M))


def apply_edits(edits, segs, routes, marks):
    """Erase trail points, add drawn routes and placed marks, hide marks. Walks and route parts
    break where points were erased (drawn routes and placed marks are not erased)."""
    erases = [e for e in edits if e["op"] == "erase"]
    hides = [e for e in edits if e["op"] == "hide_mark"]
    unlines = [e for e in edits if e["op"] == "erase_line"]
    if unlines:
        boxes = []
        for e in unlines:
            xs, ys = zip(*shape_extent(e))
            boxes.append((min(xs), min(ys), max(xs), max(ys), e))
        n = 0
        for p in [p for sg in segs for p in sg] + [p for r in routes for q in r["parts"] for p in q]:
            if any(b[0] <= p["x"] <= b[2] and b[1] <= p["y"] <= b[3] and in_shape(p["x"], p["y"], b[4])
                   for b in boxes):
                p["noline"] = True
                n += 1
        print("  edits: pathway line taken off %d recorded points (floor kept)" % n)
    if erases:
        boxes = []
        for e in erases:
            xs, ys = zip(*shape_extent(e))
            boxes.append((min(xs), min(ys), max(xs), max(ys), e))

        def gone(p):
            return any(b[0] <= p["x"] <= b[2] and b[1] <= p["y"] <= b[3] and in_shape(p["x"], p["y"], b[4])
                       for b in boxes)

        def split(seq):
            out, cur = [], []
            for p in seq:
                if gone(p):
                    if len(cur) > 1:
                        out.append(cur)
                    cur = []
                else:
                    cur.append(p)
            if len(cur) > 1:
                out.append(cur)
            return out
        n0 = sum(len(sg) for sg in segs)
        segs[:] = [q for sg in segs for q in split(sg)]
        for r in routes:
            r["parts"] = [q for part in r["parts"] for q in split(part)]
        print("  edits: erased %d recorded points" % (n0 - sum(len(sg) for sg in segs)))
    walked_marks = list(marks)
    for e in edits:
        if e["op"] == "add_mark":
            marks.append({"label": e["label"], "x": e["x"], "y": e["y"],
                          "z": nearest_z(e["x"], e["y"], segs, walked_marks),
                          "kind": "", "name": "", "file": "hand edit"})
        elif e["op"] == "route":
            # m=1 (walking) on every point: a drawn line has no falls or swims to guess at
            part = [{"x": x, "y": y, "z": nearest_z(x, y, segs, walked_marks), "m": 1, "c": False,
                     "k": "walk"} for x, y in e["pts"]]
            wire_on_drawn(part, segs)
            # "join": true = a gap in a RECORDED route (a grappling hook swing recorded while paused)
            # bridged by hand: added to that route's line, no new legend row, distance unchanged
            host = e.get("join") and next((r for r in routes if not r.get("drawn")
                                           and r["artifact"].lower() == e["artifact"].lower()
                                           and r["from"].lower() == e["from"].lower()), None)
            if host:
                for q in part:
                    q["join"] = True            # a swing through the air: no floor under it
                host["parts"].append(part)
                continue
            if e.get("join"):
                print("  edits: join route %s from %s - no recorded route like that, drawn on its own"
                      % (e["artifact"], e["from"] or "?"))
            routes.append({"artifact": e["artifact"], "from": e["from"], "parts": [part],
                           "file": "hand edit", "drawn": True,
                           "distance": sum(math.dist((a["x"], a["y"]), (b["x"], b["y"]))
                                           for a, b in zip(part, part[1:]))})
    added = sum(1 for e in edits if e["op"] in ("add_mark", "route"))
    if added:
        print("  edits: %d placed mark(s), %d drawn route(s)"
              % (sum(1 for e in edits if e["op"] == "add_mark"), sum(1 for e in edits if e["op"] == "route")))
    if hides:
        keep = [m for m in marks if not any(h.get("label", m["label"]) == m["label"] and
                                            math.dist((h["x"], h["y"]), (m["x"], m["y"])) <= 100
                                            for h in hides)]
        print("  edits: hid %d mark(s)" % (len(marks) - len(keep)))
        marks[:] = keep


def gps_of(heads):
    """-> f(point) = (lat, lon) as the in-game GPS shows it, or None.

    From the GPS constants the plugin puts in each recording's start line (APrimalWorldSettings):
    lat = (y - lat_origin) / lat_scale, lon likewise with x. The plugin reads the scales 10x low
    (Ragnarok 1310 for 13100, Valguero 816.1 for 8161, The Center 958.4/960 for 9584/9600 - the
    x10 values are compass-verified on Valguero and The Center, see tools/import_drawn_regions.py
    KNOWN), so a scale under 3000 is multiplied by 10. No real map's scale is that small.
    Ragnarok verified 2026-11-16: the recorded artifacts come out at the wiki's GPS exactly (Jungle
    Dungeon Hunter 21.5/27.2, Monkey's Puzzle Strong 24.8/24.7, Frozen Dungeon Pack 33.6/43.4)."""
    g = next((h["geo"] for h in heads if isinstance(h.get("geo"), dict)), None)
    try:
        lat_o, lat_s = float(g["lat_origin"]), float(g["lat_scale"])
        lon_o, lon_s = float(g["lon_origin"]), float(g["lon_scale"])
    except (TypeError, KeyError, ValueError):
        return None
    if not lat_s or not lon_s:
        return None
    lat_s *= 10 if abs(lat_s) < 3000 else 1
    lon_s *= 10 if abs(lon_s) < 3000 else 1
    return lambda p: ((p["y"] - lat_o) / lat_s, (p["x"] - lon_o) / lon_s)


def mark_kind(m):
    """-> (drawer, text shown beside it, legend text)."""
    if m.get("kind") == "entrance" or m["label"].lower().startswith("entrance "):
        return "entrance", m.get("name") or m["label"][len("entrance "):], "Entrance"
    k = ART_RE.match(m["label"])
    if k:
        return "artifact", "Artifact of the %s" % k.group(1).title(), None
    words = m["label"].split()
    first = words[0].lower() if words else ""
    low = [w.lower().strip("().,") for w in words]
    # "Red Cave Drop", "blue crate": a colour plus "cave drop" / "crate" anywhere is a crate too
    if (any(c in low for c in CRATE_COLOURS) and
            ("crate" in low or "crates" in low or "cave drop" in " ".join(low))):
        rest = [w for w, l in zip(words, low) if l not in ("cave", "drop", "crate", "crates")]
        return "crate", " ".join(rest), MARK_KINDS["crate"][0]
    if first in MARK_KINDS:
        legend, drawer = MARK_KINDS[first]
        # "drop into the pit": the keyword is part of the sentence, so it stays in the label
        keep = len(low) > 1 and low[1] in ("into", "to", "onto", "down", "through", "off", "from",
                                           "in", "here", "over")
        return drawer, " ".join(words if keep else words[1:]), legend
    return "note", m["label"], "Note"


# ------------------------------------------------------------------------------------- main ----
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("trails", nargs="+", help="trail .jsonl files (globs ok)")
    ap.add_argument("--out", required=True, help="output .png (a .json goes beside it)")
    ap.add_argument("--title", help="map title (default: the cave name from the recording)")
    ap.add_argument("--size", type=int, default=2000, help="map area, longest side in px")
    ap.add_argument("--pad", type=int, default=80, help="border around the map in px")
    ap.add_argument("--corridor", type=float, default=500,
                    help="width of the drawn cave floor in world units (default 500 = 5 m)")
    ap.add_argument("--base", help="draw on this image instead of a blank canvas")
    ap.add_argument("--anchor", action="append", default=[],
                    help='"<mark label>=<px>,<py>" ties a mark to a pixel of --base (repeatable)')
    ap.add_argument("--pins", metavar="MAP", help="anchor 'artifact <Name>' marks to the _cave_pins "
                    "of this map in poptracker_logic.json")
    ap.add_argument("--jump", type=float, default=2500,
                    help="world units between two samples that count as a teleport (default 2500)")
    ap.add_argument("--routes-only", action="store_true",
                    help="floor and lines only from the routes, not from every other walk")
    ap.add_argument("--route-colours", choices=("type", "entrance"), default="type",
                    help="colour routes by what you do (default) or one colour per entrance")
    ap.add_argument("--no-legend", action="store_true", help="map only: no title/legend panel")
    ap.add_argument("--no-modes", action="store_true",
                    help="ignore the recorded movement modes (flying on a mount records as SWIM, "
                         "so a flown cave turns all water): floor shaded by height, drops guessed")
    ap.add_argument("--text-scale", type=float, default=1.0,
                    help="multiply every text size (labels, legend, title), e.g. 0.8 smaller, 1.3 bigger")
    ap.add_argument("--edits", help="hand edits file (default <trails dir>/<cave>.edits.json, made "
                                     "with tools/cave_map_edit.py)")
    ap.add_argument("--no-edits", action="store_true", help="ignore the hand edits file")
    args = ap.parse_args(argv)

    paths = sorted({p for g in args.trails for p in (glob.glob(g) or [g])})
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        sys.exit("no such file: %s" % ", ".join(missing))
    segs, marks, heads, routes, rooms = load_trails(paths, args.jump)
    edits_path = None if args.no_edits else (args.edits or default_edits_path(paths, heads))
    edits = load_edits(edits_path)
    SWIM_FLOOR = LAVA_FLOOR if SWIM_IS_LAVA else WATER_FLOOR
    apply_edits(edits, segs, routes, marks)
    paints = [e for e in edits if e["op"] in PAINT_OPS]
    if args.no_modes:
        for p in [p for sg in segs for p in sg] + [p for r in routes for q in r["parts"] for p in q]:
            p["m"] = None
    # bounds and height range always come from EVERYTHING walked, so a --routes-only image has the
    # same scale and pin pixels as the full one
    pts = ([(p["x"], p["y"], p["z"]) for s in segs for p in s] +
           [(p["x"], p["y"], p["z"]) for r in routes for q in r["parts"] for p in q] +
           [(m["x"], m["y"], m["z"]) for m in marks])
    if not pts:
        sys.exit("no points in %s" % ", ".join(paths))
    if args.routes_only:
        if not routes:
            sys.exit("--routes-only, but no finished routes in these files")
        segs = []
    caves = sorted({h.get("cave", "?") for h in heads}) or ["?"]
    maps_ = sorted({h.get("map", "?") for h in heads}) or ["?"]
    gps = gps_of(heads)
    if not gps:
        print("  (no GPS constants in the recording's start line: entrances shown without coordinates)")
    print("%d file(s), cave %s on %s: %d walks, %d points, %d marks, %d routes"
          % (len(paths), " / ".join(caves), " / ".join(maps_), len(segs),
             sum(len(s) for s in segs), len(marks), len(routes)))
    if len(caves) > 1:
        print("  WARNING these files name different caves - merging them anyway")
    has_modes = any(p["m"] is not None for s in segs + [q for r in routes for q in r["parts"]]
                    for p in s)
    if not has_modes:
        print("  (recorded before v211: no movement modes, so swims/climbs are not coloured and "
              "drops are guessed from height)")

    # ---- world -> pixel
    anchors = []
    for a in args.anchor:
        label, _, px_ = a.rpartition("=")
        u, v = (float(t) for t in px_.split(","))
        anchors.append((label.strip(), (u, v)))
    if args.pins:
        cp = json.load(open(LOGIC, encoding="utf-8")).get(args.pins, {}) \
                 .get("artifacts", {}).get("_cave_pins", {})
        for m in marks:
            k = ART_RE.match(m["label"])
            if k and k.group(1).title() in cp:
                anchors.append((m["label"], tuple(cp[k.group(1).title()])))
    panel = 0
    if anchors:
        if not args.base:
            sys.exit("--anchor/--pins place the trail on an image: pass --base too")
        by_label = {}
        for m in marks:
            by_label.setdefault(m["label"].strip().lower(), []).append(m)
        pairs, used = [], []
        for label, q in anchors:
            ms = by_label.get(label.lower())
            if not ms:
                print("  anchor %r: no mark with that label, ignored" % label)
                continue
            w = (sum(m["x"] for m in ms) / len(ms), sum(m["y"] for m in ms) / len(ms))
            pairs.append((w, q))
            used.append(label)
        if len(pairs) < 2:
            sys.exit("need 2+ anchors that match marks, got %d" % len(pairs))
        s, ox, oy = fit_anchors(pairs)
        for label, (w, q) in zip(used, pairs):
            print("  anchor %-28s error %.1f px" % (label, math.dist((s * w[0] + ox, s * w[1] + oy), q)))
        canvas = Image.open(args.base).convert("RGB")
    else:
        bxy = [(p[0], p[1]) for p in pts] + [q for e in paints for q in shape_extent(e)]
        x0 = min(p[0] for p in bxy); x1 = max(p[0] for p in bxy)
        y0 = min(p[1] for p in bxy); y1 = max(p[1] for p in bxy)
        span = max(x1 - x0, y1 - y0, 1.0)
        # entrance arrows and labels stick out past the walked area: keep them on the canvas
        args.pad = max(args.pad, int(max(28, args.size // 30) * 3.4))
        s = (args.size - 2 * args.pad) / span
        panel = 0 if args.no_legend else int(args.size * 0.3)
        w_px = int((x1 - x0) * s) + 2 * args.pad + panel
        h_px = max(int((y1 - y0) * s) + 2 * args.pad, int(args.size * 0.55))
        ox = panel + args.pad - s * x0
        oy = (h_px - (y1 - y0) * s) / 2 - s * y0
        canvas = Image.new("RGB", (w_px, h_px), BG)

    def px(p):
        return (s * p["x"] + ox, s * p["y"] + oy)

    big = max(canvas.size)
    z0 = min(p[2] for p in pts)
    z1 = max(p[2] for p in pts)
    zr = (z1 - z0) or 1.0
    draw = ImageDraw.Draw(canvas)

    # ---- 1. cave floor: a wide band along every walk, lighter = higher, blue where swum
    # at least this wide on the picture, so a passage is visible - but on a big cave a fixed wide
    # minimum (it was big // 90, 35 m on Carnivorous Caverns) merges every passage into one blob
    fw = max(big // 200, int(args.corridor * s))
    walks = segs + [q for r in routes if not r.get("drawn") for q in r["parts"]   # drawn: no floor
                    if not all(p.get("join") for p in q)]       # nor under a joined swing
    walk_kinds = [classify(w) for w in walks]
    # the colour scale spans the FLOOR's heights (not flights, which would squash it), 1st to 99th
    # percentile so one odd sample does not either
    fz = sorted(w[i]["z"] for w, ks in zip(walks, walk_kinds) for i, k in enumerate(ks)
                if k not in ("fly", "wire") and not w[i].get("hide"))
    if fz:
        zf0, zf1 = fz[int(len(fz) * 0.01)], fz[min(len(fz) - 1, int(len(fz) * 0.99))]
    else:
        zf0, zf1 = z0, z1
    zr0, zr1 = zf0, zf1                         # the floor's real range, for the legend
    if zf1 - zf0 < FLOOR_MIN_SPAN:              # a nearly flat cave: bumps stay bumps
        mid = (zf0 + zf1) / 2
        zf0, zf1 = mid - FLOOR_MIN_SPAN / 2, mid + FLOOR_MIN_SPAN / 2
    zfr = zf1 - zf0

    def ht(z):
        return (z - zf0) / zfr

    lstep = max(zfr / FLOOR_LEVELS, FLOOR_LEVEL_MIN)

    def level(z):
        return max(0, min(FLOOR_LEVELS - 1, int((z - zf0) / lstep)))

    # A flight is not floor: the strip drawn under it takes the height of the nearest ground point
    # recorded in game (anything not flown or on a wire), so a flyer crossing a room 200 m up does
    # not paint a ledge at 200 m (Great Trench lower-left, 2026-11-18). With FLY_GROUND_SNAP set, a
    # flight with no ground that close keeps its own height; by default there is no limit.
    ground = [p for w, ks in zip(walks, walk_kinds) for i, p in enumerate(w)
              if (ks[min(i, len(ks) - 1)] if ks else "path") not in ("fly", "wire")]
    flown = [p for w, ks in zip(walks, walk_kinds) for i, p in enumerate(w)
             if ks and ks[min(i, len(ks) - 1)] == "fly"]
    for p in flown:
        p["fz"] = p["z"]
    if ground and flown:
        import numpy as np
        G = np.array([(p["x"], p["y"]) for p in ground], dtype=float)
        F = np.array([(p["x"], p["y"]) for p in flown], dtype=float)
        try:
            from scipy.spatial import cKDTree
            dist, idx = cKDTree(G).query(F)
        except ImportError:
            dist, idx = np.empty(len(F)), np.empty(len(F), dtype=int)
            for i in range(0, len(F), 2048):
                d = ((F[i:i + 2048, None, :] - G[None, :, :]) ** 2).sum(-1)
                idx[i:i + 2048] = d.argmin(1)
                dist[i:i + 2048] = np.sqrt(d.min(1))
        for p, d, j in zip(flown, dist, idx):
            if FLY_GROUND_SNAP is None or d <= FLY_GROUND_SNAP:
                p["fz"] = ground[int(j)]["z"]

    def fz(p):
        return p.get("fz", p["z"])

    def band(colour_of, width, only=None, target=None):
        dr = target or draw
        for w, ks in zip(walks, walk_kinds):
            last = None
            for i, k in enumerate(ks):
                if (w[i].get("hide") and w[i + 1].get("hide")) or k == "wire":
                    last = None                         # hazard-room outline lap / a thin wire:
                    continue                            # not drawn as the wide corridor
                if only is not None and level(fz(w[i + 1] if k == "drop" else w[i])) != only:
                    last = None                         # another height layer
                    continue
                a, b = px(w[i]), px(w[i + 1])
                if last and math.dist(last, b) < width / 4 and i < len(ks) - 1 and k != "drop":
                    continue                            # thin out dense samples for speed
                a = last or a
                # a drop is shaded at the height it lands on, so the floor below reads as lower
                c = colour_of(w[i + 1] if k == "drop" else w[i], k)
                dr.line([a, b], fill=c, width=width)
                r = width / 2
                dr.ellipse((b[0] - r, b[1] - r, b[0] + r, b[1] + r), fill=c)
                last = b
    # /aptrail lava: lava all round the safe ground walked, drawn first so every floor and edge
    # (this walk's and any other passage's) sits on top of it
    lava_w = fw + 2 * max(4, int(LAVA_AROUND * s))
    lava_seen = False
    for w in walks:
        for a, b in zip(w, w[1:]):
            if a.get("k") == "lava" and b.get("k") == "lava":
                lava_seen = True
                u, v = px(b)
                draw.line([px(a), (u, v)], fill=LAVA_FLOOR, width=lava_w)
                draw.ellipse((u - lava_w / 2, v - lava_w / 2, u + lava_w / 2, v + lava_w / 2),
                             fill=LAVA_FLOOR)
    def paint(e, colour, extra=0, target=None):
        dr = target or draw
        if e["shape"] == "poly":
            dr.polygon([(s * x + ox, s * y + oy) for x, y in e["pts"]], fill=colour,
                         outline=FLOOR_EDGE if extra else colour, width=max(1, extra))
            return
        w = max(2, int(e["width"] * s) + extra)
        line = [(s * x + ox, s * y + oy) for x, y in e["pts"]]
        if len(line) > 1:
            dr.line(line, fill=colour, width=w, joint="curve")
        for u, v in line:
            dr.ellipse((u - w / 2, v - w / 2, u + w / 2, v + w / 2), fill=colour)

    # hand-painted lava / water (cave_map_edit.py): under everything walked, like /aptrail lava
    for e in paints:
        if e["op"] in ("lava", "water"):
            paint(e, LAVA_FLOOR if e["op"] == "lava" else WATER_FLOOR)
    floor_paint = [e for e in paints if e["op"] == "floor"]
    # Painted floor: EVERY painted pixel takes the height of the nearest point recorded in game
    # (user, 2026-11-18: "the color of that height should be dependent on the closest thing that
    # was captured"), never a flight or a wire (a flyer crossing above a room is not its floor). So
    # a painted room shades exactly like the walked floor round it, in the same height layers. It
    # used to be one colour per stroke, from one point near the stroke's middle - a stroke over two
    # heights came out the wrong colour on one side (Great Trench lower-left, purple next to tan).
    walked = [p for w, ks in zip(walks, walk_kinds) for i, p in enumerate(w)
              if (ks[min(i, len(ks) - 1)] if ks else "path") not in ("fly", "wire") and not p.get("hide")]
    walked = walked or [p for w in walks for p in w]
    paint_rgb, paint_lv, paint_z = None, None, None
    if floor_paint and walked:
        import numpy as np
        pm = Image.new("L", canvas.size, 0)
        pmd = ImageDraw.Draw(pm)
        for e in floor_paint:
            paint(e, 255, target=pmd)
        ys, xs = np.nonzero(np.asarray(pm) > 0)
        P = np.array([px(p) for p in walked], dtype=float)
        Z = np.array([p["z"] for p in walked], dtype=float)
        Q = np.column_stack([xs, ys]).astype(float)
        try:
            from scipy.spatial import cKDTree
            idx = cKDTree(P).query(Q)[1]
        except ImportError:                     # no scipy: brute force in chunks
            idx = np.empty(len(Q), dtype=int)
            for i in range(0, len(Q), 4096):
                d = ((Q[i:i + 4096, None, :] - P[None, :, :]) ** 2).sum(-1)
                idx[i:i + 4096] = d.argmin(1)
        hz = np.clip((Z[idx] - zf0) / zfr, 0.0, 1.0)
        lut = np.array([floor_colour(i / 255) for i in range(256)], dtype=np.uint8)
        rgb = np.zeros((canvas.size[1], canvas.size[0], 3), dtype=np.uint8)
        rgb[ys, xs] = lut[np.rint(hz * 255).astype(int)]
        lv = np.full((canvas.size[1], canvas.size[0]), 255, dtype=np.uint8)
        lv[ys, xs] = np.clip(((Z[idx] - zf0) / lstep).astype(int), 0, FLOOR_LEVELS - 1)
        paint_rgb, paint_lv = Image.fromarray(rgb, "RGB"), lv
        paint_z = np.full((canvas.size[1], canvas.size[0]), np.nan, dtype=np.float32)
        paint_z[ys, xs] = Z[idx]

    def paint_mask(L):
        if paint_lv is None or not (paint_lv == L).any():
            return None
        return Image.fromarray(((paint_lv == L) * 255).astype("uint8"), "L")
    room_z = [sum(q["z"] for q in r["pts"]) / len(r["pts"]) for r in rooms]
    edge_w = fw + max(4, fw // 5)
    # Lowest height layer first, each one edge then floor: where a higher passage runs over a lower
    # one (or a slope climbs into the next layer) its dark edge lies on the lower floor - a rim
    # that shows which side is up. Within one layer there are no edges, so it reads as one floor.
    # /aptrail fillroom: the inside of a lap walked round a room is floor too, at the lap's mean
    # height (water when most of it was swum), in its layer between the edge and the floor pass, so
    # the room and the passages through it read as one floor.
    # /aptrail fillroom lava | water: the whole room is that hazard; what you walk later is floor.
    # A room you walked back and forth across is many passes side by side; drawn as they are they
    # read as a comb of separate passages. So each layer's floor is drawn into its own image and
    # "closed" (grown by close_px, then shrunk back): gaps narrower than ~2 x close_px between
    # passes fill in, while passages further apart stay apart and nothing gets wider.
    # (capped: on a small cave the corridor is already wider than the gaps, and each px costs a pass)
    close_px = max(1, min(int(ROOM_GAP_FILL * s / 2), fw, 16))
    ring_px = max(2, min((edge_w - fw) // 2, 16))
    W_, H_ = canvas.size

    def nearest_fill(colour, mask):
        """colour where mask is set; everywhere else the colour of the nearest masked pixel."""
        try:
            import numpy as np
            from scipy.ndimage import distance_transform_edt
        except ImportError:
            return Image.composite(colour, grow(colour, close_px), mask)
        m = np.asarray(mask) > 0
        if not m.any():
            return colour
        iy, ix = distance_transform_edt(~m, return_distances=False, return_indices=True)
        return Image.fromarray(np.asarray(colour)[iy, ix], "RGB")

    def grow(im, n, shrink=False):
        # Round: a mask grows / shrinks by a DISC of radius n (distance transform). Repeating a 3x3
        # Max/MinFilter grows by a SQUARE, which left stair-step edges and square holes in the
        # floor (Boreal Forest, 2026-11-19). The filter is only the fallback (no scipy, RGB image).
        if im.mode == "L" and n > 0:
            try:
                import numpy as np
                from scipy.ndimage import distance_transform_edt
                a = np.asarray(im) > 127
                if shrink:
                    out = distance_transform_edt(a) > n
                else:
                    out = (distance_transform_edt(~a) <= n) if a.any() else a
                return Image.fromarray(out.astype("uint8") * 255, "L")
            except ImportError:
                pass
        f = ImageFilter.MinFilter(3) if shrink else ImageFilter.MaxFilter(3)
        for _ in range(n):
            im = im.filter(f)
        return im

    # The dark rim round a height layer is only drawn where it is a real edge: against the
    # background, or over floor more than FLOOR_LEDGE lower (a passage over another one, a drop).
    # Where a slope just runs on from one layer into the next it is the same floor, and a rim there
    # cut every sloped passage into bands with dark lines across it, plus a little ring round any
    # single sample in the next layer (Boreal Forest, 2026-11-19: "why does it look choppy").
    # zmap = height of the floor drawn so far, per pixel (NaN = nothing yet).
    try:
        import numpy as np
        from scipy.ndimage import distance_transform_edt as _edt
        zmap = np.full((H_, W_), np.nan, dtype=np.float32)
        wetmap = np.zeros((H_, W_), dtype=bool)     # floor drawn so far that is water / lava
    except ImportError:
        zmap = None

    def nearest_z(za, mask):
        """za's value where mask is set; elsewhere the value at the nearest masked pixel."""
        m = np.asarray(mask) > 0
        if not m.any():
            return za.astype(np.float32)
        iy, ix = _edt(~m, return_distances=False, return_indices=True)
        return za[iy, ix].astype(np.float32)

    def ledge_only(edge, zthis, box, wthis):
        """edge mask kept only on background, between floor and water/lava, or over floor lower
        than this layer by FLOOR_LEDGE."""
        e = np.asarray(edge) > 0
        below = zmap[box[1]:box[3], box[0]:box[2]]
        wet = wetmap[box[1]:box[3], box[0]:box[2]]
        keep = e & (np.isnan(below) | (wet != wthis) |
                    (zthis - np.nan_to_num(below, nan=-1e12) > max(FLOOR_LEDGE, lstep)))
        return Image.fromarray(keep.astype("uint8") * 255, "L")

    for L in range(FLOOR_LEVELS):
        layer = Image.new("RGB", canvas.size, (0, 0, 0))
        mask = Image.new("L", canvas.size, 0)
        lc = ImageDraw.Draw(layer)
        mc = ImageDraw.Draw(mask)
        band(lambda p, k: SWIM_FLOOR if k == "water" else floor_colour(ht(fz(p))), fw, only=L, target=lc)
        band(lambda p, k: 255, fw, only=L, target=mc)
        if zmap is not None:
            zl = Image.new("F", canvas.size, 0.0)
            band(lambda p, k: float(fz(p)), fw, only=L, target=ImageDraw.Draw(zl))
            wl = Image.new("L", canvas.size, 0)         # where this layer is water / lava (swum)
            band(lambda p, k: 255 if k == "water" else 0, fw, only=L, target=ImageDraw.Draw(wl))
        box = mask.getbbox()
        if box:
            pad = close_px + ring_px + 4
            box = (max(0, box[0] - pad), max(0, box[1] - pad), min(W_, box[2] + pad), min(H_, box[3] + pad))
            m0, l0 = mask.crop(box), layer.crop(box)
            closed = grow(grow(m0, close_px), close_px, shrink=True)
            # the filled-in gaps take the colour of the NEAREST floor pixel. (A MaxFilter on the
            # colour image took each channel's max separately, so where lava met purple floor the
            # gap came out pink - 196,78,118 - Great Trench 2026-11-18.)
            filled = nearest_fill(l0, m0)
            edge = grow(closed, ring_px)
            if zmap is not None:
                zfill = nearest_z(np.asarray(zl.crop(box)), m0)
                wfill = nearest_z(np.asarray(wl.crop(box)), m0) > 0
                edge = ledge_only(edge, zfill, box, wfill)
            canvas.paste(FLOOR_EDGE, (box[0], box[1], box[2], box[3]), edge)
        pmL = paint_mask(L)                     # painted floor gets the same dark edge as a walk
        if pmL is not None:
            pedge = grow(pmL, max(2, fw // 10))
            if zmap is not None and paint_z is not None:
                pzf = nearest_z(np.nan_to_num(paint_z), pmL)
                pedge = ledge_only(pedge, pzf, (0, 0, W_, H_), np.zeros((H_, W_), dtype=bool))
            canvas.paste(FLOOR_EDGE, (0, 0), pedge)
        for room, zm in zip(rooms, room_z):
            if level(zm) != L:
                continue
            ring = room["pts"]
            wet = sum(1 for q in ring if q.get("k") == "swim") * 2 > len(ring)
            if room["fill"] == "lava":
                col = LAVA_FLOOR
            elif room["fill"] == "water":
                col = WATER_FLOOR
            elif wet:
                col = SWIM_FLOOR
            else:
                col = floor_colour(ht(zm))
            draw.polygon([px(q) for q in ring], fill=col,
                         outline=FLOOR_EDGE if room["fill"] != "floor" else None,
                         width=max(3, fw // 6) if room["fill"] != "floor" else 1)
        if pmL is not None:
            canvas.paste(paint_rgb, (0, 0), pmL)
            if zmap is not None and paint_z is not None:
                pm_ = np.asarray(pmL) > 0
                zmap[pm_] = paint_z[pm_]
                wetmap[pm_] = False
        if box:
            canvas.paste(filled, (box[0], box[1]), closed)
            if zmap is not None:
                c_ = np.asarray(closed) > 0
                zmap[box[1]:box[3], box[0]:box[2]][c_] = zfill[c_]
                wetmap[box[1]:box[3], box[0]:box[2]][c_] = wfill[c_]

    # Hand-painted lava / water ALSO goes on top of the walked floor, so painting lava over a patch
    # of land turns it into lava (Boreal Forest, 2026-11-19: "I painted lava there but it isn't
    # showing over the land"). In edit order: a floor stroke painted after a lava stroke wins again.
    if any(e["op"] in ("lava", "water") for e in paints):
        after_hazard = False
        for e in paints:
            if e["op"] in ("lava", "water"):
                after_hazard = True
                paint(e, LAVA_FLOOR if e["op"] == "lava" else WATER_FLOOR)
            elif e["op"] == "floor" and after_hazard and paint_rgb is not None:
                m_ = Image.new("L", canvas.size, 0)
                paint(e, 255, target=ImageDraw.Draw(m_))
                canvas.paste(paint_rgb, (0, 0), m_)

    # /aptrail wire: a 1 m strip, drawn after the whole floor so a passage underneath can never
    # cover it, with the striped line on top
    ww = max(4, int(WIRE_WIDTH * s))
    wire_seen = False
    for w, ks in zip(walks, walk_kinds):
        for k, run in runs(w, ks):
            if k == "wire":
                wire_seen = True
                line = [px(p) for p in run]
                draw.line(line, fill=FLOOR_EDGE, width=ww + max(4, ww // 2), joint="curve")
                draw.line(line, fill=floor_colour(ht(sum(p["z"] for p in run) / len(run))),
                          width=ww, joint="curve")
                draw_wire(draw, line, max(3, ww // 2))

    # ---- 2. routes on top (or, with no routes, a thin typed line along every walk)
    entr = sorted({r["from"] for r in routes})
    used_kinds = {"wire"} if wire_seen else set()
    auto_syms = []          # (drop|climb, point): a drop or climb is near vertical, so its line is
                            # invisible from above - it gets a symbol where it starts / tops out
    rw = max(big // 330, int(fw * 0.32))             # route line: readable on a big cave too
    out_routes = []
    for r in routes:
        for part in r["parts"]:
            ks = classify(part)
            for k, run in runs(part, ks):
                col = (KIND_COLOUR[k] if args.route_colours == "type"
                       else ENTRANCE_COLOURS[entr.index(r["from"]) % len(ENTRANCE_COLOURS)])
                for bit in lined(run):
                    line = [px(p) for p in bit]
                    if k == "wire" and args.route_colours == "type":
                        draw_wire(draw, line, rw)
                    else:
                        draw.line(line, fill=(0, 0, 0), width=rw + 4, joint="curve")
                        draw.line(line, fill=col, width=rw, joint="curve")
                    used_kinds.add(k)
                if k in ("drop", "climb"):
                    auto_syms.append((k, run))
        out_routes.append({"artifact": r["artifact"], "from": r["from"], "file": r["file"],
                           "drawn": bool(r.get("drawn")),
                           "metres": round(r.get("distance", 0) / UNITS_PER_M),
                           "px": [[[round(c) for c in px(p)] for p in part] for part in r["parts"]]})
    if routes:
        # a fall off a thin wire is shown even outside the routes: it is where a slip takes you
        in_route = {id(run[0]) for k, run in auto_syms}
        for w, ks in zip(walks, walk_kinds):
            for k, run in runs(w, ks):
                if k == "drop" and run[0].get("k") == "wire" and id(run[0]) not in in_route:
                    auto_syms.append((k, run))
                    used_kinds.add(k)
    if not routes:
        for w, ks in zip(walks, walk_kinds):
            for k, run in runs(w, ks):
                if sum(1 for q in run if q.get("hide")) >= len(run) - 2:
                    continue                            # hazard-room outline lap (bar its ends)
                for bit in lined(run):
                    if k == "wire":
                        draw_wire(draw, [px(p) for p in bit], max(3, rw // 2))
                    else:
                        draw.line([px(p) for p in bit], fill=KIND_COLOUR[k], width=max(2, rw // 2),
                                  joint="curve")
                    used_kinds.add(k)
                if k in ("drop", "climb"):
                    auto_syms.append((k, run))

    # ---- 3. marks. Symbols first, then every label is placed where it overlaps nothing.
    ts = args.text_scale
    f_lab = font(max(12, int(big / 125 * ts)), bold=True)       # labels on the map
    icon = max(28, big // 30)
    out_marks, pins, used_marks = [], {}, {}
    occupied, labels = [], []                     # rects in use; (u, v, gap, text) to place

    def claim(u, v, r):
        occupied.append((u - r, v - r, u + r, v + r))

    seen_syms = set()
    for k, run in auto_syms:
        p = run[0] if k == "drop" else run[-1]
        if id(p) in seen_syms:                          # same stretch in a walk and a route
            continue
        seen_syms.add(id(p))
        u, v = px(p)
        draw_symbol(draw, k, u, v, icon * 0.32)
        claim(u, v, icon * 0.32)
        used_marks[k] = {"drop": "Drop down here", "climb": "Climb here"}[k]
        # top-down, a drop or climb is a dot: say how far it goes, so the height change shows
        dz = round(abs(run[0]["z"] - run[-1]["z"]) / UNITS_PER_M)
        if k == "drop" and run[0].get("k") == "wire":
            labels.append((u, v, icon * 0.4, "Fall off the wire: %d m down" % dz))
        elif dz:
            labels.append((u, v, icon * 0.4, "%d m %s" % (dz, "down" if k == "drop" else "up")))
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    route_dir = {}                                      # entrance name -> first heading of its route
    for r in routes:
        if r["from"] and r["parts"]:
            part = r["parts"][0]
            a = part[0]
            b = next((p for p in part if math.dist((p["x"], p["y"]), (a["x"], a["y"])) > 600), part[-1])
            route_dir.setdefault(r["from"].lower(), (b["x"] - a["x"], b["y"] - a["y"]))
    # one arrow per entrance name: `/aptrail entrance North` and later `route X from North` both mark
    # it. Keep the one where a route starts (its arrow follows the route), else the first.
    route_starts = {(r["from"].lower(), r["parts"][0][0]["x"], r["parts"][0][0]["y"])
                    for r in routes if r["from"] and r["parts"]}
    entrance_at = {}
    for m in marks:
        drawer, shown, _ = mark_kind(m)
        if drawer == "entrance":
            key = shown.lower()
            if key not in entrance_at or (key, m["x"], m["y"]) in route_starts:
                entrance_at[key] = id(m)
    for m in marks:
        drawer, shown, legend = mark_kind(m)
        if drawer == "entrance" and entrance_at.get(shown.lower()) != id(m):
            continue                                    # same entrance marked again
        u, v = px(m)
        if drawer == "entrance":
            d = route_dir.get(shown.lower()) or (cx - m["x"], cy - m["y"])
            L = math.hypot(*d) or 1.0
            arrow_len = icon * 2.2
            tail = (u - d[0] / L * arrow_len, v - d[1] / L * arrow_len)
            aw = max(6, icon // 3)
            draw_arrow(draw, tail, (u, v), aw)
            hw = aw * 1.6                                # the arrow's half width at its head
            occupied.append((min(tail[0], u) - hw, min(tail[1], v) - hw,
                             max(tail[0], u) + hw, max(tail[1], v) + hw))
            labels.append(((u + tail[0]) / 2, (v + tail[1]) / 2, hw + 8,
                           "Entrance" + (": " + shown if shown else "") +
                           ("  (%.1f, %.1f)" % gps(m) if gps else "")))
            used_marks["entrance"] = "Entrance"
        elif drawer == "artifact":
            ic = find_icon("artifact_of_the_" + shown.split()[-1].lower(),
                           "artifact_" + shown.split()[-1].lower())
            # no disc: the icon with a thin white outline that follows its own shape, like the
            # wiki cave maps (user 2026-11-18: "fully remove the white background")
            asz = int(icon * ARTIFACT_SIZE)
            r = asz * 0.47
            if ic:
                im_ = Image.open(ic).convert("RGBA")
                im_.thumbnail((asz, asz), Image.LANCZOS)
                pad = ARTIFACT_OUTLINE + 2
                a_ = Image.new("L", (im_.width + 2 * pad, im_.height + 2 * pad), 0)
                a_.paste(im_.getchannel("A").point(lambda x_: 255 if x_ > 40 else 0), (pad, pad))
                bb = a_.getbbox()
                ring = a_.filter(ImageFilter.MaxFilter(2 * ARTIFACT_OUTLINE + 1))
                x0, y0 = int(u - im_.width / 2) - pad, int(v - im_.height / 2) - pad
                canvas.paste((250, 250, 250), (x0, y0), ring)
                canvas.paste(im_, (x0 + pad, y0 + pad), im_)
                if bb:
                    cxa, cya = (bb[0] + bb[2]) / 2 - a_.width / 2, (bb[1] + bb[3]) / 2 - a_.height / 2
                    r = max(bb[2] - bb[0], bb[3] - bb[1]) / 2 + math.hypot(cxa, cya) + ARTIFACT_OUTLINE
            else:
                draw.polygon(poly_regular(u, v, r, 4), fill=(80, 255, 200), outline=(0, 0, 0))
            claim(u, v, r)
            labels.append((u, v, r + icon * 0.08, shown))
            pins[shown.split()[-1]] = [round(u), round(v)]
        elif drawer == "crate":
            col = next((c for c in CRATE_COLOURS if c in shown.lower().split()), "blue")
            ic = find_icon("%s_crate" % col)
            if ic:
                paste_icon(canvas, ic, u, v, icon)
            else:
                draw.rectangle((u - icon / 3, v - icon / 3, u + icon / 3, v + icon / 3),
                               fill=(90, 140, 230), outline=(0, 0, 0))
            rest = " ".join(w for w in shown.split() if w.lower() != col)
            claim(u, v, icon * 0.5)
            if rest:
                labels.append((u, v, icon * 0.55, rest))
            used_marks["crate"] = legend
        else:
            draw_symbol(draw, drawer, u, v, icon * 0.4)
            claim(u, v, icon * 0.4)
            if shown:
                labels.append((u, v, icon * 0.5, shown))
            used_marks[drawer] = legend
        out_marks.append({"label": m["label"], "px": [round(u), round(v)], "kind": drawer,
                          "name": shown, "x": m["x"], "y": m["y"], "z": m["z"], "file": m["file"]})
        if gps:
            out_marks[-1]["gps"] = [round(c, 1) for c in gps(m)]

    def hits(b):
        return any(b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3] for o in occupied)

    def overlap(b):
        return sum(max(0, min(b[2], o[2]) - max(b[0], o[0])) * max(0, min(b[3], o[3]) - max(b[1], o[1]))
                   for o in occupied)

    W, H = canvas.size
    for u, v, gap, txt in labels:
        # the 8 spots round the point first, then the same 8 further out (with a leader line back
        # to the point); if every spot overlaps something, the one that overlaps least
        best = None
        for ring in (1, 2, 3, 4.5):
            g = gap + (ring - 1) * f_lab.size * 1.6
            for dx, dy, anchor in ((g, 0, "lm"), (-g, 0, "rm"), (0, g, "mt"), (0, -g, "mb"),
                                   (g, g, "lt"), (-g, g, "rt"), (g, -g, "lb"), (-g, -g, "rb")):
                b = draw.textbbox((u + dx, v + dy), txt, font=f_lab, anchor=anchor,
                                  stroke_width=max(2, f_lab.size // 9))
                if not (b[0] >= panel and b[1] >= 0 and b[2] <= W and b[3] <= H):
                    continue
                score = overlap(b)
                if best is None or score < best[4]:
                    best = (u + dx, v + dy, anchor, b, score, ring)
                if score == 0:
                    break
            if best and best[4] == 0:
                break
        if best is None:                                # nowhere on the canvas: right of the point
            b = draw.textbbox((u + gap, v), txt, font=f_lab, anchor="lm")
            best = (u + gap, v, "lm", b, 0, 1)
        if best[5] > 1:                                 # moved away: a thin line back to the point
            b = best[3]
            tx_, ty_ = min(max(u, b[0]), b[2]), min(max(v, b[1]), b[3])
            r0 = gap * 0.8
            L = math.hypot(tx_ - u, ty_ - v) or 1.0
            draw.line([(u + (tx_ - u) / L * r0, v + (ty_ - v) / L * r0), (tx_, ty_)],
                      fill=(0, 0, 0), width=4)
            draw.line([(u + (tx_ - u) / L * r0, v + (ty_ - v) / L * r0), (tx_, ty_)],
                      fill=(225, 225, 225), width=2)
        text(draw, best[:2], txt, f_lab, anchor=best[2])
        occupied.append(best[3])

    # ---- 4. title, legend, routes list, compass, scale bar
    if not args.no_legend:
        f_title = font(max(20, int(big / 46 * ts)), bold=True)
        f_leg = font(max(13, int(big / 92 * ts)))
        x, y = 30, 26
        if args.base:                                   # no side panel on a base image: a box
            draw.rectangle((10, 10, int(big * 0.27), int(big * 0.45)), fill=(0, 0, 0))
        title = args.title or " / ".join(caves)
        text(draw, (x, y), title, f_title, fill=(90, 175, 255))
        y += int(f_title.size * 1.25)
        text(draw, (x, y), " / ".join(m.title() for m in maps_) + (" - " + CAVE_WHERE if CAVE_WHERE else ""),
             f_leg, fill=(200, 200, 200))
        y += int(f_leg.size * 2.0)
        sw = int(f_leg.size * 1.6)
        rows = []
        for k in ("path", "water", "fly", "wire", "drop", "climb"):
            if k in used_kinds:
                rows.append(("wire" if k == "wire" else "line", KIND_COLOUR[k], KIND_LABEL[k]))
        ent_z = next((m["z"] for m in marks if mark_kind(m)[0] == "entrance"), None)
        if ent_z is not None:
            floor_txt = "Cave floor: %+d m to %+d m from the entrance" % (
                round((zr0 - ent_z) / UNITS_PER_M), round((zr1 - ent_z) / UNITS_PER_M))
        else:
            floor_txt = "Cave floor: %d m from lowest to highest" % round((zr1 - zr0) / UNITS_PER_M)
        rows.append(("floor", None, floor_txt))
        swum = any(k == "water" for ks in walk_kinds for k in ks)
        if ((swum and not SWIM_IS_LAVA)
                or any(r["fill"] == "water" for r in rooms) or any(e["op"] == "water" for e in paints)):
            rows.append(("water_floor", None, "Water"))
        if (lava_seen or (swum and SWIM_IS_LAVA) or any(r["fill"] == "lava" for r in rooms)
                or any(e["op"] == "lava" for e in paints)):
            rows.append(("lava_floor", None, "Lava - do not walk"))
        for key in ("entrance", "crate", "button", "climb", "drop", "water", "danger", "note"):
            if key in used_marks:
                rows.append(("mark", key, used_marks[key]))
        for kind, val, label in rows:
            cy_ = y + f_leg.size // 2 + 2
            if kind == "line":
                draw.line([(x, cy_), (x + sw, cy_)], fill=(0, 0, 0), width=rw + 4)
                draw.line([(x, cy_), (x + sw, cy_)], fill=val, width=rw)
            elif kind == "wire":
                draw_wire(draw, [(x, cy_), (x + sw, cy_)], rw)
            elif kind == "floor":
                for i in range(sw):
                    draw.line([(x + i, cy_ - sw // 4), (x + i, cy_ + sw // 4)],
                              fill=floor_colour(i / max(sw - 1, 1)))
            elif kind == "water_floor":
                draw.rectangle((x, cy_ - sw // 4, x + sw, cy_ + sw // 4), fill=WATER_FLOOR)
            elif kind == "lava_floor":
                draw.rectangle((x, cy_ - sw // 4, x + sw, cy_ + sw // 4), fill=LAVA_FLOOR)
            elif val == "entrance":
                draw_arrow(draw, (x, cy_), (x + sw, cy_), max(4, sw // 5))
            elif val == "crate":
                ic = find_icon("blue_crate")
                if ic:
                    paste_icon(canvas, ic, x + sw / 2, cy_, sw)
            else:
                draw_symbol(draw, val, x + sw / 2, cy_, sw * 0.35)
            text(draw, (x + sw + 12, y), label, f_leg)
            y += int(f_leg.size * 1.7)
        if routes:
            y += int(f_leg.size * 0.8)
            text(draw, (x, y), "Routes", font(f_leg.size, bold=True), fill=(90, 175, 255))
            y += int(f_leg.size * 1.6)
            for r in sorted(routes, key=lambda r: (r["artifact"], r["from"])):
                col = (ENTRANCE_COLOURS[entr.index(r["from"]) % len(ENTRANCE_COLOURS)]
                       if args.route_colours == "entrance" else (230, 230, 230))
                txt = "%s%s - %d m" % (r["artifact"], " from " + r["from"] if r["from"] else "",
                                       round(r.get("distance", 0) / UNITS_PER_M))
                text(draw, (x, y), txt, f_leg, fill=col)
                y += int(f_leg.size * 1.5)
        # compass, top right
        cr = max(18, big // 50)
        ccx, ccy = canvas.size[0] - cr * 2, cr * 2
        draw.polygon([(ccx, ccy - cr), (ccx + cr * 0.45, ccy + cr * 0.6), (ccx, ccy + cr * 0.25),
                      (ccx - cr * 0.45, ccy + cr * 0.6)], fill=(90, 175, 255), outline=(0, 0, 0))
        text(draw, (ccx, ccy - cr - 4), "N", font(int(cr * 0.9), bold=True), anchor="mb")
        # scale bar, bottom right
        metres = nice_metres(canvas.size[0] * 0.12 / (s * UNITS_PER_M))
        bar = metres * UNITS_PER_M * s
        bx1, by_ = canvas.size[0] - 30, canvas.size[1] - 40
        draw.rectangle((bx1 - bar, by_, bx1, by_ + 8), fill=(235, 235, 235), outline=(0, 0, 0))
        draw.rectangle((bx1 - bar, by_, bx1 - bar / 2, by_ + 8), fill=(95, 95, 95))
        text(draw, (bx1 - bar / 2, by_ - 6), "%d m" % metres, f_leg, anchor="mb")
        text(draw, (30, canvas.size[1] - 30), "Top-down, north up. Recorded with /aptrail (ArkAP).",
             font(max(11, int(big / 130 * ts))), fill=(150, 150, 150))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    canvas.save(args.out)
    side = os.path.splitext(args.out)[0] + ".json"
    with open(side, "w", encoding="utf-8") as fh:
        json.dump({"_comment": "Generated by tools/cave_trail_map.py. pixel = scale * world + offset.",
                   "sources": [os.path.basename(p) for p in paths], "caves": caves, "maps": maps_,
                   "image": os.path.basename(args.out), "size": list(canvas.size),
                   "world_to_px": {"scale": s, "ox": ox, "oy": oy}, "z_range": [z0, z1],
                   "marks": out_marks, "pins": pins, "routes": out_routes,
                   "edits": edits_path if edits else None,
                   # what made this picture: tools/cave_map_edit.py renders it again when either
                   # changed (an editor started on older code must not leave its picture behind)
                   "renderer_mtime": os.path.getmtime(os.path.abspath(__file__)),
                   "edits_mtime": (os.path.getmtime(edits_path)
                                   if edits_path and os.path.isfile(edits_path) else None)},
                  fh, indent=1)
    print("wrote %s (%dx%d) and %s" % (args.out, canvas.size[0], canvas.size[1], side))
    if pins:
        print("  _cave_pins: %s" % json.dumps(pins))


if __name__ == "__main__":
    main()
