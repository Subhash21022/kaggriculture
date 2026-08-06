"""Phase 1 baseline: survive 719 turns without crashing.

One farmer, a small wheat block next to the shed, watered on the days that matter,
harvested at max yield, sold immediately. No hands, no routing, no market model.
This is the bar main.py has to clear.
"""

CROP = "WHEAT"
FIRST_YIELD_DAY = 2
MAX_YIELD_DAY = 4
BLOCK = [(4, 4), (3, 4), (2, 4), (4, 3), (3, 3), (2, 3), (4, 2), (3, 2)]
SHED = (4, 4)


def _step_toward(cur, target):
    dx, dy = target[0] - cur[0], target[1] - cur[1]
    if dx > 0:
        return "EAST"
    if dx < 0:
        return "WEST"
    if dy > 0:
        return "SOUTH"
    if dy < 0:
        return "NORTH"
    return None


def agent(observation, configuration=None):
    try:
        obs = observation
        player = obs["player"]
        farm = obs["farms"][player]
        private = obs["private"]
        day = obs["day"]
        seeds = private.get("seeds") or {}
        shed = private.get("shed") or {}
        inv = (private.get("inventories") or [{}])[0]
        fx, fy = farm["farmer"]

        market = []
        have = shed.get(CROP, 0)
        if have > 0:
            market.append(["SELL", CROP, have])
        n_seeds = seeds.get(CROP, 0)
        if n_seeds < 2 and farm["money"] >= 40:
            market.append(["BUY_SEED", CROP, 2])

        # Drop a full hand at the shed so the produce can actually be sold.
        carried = sum(v for v in inv.values() if v > 0)
        if carried >= 8:
            if (fx, fy) == SHED:
                return {"farmer": ["DROP"], "hands": [], "market": market}
            mv = _step_toward((fx, fy), SHED)
            return {"farmer": [mv] if mv else ["PASS"], "hands": [], "market": market}

        for tx, ty in BLOCK:
            tile = farm["tiles"][ty][tx]
            op = None
            if tile is None:
                if n_seeds > 0 and day <= 25:
                    op = ["PLANT", CROP]
            elif isinstance(tile, dict) and tile.get("kind") == "WEED":
                op = ["DIG"]
            elif isinstance(tile, dict) and tile.get("kind") == "PLANT":
                age = day - tile["planted_day"]
                if tile["yield_units"] > 0 and age >= max(FIRST_YIELD_DAY, MAX_YIELD_DAY):
                    op = ["HARVEST"]
                elif not tile["watered_today"] and (
                        tile["consecutive_unwatered"] >= 1 or 2 <= age <= MAX_YIELD_DAY):
                    op = ["WATER"]
            if op is None:
                continue
            if (fx, fy) == (tx, ty):
                return {"farmer": op, "hands": [], "market": market}
            mv = _step_toward((fx, fy), (tx, ty))
            return {"farmer": [mv] if mv else ["PASS"], "hands": [], "market": market}

        return {"farmer": ["PASS"], "hands": [], "market": market}
    except Exception:
        return {"farmer": ["PASS"], "hands": [], "market": []}
