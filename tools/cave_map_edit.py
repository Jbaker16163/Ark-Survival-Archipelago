"""Browse every cave map, and edit one by hand: erase trail, paint lava / water / floor, draw pathways, place and remove marks.

    python tools/cave_map_edit.py                         (gallery of every cave under trails/)
    python tools/cave_map_edit.py jungle_dungeon          (straight into that cave's editor)
    python tools/cave_map_edit.py trails/ragnarok/jungle_dungeon_*.jsonl

Serves http://127.0.0.1:8765/ (works from any folder; Ctrl+C stops it):
  /                     the gallery (tools/cave_map_gallery.html): every cave, grouped by map, with
                        its picture, routes and artifacts; Edit / Open PNG / Download
  /edit/<map>/<cave>/   the editor (tools/cave_map_editor.html). "Save & render" writes the edits
                        and renders again, so the page shows the result straight away.

A cave's picture is build/caves/<cave>.png. It is rendered again by itself whenever a recording,
its edits or tools/cave_trail_map.py is newer than it.

Nothing in the recordings is changed. The edits go to <trails dir>/<cave>.edits.json in world
units (format in tools/cave_trail_map.py, "edits"), and tools/cave_trail_map.py applies that file
on every render - so they survive re-renders, new recordings of the same cave and --routes-only.
The previous edits file is kept as <cave>.edits.json.bak on each save.

Anything after "--" goes to cave_trail_map.py as is, e.g. `-- --corridor 300 --text-scale 0.8`.
"""
import argparse
import contextlib
import glob
import io
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cave_trail_map as ctm  # noqa: E402
import importlib  # noqa: E402

_ctm_mtime = os.path.getmtime(ctm.__file__)


def fresh_renderer():
    """Reload tools/cave_trail_map.py when it changed since it was loaded, so a running editor
    never renders with old code (a new edit type would otherwise be ignored until a restart)."""
    global _ctm_mtime
    t = os.path.getmtime(ctm.__file__)
    if t != _ctm_mtime:
        importlib.reload(ctm)
        _ctm_mtime = t
        print("(tools/cave_trail_map.py changed - reloaded it)")

EDITOR = os.path.join(HERE, "cave_map_editor.html")
GALLERY = os.path.join(HERE, "cave_map_gallery.html")
CAVES_DIR = os.path.join(ROOT, "build", "caves")


def read_trail(path):
    """-> (start header or None, summary) for one recording, reading it once."""
    head, info = None, {"routes": [], "artifacts": set(), "entrances": set(), "points": 0}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            t = o.get("type")
            if t == "start" and head is None:
                head = o
            elif t is None:
                info["points"] += 1
            elif t == "route_end":
                info["routes"].append("%s from %s" % (o.get("artifact", "?"), o.get("from") or "?"))
            elif t == "mark" and o.get("kind") == "artifact":
                info["artifacts"].add(o.get("name") or o.get("label", ""))
            elif t == "mark" and o.get("kind") == "entrance":
                info["entrances"].add(o.get("name") or "")
    return head, info


