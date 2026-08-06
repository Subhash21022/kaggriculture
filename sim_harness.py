"""Local runner for the Kaggriculture agent.

  python sim_harness.py duel                 # main vs each baseline, seeded
  python sim_harness.py day    [seed]        # one game with the per-day table
  python sim_harness.py batch  [n]           # unseeded head-to-heads, win/loss record
  python sim_harness.py sweep  <key> <v,v,v> # PARAMS sweep, scored on head-to-heads
  python sim_harness.py mirror [n]           # main vs main, checks for symmetry bugs

Diagnostics per day: money, tiles by state, share of actions spent moving, share
wasted on silent no-ops, shed occupancy, and realized average sale price per
product. Invariants are asserted every turn; violations are printed, not swallowed.
"""

import importlib.util
import statistics
import sys
import time
from collections import defaultdict

from kaggle_environments import make
from kaggle_environments.envs.kaggriculture import kaggriculture as K

# --------------------------------------------------------------------------- #
# Trade logging (monkeypatched onto the interpreter)
# --------------------------------------------------------------------------- #

_TRADES = []
_CTX = {"farms": None, "step": 0}
_orig_process_market = K._process_market
_orig_commit_unit = K._commit_unit


def _patched_process_market(state, env):
    _CTX["farms"] = state[0].observation.farms
    _CTX["step"] = state[0].observation.get("step", 0)
    return _orig_process_market(state, env)


def _patched_commit_unit(op, item, price, farm, private, market):
    ok = _orig_commit_unit(op, item, price, farm, private, market)
    if ok:
        farms = _CTX["farms"] or []
        pid = -1
        for i, f in enumerate(farms):
            if f is farm:
                pid = i
                break
        _TRADES.append((_CTX["step"], pid, op, item, price))
    return ok


K._process_market = _patched_process_market
K._commit_unit = _patched_commit_unit


# --------------------------------------------------------------------------- #
# Action validation -- every silent no-op is a wasted turn
# --------------------------------------------------------------------------- #

MOVE_OPS = ("NORTH", "SOUTH", "EAST", "WEST")
UNIT_OPS = MOVE_OPS + (
    "PASS", "PICKUP", "DROP", "PLANT", "WATER", "HARVEST", "FERTILIZE",
    "BUILD_COOP", "BUILD_PASTURE", "DIG", "PLACE", "FEED",
    "COLLECT_FERTILIZER", "CARE",
)
MARKET_OPS = ("BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL", "SELL", "HIRE", "BUY_LAND")


def _shed_tiles(board):
    half = board // 2
    return {(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)}


