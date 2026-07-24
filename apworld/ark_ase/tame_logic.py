"""Tame/craft access logic for the ARK apworld.

Compiles the dependency graph in data/tame_logic.json (seeded from the Ark IDs spreadsheet) into
Archipelago access rules so the fill can never strand a needed item behind a dino you can't yet
tame. A "Tamed: X" check requires the ENGRAMS its taming method needs, expanded recursively through
the item recipe graph (macros like "Crossbow KO" -> Crossbow + Tranq Arrow + their stations).

Requirement expression grammar (from the sheet): '+' = AND, '|' = OR, parentheses group. Leaves are
engram/recipe node names. OR is preserved faithfully (never collapsed to AND).

Dinos not in the sheet fall back to a requirement derived from the apworld's DINO_TIER table
(T1 -> early KO, T2 -> Crossbow KO, T3 -> Rifle KO), plus a scuba requirement for deep-water tames.
"""
import re
from typing import Callable, Dict, List, Tuple

# AST node forms:  ('has', ap_item_name) | ('and', [nodes]) | ('or', [nodes]) | ('true',)
AST = tuple

# deep-water dinos (roster short names) whose tame realistically needs scuba gear ("Deep Dive").
# Shallow/surface water tames (Ichthy, Manta, Sarco, Diplocaulus, Electrophorus) don't.
DEEP_WATER = {"Mosasaur", "Tusoteuthis", "Plesiosaur", "Angler", "Dunkle", "Megalodon",
              "Liopleurodon"}

# DINO_TIER -> fallback requirement expression (for dinos the sheet doesn't cover).
_TIER_REQ = {0: "", 1: "Slingshot | (Bola + Club)", 2: "Crossbow KO", 3: "Rifle KO"}


class TameLogic:
    def __init__(self, data: dict):
        self.recipes: Dict[str, str] = data.get("item_recipes", {})
        self.alias: Dict[str, str] = data.get("alias", {})            # node -> engram short name
        self.dino_raw: Dict[str, str] = data.get("dino_tame_raw", {})  # sheet dino -> expr
        self.dino_alias: Dict[str, str] = data.get("dino_alias", {})   # roster short -> sheet name
        self.cave_tames: Dict[str, str] = data.get("cave_tames", {})   # cave dweller -> cave-floor override

    # ---- requirement expression for a roster dino (cave override > sheet > DINO_TIER fallback) ----
    def dino_expr(self, roster_short: str, tier: int) -> str:
        if roster_short in self.cave_tames:            # cave dwellers: survival floor overrides method
            return self.cave_tames[roster_short]
        sheet_name = self.dino_alias.get(roster_short, roster_short)
        expr = self.dino_raw.get(sheet_name) or self.dino_raw.get(roster_short)
        if expr and expr.strip():
            return expr
        base = _TIER_REQ.get(tier, "")
        if roster_short in DEEP_WATER:
            base = (base + " + Deep Dive") if base else "Deep Dive"
        return base

    # ---- compile an expression string into an AST over ('has', ap_item_name) ----
    # `free` = ap_item_names that are auto-granted (start engrams, etc.) and therefore never sit
    # in the item pool. Requiring has(free) would be permanently unsatisfiable and strand the
    # location, so those leaves collapse to ('true',).
    def compile(self, expr: str, remap: Callable[[str], str], free=frozenset()) -> AST:
        if not expr or not expr.strip():
            return ("true",)
        return self._expand(_parse(expr), remap, frozenset(), free)

    def _expand(self, node: AST, remap, seen, free) -> AST:
        kind = node[0]
        if kind in ("and", "or"):
            kids = [self._expand(c, remap, seen, free) for c in node[1]]
            if kind == "or":
                if any(k == ("true",) for k in kids):   # a free branch makes the OR trivially true
                    return ("true",)
            else:                                        # AND: drop always-true terms
                kids = [k for k in kids if k != ("true",)]
            if not kids:
                return ("true",)
            return kids[0] if len(kids) == 1 else (kind, kids)
        if kind == "name":
            return self._expand_name(node[1], remap, seen, free)
        return ("true",)

    def _expand_name(self, name: str, remap, seen, free) -> AST:
        if name in seen:                       # cycle guard
            return ("true",)
        seen = seen | {name}
        parts: List[AST] = []
        if name in self.alias:                 # this node is a real gated engram
            ap = remap(self.alias[name])
            if ap not in free:                 # auto-granted engrams are always available
                parts.append(("has", ap))
        recipe = self.recipes.get(name)        # + whatever crafting/using it needs
        if recipe and recipe.strip():
            parts.append(self._expand(_parse(recipe), remap, seen, free))
        parts = [p for p in parts if p != ("true",)]
        if not parts:
            return ("true",)                   # free node (crop/resource/unknown)
        return parts[0] if len(parts) == 1 else ("and", parts)

    # every engram ap_item_name that could be required across the given expressions (for
    # progression classification - these must be progression so the fill guarantees reachability).
    def required_items(self, exprs: List[str], remap, free=frozenset()) -> set:
        out: set = set()
        for e in exprs:
            _collect(self.compile(e, remap, free), out)
        return out


# ---- expression parser: OR of ANDs of factors; NAME may contain spaces/&/digits/'/' ----
def _parse(expr: str) -> AST:
    toks = _tokenize(expr)
    pos = [0]

    def parse_or():
        nodes = [parse_and()]
        while pos[0] < len(toks) and toks[pos[0]] == "|":
            pos[0] += 1
            nodes.append(parse_and())
        return nodes[0] if len(nodes) == 1 else ("or", nodes)

    def parse_and():
        nodes = [parse_factor()]
        while pos[0] < len(toks) and toks[pos[0]] == "+":
            pos[0] += 1
            nodes.append(parse_factor())
        return nodes[0] if len(nodes) == 1 else ("and", nodes)

    def parse_factor():
        t = toks[pos[0]]
        if t == "(":
            pos[0] += 1
            inner = parse_or()
            if pos[0] < len(toks) and toks[pos[0]] == ")":
                pos[0] += 1
            return inner
        pos[0] += 1
        return ("name", t)

    return parse_or()


def _tokenize(expr: str) -> List[str]:
    out, buf = [], ""
    for ch in expr:
        if ch in "+|()":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
            out.append(ch)
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def _collect(ast: AST, out: set) -> None:
    if ast[0] == "has":
        out.add(ast[1])
    elif ast[0] in ("and", "or"):
        for c in ast[1]:
            _collect(c, out)


def eval_ast(ast: AST, state, player: int) -> bool:
    kind = ast[0]
    if kind == "has":
        return state.has(ast[1], player)
    if kind == "and":
        return all(eval_ast(c, state, player) for c in ast[1])
    if kind == "or":
        return any(eval_ast(c, state, player) for c in ast[1])
    return True   # 'true'