class Cave:
    def __init__(self, map_key, name, paths, out=None):
        self.map, self.name, self.paths = map_key, name, sorted(paths)
        self.slug = ctm.slug(name)
        self.key = "%s/%s" % (self.map, self.slug)
        self.edits_path = os.path.join(os.path.dirname(self.paths[0]), self.slug + ".edits.json")
        self.out = out or os.path.join(CAVES_DIR, self.slug + ".png")
        self.side = os.path.splitext(self.out)[0] + ".json"
        self.lock = threading.Lock()

    def inputs(self):
        return self.paths + ([self.edits_path] if os.path.isfile(self.edits_path) else []) + [ctm.__file__]

    def stale(self):
        if not (os.path.isfile(self.out) and os.path.isfile(self.side)):
            return True
        try:
            with open(self.side, encoding="utf-8") as fh:
                made = json.load(fh)
        except (ValueError, OSError):
            return True
        # made by other renderer code, or from another version of the edits: draw it again
        if made.get("renderer_mtime") != os.path.getmtime(ctm.__file__):
            return True
        ed = os.path.getmtime(self.edits_path) if os.path.isfile(self.edits_path) else None
        if made.get("edits_mtime") != ed:
            return True
        t = os.path.getmtime(self.out)
        return any(os.path.getmtime(p) > t for p in self.inputs() if os.path.isfile(p))

    def render(self, extra):
        buf = io.StringIO()
        ok = True
        with contextlib.redirect_stdout(buf):
            try:
                fresh_renderer()
                ctm.main(self.paths + ["--out", self.out, "--edits", self.edits_path] + extra)
            except SystemExit as e:
                ok = False
                print("render stopped: %s" % e)
            except Exception as e:          # keep the server up; show the error in the page
                ok = False
                print("render failed: %r" % e)
        log = buf.getvalue()
        print("[%s]\n%s" % (self.key, log), end="")
        return ok, log

    def ensure(self, extra):
        with self.lock:
            if self.stale():
                return self.render(extra)
        return True, ""

    def read_edits(self):
        if os.path.isfile(self.edits_path):
            with open(self.edits_path, encoding="utf-8") as fh:
                return json.load(fh)
        return {"cave": self.name, "edits": []}

    def summary(self):
        heads, routes, arts, ents, points, latest = [], [], set(), set(), 0, 0
        for p in self.paths:
            h, info = read_trail(p)
            heads.append(h or {})
            routes += info["routes"]
            arts |= info["artifacts"]
            ents |= info["entrances"]
            points += info["points"]
            latest = max(latest, os.path.getmtime(p))
        n_edits = 0
        if os.path.isfile(self.edits_path):
            try:
                n_edits = len(self.read_edits().get("edits") or [])
            except ValueError:
                n_edits = -1
        return {"key": self.key, "map": self.map,
                "map_name": next((h.get("map_name") for h in heads if h.get("map_name")), self.map.title()),
                "cave": self.name, "recordings": [os.path.basename(p) for p in self.paths],
                "routes": routes, "artifacts": sorted(a for a in arts if a),
                "entrances": sorted(e for e in ents if e), "points": points,
                "recorded": time.strftime("%Y-%m-%d %H:%M", time.localtime(latest)),
                "edits": n_edits, "image": os.path.basename(self.out),
                "rendered": os.path.isfile(self.out) and not self.stale()}


def scan(extra_paths=()):
    """Every cave under trails/<map>/*.jsonl (plus any recordings named on the command line),
    grouped by map and cave name."""
    groups = {}
    # a recording's file name is unique (cave, time, steam id): a copy of one that is already
    # under trails/ is the same recording, so it counts once - the copy named on the command line
    # wins, and comes first (its folder is where the edits file goes)
    named = [os.path.abspath(f) for f in extra_paths]
    seen = {os.path.basename(f).lower() for f in named}
    found = sorted(os.path.abspath(f) for f in glob.glob(os.path.join(ROOT, "trails", "*", "*.jsonl")))
    files = list(dict.fromkeys(named + [f for f in found if os.path.basename(f).lower() not in seen]))
    for p in files:
        try:
            with open(p, encoding="utf-8") as fh:
                h = json.loads(fh.readline())
        except (ValueError, OSError):
            continue
        if h.get("type") != "start" or not h.get("cave"):
            continue
        mk = h.get("map") or os.path.basename(os.path.dirname(p))
        groups.setdefault((mk, ctm.slug(h["cave"])), (h["cave"], []))[1].append(p)
    slugs = [s for _, s in groups]
    out = {}
    for (mk, s), (name, ps) in groups.items():
        # the same cave name on two maps: keep their pictures apart
        png = os.path.join(CAVES_DIR, ("%s_%s" % (mk, s) if slugs.count(s) > 1 else s) + ".png")
        c = Cave(mk, name, ps, out=png)
        out[c.key] = c
    return out