def classify_op(obs, op, pos, inv, seeds, board, plant_demand):
    """Return ('move'|'act'|'pass'|'illegal'|'wasted', detail)."""
    if not isinstance(op, list) or not op:
        return "illegal", "not a non-empty list"
    name = op[0]
    if name not in UNIT_OPS:
        return "illegal", "unknown op %r" % (name,)
    x, y = pos
    farm = obs["farms"][obs["player"]]
    tile = farm["tiles"][y][x]

    if name in MOVE_OPS:
        d = {"NORTH": (0, -1), "SOUTH": (0, 1), "EAST": (1, 0), "WEST": (-1, 0)}[name]
        nx, ny = x + d[0], y + d[1]
        if not (0 <= nx < board and 0 <= ny < board):
            return "wasted", "move off the board"
        return "move", ""
    if name == "PASS":
        return "pass", ""

    if tile == "LOCKED":
        return "wasted", "%s on a LOCKED tile" % name

    at_shed = (x, y) in _shed_tiles(board)
    if name in ("DROP", "PICKUP"):
        if not at_shed:
            return "wasted", "%s away from the shed" % name
        if name == "DROP":
            return ("act", "") if any(v > 0 for v in inv.values()) else ("wasted", "DROP with empty hands")
        if len(op) < 2:
            return "illegal", "PICKUP without an item"
        if (obs["private"].get("shed") or {}).get(op[1], 0) <= 0:
            return "wasted", "PICKUP %s not in shed" % op[1]
        return "act", ""

    if name == "PLANT":
        if len(op) < 2 or op[1] not in K.CROPS:
            return "illegal", "PLANT %r" % (op[1:],)
        if tile is not None:
            return "wasted", "PLANT on an occupied tile"
        if seeds.get(op[1], 0) <= 0:
            return "wasted", "PLANT %s with no seed" % op[1]
        if plant_demand.get(op[1], 0) > seeds.get(op[1], 0):
            return "wasted", "PLANT %s over seed count (whole batch dropped)" % op[1]
        return "act", ""

    if name == "WATER":
        if not (isinstance(tile, dict) and tile.get("kind") == "PLANT"):
            return "wasted", "WATER on a non-plant"
        if tile["watered_today"]:
            return "wasted", "WATER already watered"
        return "act", ""

    if name == "HARVEST":
        if not isinstance(tile, dict) or tile.get("yield_units", 0) <= 0:
            return "wasted", "HARVEST with nothing ready"
        if tile.get("kind") == "PLANT":
            cd = K.CROPS[tile["crop"]]
            if obs["day"] - tile["planted_day"] < cd["first_yield_day"]:
                return "wasted", "HARVEST before first yield"
        return "act", ""

    if name == "FERTILIZE":
        if not (isinstance(tile, dict) and tile.get("kind") == "PLANT"):
            return "wasted", "FERTILIZE on a non-plant"
        if inv.get("FERTILIZER", 0) <= 0:
            return "wasted", "FERTILIZE with no fertilizer"
        return "act", ""

    if name == "DIG":
        if tile is None:
            return "wasted", "DIG on bare ground"
        if isinstance(tile, dict) and "animal" in tile:
            return "wasted", "DIG on an occupied structure"
        return "act", ""

    if name in ("BUILD_COOP", "BUILD_PASTURE"):
        return ("act", "") if tile is None else ("wasted", "%s on an occupied tile" % name)

    if name == "PLACE":
        if len(op) < 2:
            return "illegal", "PLACE without an item"
        item = op[1]
        if item in K.ANIMALS and isinstance(tile, dict) \
                and tile.get("kind") == K.ANIMALS[item]["structure"] and "animal" not in tile:
            return ("act", "") if inv.get(item, 0) > 0 else ("wasted", "PLACE %s not carried" % item)
        if at_shed:
            return ("act", "") if inv.get(item, 0) > 0 else ("wasted", "PLACE %s not carried" % item)
        return "wasted", "PLACE %s with no valid target" % item

    if name == "FEED":
        if not (isinstance(tile, dict) and "animal" in tile):
            return "wasted", "FEED on a non-animal"
        if tile["fed_today"]:
            return "wasted", "FEED already fed"
        if inv.get("WHEAT", 0) <= 0:
            return "wasted", "FEED with no wheat carried"
        return "act", ""

    if name == "COLLECT_FERTILIZER":
        if not (isinstance(tile, dict) and "animal" in tile):
            return "wasted", "COLLECT_FERTILIZER on a non-animal"
        if not tile["fertilizer_available"]:
            return "wasted", "COLLECT_FERTILIZER with none available"
        return "act", ""

    if name == "CARE":
        if not (isinstance(tile, dict) and "animal" in tile):
            return "wasted", "CARE on a non-animal"
        if tile["cared_today"]:
            return "wasted", "CARE already cared"
        return "act", ""

    return "act", ""


# --------------------------------------------------------------------------- #
# Recorder
# --------------------------------------------------------------------------- #

class Recorder(object):
    """Wraps an agent: records actions, timings, invariant violations, daily state."""

    def __init__(self, fn, name, collect_days=False):
        self.fn = fn
        self.name = name
        self.collect_days = collect_days
        self.counts = defaultdict(int)
        self.day_counts = defaultdict(lambda: defaultdict(int))
        self.days = []
        self.problems = []
        self.times = []
        self.max_orders_exceeded = 0
        self.crashes = 0

    def __call__(self, obs, config):
        t0 = time.perf_counter()
        try:
            action = self.fn(obs, config)
        except Exception as exc:               # the real runner swallows these
            self.crashes += 1
            self.problems.append((obs.get("step", -1), "CRASH", repr(exc)))
            action = {"farmer": ["PASS"], "hands": [], "market": []}
        self.times.append(time.perf_counter() - t0)

        try:
            self.inspect(obs, config, action)
        except Exception as exc:
            self.problems.append((obs.get("step", -1), "HARNESS", repr(exc)))
        return action

    # -- per-turn bookkeeping ------------------------------------------------ #
    def inspect(self, obs, config, action):
        board = int(config["boardSize"])
        tpd = int(config["turnsPerDay"])
        cap = int(config["shedCapacity"])
        max_orders = int(config["maxMarketOrdersPerTurn"])
        day, hour, step = obs["day"], obs["hour"], obs["step"]
        player = obs["player"]
        farm = obs["farms"][player]
        private = obs["private"]
        seeds = private.get("seeds") or {}
        invs = private.get("inventories") or [{}]

        if not isinstance(action, dict):
            self.problems.append((step, "ILLEGAL", "action is not a dict"))
            return
        units = [action.get("farmer", ["PASS"])] + list(action.get("hands") or [])
        positions = [list(farm["farmer"])] + [list(h) for h in farm["hands"]]
        if len(action.get("hands") or []) > len(farm["hands"]):
            self.problems.append((step, "ILLEGAL", "more hand actions than hands"))

        plant_demand = defaultdict(int)
        for op in units:
            if isinstance(op, list) and len(op) >= 2 and op[0] == "PLANT":
                plant_demand[op[1]] += 1

        for i, op in enumerate(units):
            if i >= len(positions):
                break
            inv = dict(invs[i]) if i < len(invs) else {}
            kind, detail = classify_op(obs, op, positions[i], inv, seeds, board, plant_demand)
            self.counts[kind] += 1
            self.day_counts[day][kind] += 1
            if kind in ("illegal", "wasted"):
                self.day_counts[day][kind + "_n"] += 1
                if len(self.problems) < 400:
                    self.problems.append((step, kind.upper(), "%s: %s" % (op, detail)))

        orders = action.get("market") or []
        if len(orders) > max_orders:
            self.max_orders_exceeded += 1
        for o in orders:
            if not isinstance(o, list) or not o or o[0] not in MARKET_OPS:
                self.problems.append((step, "ILLEGAL", "market order %r" % (o,)))
            elif o[0] in ("BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL", "SELL") and len(o) < 3:
                self.problems.append((step, "ILLEGAL", "market order missing count %r" % (o,)))

        # -- invariants that only resolve at the end-of-day refresh ---------- #
        if hour == tpd - 1:
            doomed_plants, doomed_animals = [], []
            tiles_by_state = defaultdict(int)
            for y, row in enumerate(farm["tiles"]):
                for x, t in enumerate(row):
                    if t == "LOCKED":
                        tiles_by_state["locked"] += 1
                    elif t is None:
                        tiles_by_state["empty"] += 1
                    elif isinstance(t, dict):
                        k = t.get("kind")
                        if k == "WEED":
                            tiles_by_state["weed"] += 1
                        elif k == "PLANT":
                            tiles_by_state["crop:" + t["crop"]] += 1
                            if not t["watered_today"] and t["consecutive_unwatered"] >= 1:
                                doomed_plants.append((x, y, t["crop"]))
                        elif "animal" in t:
                            tiles_by_state["animal:" + t["animal"]] += 1
                            if not t["fed_today"] and t["consecutive_unfed"] >= 1:
                                doomed_animals.append((x, y, t["animal"]))
                        else:
                            tiles_by_state["empty_" + str(k).lower()] += 1
            shed = private.get("shed") or {}
            shed_used = sum(v for v in shed.values() if v > 0)
            carried = sum(sum(v for v in iv.values() if v > 0) for iv in invs)
            overflow = max(0, shed_used + carried - cap)

            if doomed_plants:
                self.problems.append((step, "PLANT_LOSS", str(doomed_plants[:6])))
            if doomed_animals:
                self.problems.append((step, "ANIMAL_LOSS", str(doomed_animals[:6])))
            if overflow > 0:
                self.problems.append((step, "SHED_OVERFLOW", "%d items discarded" % overflow))

            if self.collect_days:
                self.days.append({
                    "day": day,
                    "money": farm["money"],
                    "tiles": dict(tiles_by_state),
                    "shed": shed_used,
                    "carried": carried,
                    "overflow": overflow,
                    "hands": farm["hires_today"],
                    "quads": len(farm["unlocked_quadrants"]),
                    "doomed": len(doomed_plants) + len(doomed_animals),
                })