def main():
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("trails", nargs="*", help="a cave name or its .jsonl files (globs ok); none = gallery")
    ap.add_argument("--out", help="output .png for the named cave (default build/caves/<cave>.png)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    def find(g):
        # as given (from where you are), then from the repo folder, then a bare cave name
        # ("jungle_dungeon", "Jungle Dungeon") -> trails/<any map>/<cave>_*.jsonl
        for pat in (g, os.path.join(ROOT, g),
                    os.path.join(ROOT, "trails", "*", ctm.slug(g) + "_*.jsonl")):
            hits = [p for p in glob.glob(pat) if p.endswith(".jsonl")]
            if hits:
                return hits
        return [g]

    named = sorted({os.path.abspath(p) for g in args.trails for p in find(g)})
    missing = [p for p in named if not os.path.isfile(p)]
    if missing:
        sys.exit("no such file: %s" % ", ".join(missing))
    caves = scan(named)
    caves_lock = threading.Lock()
    start = "/"
    if named:
        hits = {c.key for c in caves.values() for p in c.paths if p in named}
        if len(hits) != 1:
            sys.exit("those files are %d different caves - name one cave" % len(hits))
        c = caves[hits.pop()]
        if args.out:
            caves[c.key] = c = Cave(c.map, c.name, c.paths, out=os.path.abspath(args.out))
        ok, _ = c.ensure(extra)
        if not ok:
            sys.exit("render failed - see above")
        start = "/edit/%s/" % c.key
    if not caves:
        print("no recordings under %s yet" % os.path.join(ROOT, "trails"))

    def rescan():
        """New recordings copied in while the server runs show up on the next gallery load."""
        with caves_lock:
            fresh = scan(named)
            for k, c in fresh.items():
                if k not in caves or caves[k].paths != c.paths:
                    if k in caves and caves[k].out != c.out:     # keep an --out override
                        c = Cave(c.map, c.name, c.paths, out=caves[k].out)
                    caves[k] = c
        return caves

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def file(self, path, ctype):
            if not os.path.isfile(path):
                return self.send(404, "not rendered", "text/plain")
            with open(path, "rb") as fh:
                return self.send(200, fh.read(), ctype)

        def cave_and_rest(self, path, prefix):
            # /<prefix>/<map>/<slug>/<rest>
            parts = [unquote(x) for x in path[len(prefix):].split("/")]
            if len(parts) < 2:
                return None, None
            c = caves.get("%s/%s" % (parts[0], parts[1]))
            return c, "/".join(parts[2:])

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                with open(GALLERY, encoding="utf-8") as fh:
                    return self.send(200, fh.read(), "text/html; charset=utf-8")
            if path == "/api/caves":
                cs = rescan()
                return self.send(200, json.dumps([c.summary() for c in sorted(
                    cs.values(), key=lambda c: (c.map, c.name.lower()))]), "application/json")
            if path.startswith("/thumb/"):
                c, _ = self.cave_and_rest(path[:-4] if path.endswith(".png") else path, "/thumb/")
                if not c:
                    return self.send(404, "no such cave", "text/plain")
                c.ensure(extra)
                return self.file(c.out, "image/png")
            if path.startswith("/edit/"):
                c, rest = self.cave_and_rest(path, "/edit/")
                if not c:
                    return self.send(404, "no such cave", "text/plain")
                if rest == "":
                    if not path.endswith("/"):
                        self.send_response(301)
                        self.send_header("Location", path + "/")
                        self.end_headers()
                        return
                    c.ensure(extra)
                    with open(EDITOR, encoding="utf-8") as fh:
                        return self.send(200, fh.read(), "text/html; charset=utf-8")
                if rest == "map.png":
                    c.ensure(extra)
                    return self.file(c.out, "image/png")
                if rest == "map.json":
                    c.ensure(extra)
                    return self.file(c.side, "application/json")
                if rest == "edits":
                    return self.send(200, json.dumps(c.read_edits()), "application/json")
                if rest == "info":
                    return self.send(200, json.dumps({"cave": c.name, "map": c.map, "edits_file": c.edits_path,
                                                      "image": c.out,
                                                      "sources": [os.path.basename(p) for p in c.paths]}),
                                     "application/json")
            self.send(404, "not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?")[0]
            c, rest = self.cave_and_rest(path, "/edit/") if path.startswith("/edit/") else (None, None)
            if not c or rest != "save":
                return self.send(404, "not found", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("edits"), list):
                    raise ValueError("expected {\"edits\": [...]}")
            except ValueError as e:
                return self.send(400, json.dumps({"ok": False, "log": "bad edits: %s" % e}), "application/json")
            data["cave"] = c.name
            with c.lock:
                if os.path.isfile(c.edits_path):
                    with open(c.edits_path, "rb") as fh, open(c.edits_path + ".bak", "wb") as bk:
                        bk.write(fh.read())
                tmp = c.edits_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=1)
                os.replace(tmp, c.edits_path)
                ok, log = c.render(extra)
            self.send(200, json.dumps({"ok": ok, "log": log}), "application/json")

    class Server(HTTPServer):
        # Python turns SO_REUSEADDR on, and on Windows that lets a SECOND server bind a port that
        # is already in use - the old one keeps answering and the new one is never reached.
        allow_reuse_address = False

    try:
        srv = Server(("127.0.0.1", args.port), H)
    except OSError:
        sys.exit("port %d is already in use - the editor is probably still running in another window. "
                 "Press Ctrl+C there first, or add --port %d." % (args.port, args.port + 1))
    url = "http://127.0.0.1:%d%s" % (args.port, start)
    print("\n%d cave(s): %s\nOpen %s  (gallery: http://127.0.0.1:%d/ - Ctrl+C here to stop)"
          % (len(caves), ", ".join(sorted(caves)), url, args.port))
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