# --------------------------------------------------------------------------- #
# Running games
# --------------------------------------------------------------------------- #

def load_agent(path, alias):
    """Load a .py file as a fresh module so each side gets its own global state."""
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod.agent


def play(agent0, agent1, seed=None, name0="p0", name1="p1", collect=False, steps=720):
    del _TRADES[:]
    # Generous wall clock: the default 1200s trips when several sweeps share a CPU,
    # which is a harness artefact, not agent slowness (per-turn budget is 1s and the
    # agent runs in ~2ms).
    cfg = {"episodeSteps": steps, "runTimeout": 9000}
    if seed is not None:
        cfg["seed"] = seed
    env = make("kaggriculture", configuration=cfg, debug=False)
    r0 = Recorder(agent0, name0, collect_days=collect)
    r1 = Recorder(agent1, name1, collect_days=False)
    env.run([r0, r1])
    final = env.steps[-1]
    return {
        "env": env,
        "rewards": [float(s.reward or 0) for s in final],
        "recorders": [r0, r1],
        "trades": list(_TRADES),
        "seed": env.info.get("seed"),
    }


def trade_summary(trades, player):
    """Realized average sale price and volume per product."""
    sells = defaultdict(list)
    buys = defaultdict(list)
    for _step, pid, op, item, price in trades:
        if pid != player:
            continue
        if op == "SELL":
            sells[item].append(price)
        elif op in ("BUY_PRODUCT", "BUY_SEED", "BUY_ANIMAL"):
            buys[item].append(price)
    out = {}
    for item, prices in sells.items():
        out[item] = {"n": len(prices), "avg": sum(prices) / len(prices),
                     "first": prices[0], "last": prices[-1],
                     "revenue": sum(prices)}
    return out, {i: {"n": len(p), "spend": sum(p)} for i, p in buys.items()}


def print_day_table(res, player=0):
    rec = res["recorders"][player]
    print("\n%-4s %9s %5s %5s %4s %4s %5s %5s %6s  %s" % (
        "day", "money", "move%", "wast%", "shed", "carr", "hands", "quad", "lost", "tiles"))
    for row in rec.days:
        d = row["day"]
        c = rec.day_counts[d]
        total = c["move"] + c["act"] + c["pass"] + c["wasted"] + c["illegal"]
        total = max(1, total)
        tiles = ", ".join("%s=%d" % (k.replace("crop:", "").replace("animal:", "").lower(), v)
                          for k, v in sorted(row["tiles"].items())
                          if k not in ("locked",) and v)
        print("%-4d %9.0f %5.1f %5.1f %4d %4d %5d %5d %6d  %s" % (
            d, row["money"], 100.0 * c["move"] / total,
            100.0 * (c["wasted"] + c["illegal"]) / total,
            row["shed"], row["carried"], row["hands"], row["quads"],
            row["doomed"] + (1 if row["overflow"] else 0), tiles))

    brain = getattr(sys.modules.get("main_a"), "BRAIN", None)
    if brain is not None and getattr(brain, "diag", None):
        total = max(1, sum(brain.diag.values()))
        print("\nwhere the actions went: " + "  ".join(
            "%s=%.1f%%" % (k, 100.0 * v / total) for k, v in sorted(brain.diag.items())))

    sells, buys = trade_summary(res["trades"], player)
    print("\nrealized sales (player %d):" % player)
    for item in sorted(sells, key=lambda i: -sells[i]["revenue"]):
        s = sells[item]
        print("  %-11s n=%-5d avg=$%-7.1f first=$%-5d last=$%-5d revenue=$%d"
              % (item, s["n"], s["avg"], s["first"], s["last"], s["revenue"]))
    print("purchases:")
    for item in sorted(buys, key=lambda i: -buys[i]["spend"]):
        b = buys[item]
        print("  %-11s n=%-5d spend=$%d" % (item, b["n"], b["spend"]))


def print_health(res):
    for i, rec in enumerate(res["recorders"]):
        c = rec.counts
        total = max(1, sum(c[k] for k in ("move", "act", "pass", "wasted", "illegal")))
        by_kind = defaultdict(int)
        for _s, kind, _d in rec.problems:
            by_kind[kind] += 1
        print("  [%s] acts=%d move=%.1f%% pass=%.1f%% wasted=%.1f%% illegal=%d "
              "crashes=%d maxturn=%.3fs" % (
                  rec.name, total, 100.0 * c["move"] / total, 100.0 * c["pass"] / total,
                  100.0 * c["wasted"] / total, c["illegal"], rec.crashes,
                  max(rec.times) if rec.times else 0.0))
        if by_kind:
            print("       problems: %s" % dict(by_kind))
            for s, kind, d in rec.problems[:5]:
                print("         step %s %s %s" % (s, kind, d))


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_day(argv):
    seed = int(argv[0]) if argv else 11
    a0 = load_agent("main.py", "main_a")
    a1 = load_agent("phase1_agent.py", "phase1_b")
    res = play(a0, a1, seed=seed, name0="main", name1="phase1", collect=True)
    print("seed=%s  rewards=%s" % (res["seed"], res["rewards"]))
    print_day_table(res, 0)
    print()
    print_health(res)


def cmd_duel(argv):
    seeds = [int(s) for s in argv] or [1, 2, 3]
    a_main = load_agent("main.py", "main_a")
    opponents = [
        ("random", "random"),
        ("starter", "starter"),
        ("phase1", load_agent("phase1_agent.py", "phase1_b")),
        ("mirror", load_agent("main.py", "main_b")),
    ]
    for label, opp in opponents:
        wins = losses = ties = 0
        margins = []
        for s in seeds:
            res = play(a_main, opp, seed=s, name0="main", name1=label)
            m, o = res["rewards"]
            margins.append(m - o)
            if m > o:
                wins += 1
            elif m < o:
                losses += 1
            else:
                ties += 1
            bad = sum(1 for _s, k, _d in res["recorders"][0].problems
                      if k in ("CRASH", "ILLEGAL"))
            print("  vs %-8s seed=%-4s  main=%-9.0f opp=%-9.0f  %s%s" % (
                label, s, m, o, "WIN " if m > o else ("LOSS" if m < o else "TIE "),
                "  !! %d hard problems" % bad if bad else ""))
        print("  vs %-8s  W-L-T %d-%d-%d  median margin %+.0f\n" % (
            label, wins, losses, ties, statistics.median(margins)))


def cmd_batch(argv):
    n = int(argv[0]) if argv else 8
    a_main = load_agent("main.py", "main_a")
    opp = load_agent("phase1_agent.py", "phase1_b")
    wins = losses = ties = 0
    scores = []
    for i in range(n):
        res = play(a_main, opp, seed=None, name0="main", name1="phase1")
        m, o = res["rewards"]
        scores.append(m)
        wins += m > o
        losses += m < o
        ties += m == o
        print("  run %-3d main=%-9.0f phase1=%-9.0f  %s" % (
            i, m, o, "WIN" if m > o else ("LOSS" if m < o else "TIE")))
    print("\n  W-L-T %d-%d-%d   main median=%.0f  min=%.0f  max=%.0f"
          % (wins, losses, ties, statistics.median(scores), min(scores), max(scores)))


def cmd_mirror(argv):
    n = int(argv[0]) if argv else 3
    wins = [0, 0, 0]
    for i in range(n):
        a = load_agent("main.py", "mirror_a%d" % i)
        b = load_agent("main.py", "mirror_b%d" % i)
        res = play(a, b, seed=100 + i, name0="A", name1="B")
        x, y = res["rewards"]
        wins[0 if x > y else (1 if y > x else 2)] += 1
        print("  seed=%-4d A=%-9.0f B=%-9.0f  diff=%+.0f" % (100 + i, x, y, x - y))
        print_health(res)
    print("\n  A-B-T %d-%d-%d (a large skew here means a first-mover market edge)" % tuple(wins))


# The three strongest ladder opponents all converged on the same shape: ~30
# strawberry tiles, 6-8 cows, 6 sheep, 12 hires/day, no geese. Tuning against
# `random`/`phase1` is misleading because they sell nothing, so every price stays
# high; this sparring partner actually competes for the same shallow curves.
META_PARAMS = {
    "target_strawberry": 30, "target_melon": 8, "target_cow": 8,
    "target_sheep": 6, "target_goose": 0, "useful_action_frac": 0.40,
    "spare_tile_cap": 20,
}


def load_with(path, alias, overrides):
    fn = load_agent(path, alias)
    sys.modules[alias].PARAMS.update(overrides)
    return fn


def cmd_arena(argv):
    """Candidate (current PARAMS) vs a field-realistic strawberry-heavy opponent."""
    seeds = [int(s) for s in argv] or [1, 2, 3, 4, 5]
    wins = losses = ties = 0
    margins = []
    for s in seeds:
        cand = load_agent("main.py", "arena_cand")
        meta = load_with("main.py", "arena_meta", META_PARAMS)
        res = play(cand, meta, seed=s, name0="mine", name1="meta")
        a, b = res["rewards"]
        margins.append(a - b)
        wins += a > b
        losses += a < b
        ties += a == b
        print("  seed=%-4d mine=%-9.0f meta=%-9.0f  %s" % (
            s, a, b, "WIN" if a > b else ("LOSS" if a < b else "TIE")))
    print("\n  vs META  W-L-T %d-%d-%d  median margin %+.0f  my median %.0f" % (
        wins, losses, ties, statistics.median(margins),
        statistics.median([m for m in margins])))


def cmd_sweepvs(argv):
    """Sweep a PARAMS key, scoring every candidate against the META opponent."""
    if len(argv) < 2:
        print("usage: sweepvs <key> <v1,v2,...> [n_seeds]")
        return
    key, values = argv[0], argv[1].split(",")
    seeds = list(range(1, (int(argv[2]) if len(argv) > 2 else 5) + 1))
    base = load_agent("main.py", "sv_probe")
    default = sys.modules["sv_probe"].PARAMS[key]
    print("sweeping %s (default %r) vs META over %d seeds\n" % (key, default, len(seeds)))
    for raw in values:
        val = raw.lower() == "true" if isinstance(default, bool) else type(default)(raw)
        cand = load_with("main.py", "sv_cand", {key: val})
        w = l = t = 0
        margins, mine = [], []
        for s in seeds:
            meta = load_with("main.py", "sv_meta", META_PARAMS)
            res = play(cand, meta, seed=s, name0="cand", name1="meta")
            a, b = res["rewards"]
            margins.append(a - b)
            mine.append(a)
            w += a > b
            l += a < b
            t += a == b
        print("  %s=%-10r W-L-T %d-%d-%d  median margin %+8.0f  my median %.0f"
              % (key, val, w, l, t, statistics.median(margins), statistics.median(mine)))


def cmd_sweep(argv):
    if len(argv) < 2:
        print("usage: sweep <PARAMS key> <v1,v2,...> [n_seeds]")
        return
    key, values = argv[0], argv[1].split(",")
    n_seeds = int(argv[2]) if len(argv) > 2 else 3
    seeds = list(range(1, n_seeds + 1))

    baseline = load_agent("main.py", "sweep_base")
    import sweep_base  # noqa: F401  (registered by load_agent)
    base_mod = sys.modules["sweep_base"]
    default = base_mod.PARAMS[key]
    print("sweeping %s (default %r) over %s seeds\n" % (key, default, len(seeds)))

    for raw in values:
        cand = load_agent("main.py", "sweep_cand")
        mod = sys.modules["sweep_cand"]
        val = type(default)(raw) if not isinstance(default, bool) else raw.lower() == "true"
        mod.PARAMS[key] = val
        wins = losses = ties = 0
        margins = []
        for s in seeds:
            res = play(cand, baseline, seed=s, name0="cand", name1="base")
            c, b = res["rewards"]
            margins.append(c - b)
            wins += c > b
            losses += c < b
            ties += c == b
        print("  %s=%-10r  W-L-T %d-%d-%d  median margin %+.0f"
              % (key, val, wins, losses, ties, statistics.median(margins)))


COMMANDS = {"day": cmd_day, "duel": cmd_duel, "batch": cmd_batch, "arena": cmd_arena,
            "sweepvs": cmd_sweepvs,
            "mirror": cmd_mirror, "sweep": cmd_sweep}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "duel"
    if cmd not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[cmd](sys.argv[2:])
