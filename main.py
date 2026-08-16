"""Kaggriculture competition agent.

Layered design:
  * static tables + market model   -- reimplemented from kaggriculture.py
  * TurnState                      -- normalised view of one observation
  * DayPlan                        -- tile roles, workforce size, worker->tile clusters
  * turn executor                  -- one concrete op per worker, strict priority
  * market executor                -- <= maxMarketOrdersPerTurn orders

Every tunable lives in PARAMS so sim_harness.py can sweep it.
The top-level `agent` never raises; on any error it falls back to PASS.

Strategy in one paragraph: actions are scarcer than land, so tiles go to whatever
earns the most per worker-action, priced at the margin behind everything already in
the ground. Cows and sheep win that ranking -- the town drains milk and wool faster
than one farm can supply them, so both trade well above base all season, while eggs
sit at base. Melon is the best crop by a distance and geese only soak up leftover
labour. Livestock starves without wheat, so cash for feed is reserved before any
animal is bought, and the feed itself is bought rather than grown while it stays
cheap, because market orders cost zero actions. Fertilizer is the quiet edge: the
README tells everyone it cannot be sold, but the interpreter happily sells it, and
nothing in the town ever drains it.

The counter-intuitive part is scale. `useful_action_frac` deliberately understates
the workforce, because an unfed animal escapes for good and an unwatered plant turns
to weed -- over-extending costs tiles you already own, not just the marginal one.
Running a smaller, fully-serviced farm was worth more than every market refinement
combined (median $70k -> $108k).
"""

import math

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

PARAMS = {
    # ---- workforce -------------------------------------------------------- #
    # A hand yields 24 actions; at ~$30 an action the marginal hand stops paying
    # around fib(13) = $377, so the cost cap rather than the count is the real gate.
    "max_hands": 14,            # hard cap on hires per day
    "max_hire_cost": 400,       # never pay more than this for a single hand
    "cheap_hire_cost": 60,      # always take hands costing at most this (see plan_hires)
    "cheap_hire_frac": 0.015,   # ... but never more than this share of the bank
    "hire_cash_frac": 0.5,      # spend at most this share of cash on a day's hires
    # Over-hire vs. the estimated work. Deliberately large: once expected_jobs stopped
    # counting idle tiles the estimate got honest, and honest under-hires, because it
    # can't see the ~55% of a worker-day that goes on walking. More hands wins even as
    # the efficiency ratio falls -- what matters is absolute useful actions, not the
    # ratio. Swept on seed 13: 1.1=$99k, 1.35=$124k, 1.6=$133k, 2.0=$129k, 3.2=$128k.
    "hire_slack": 1.6,
    "travel_per_tile": 1.8,     # measured moves per tile visited, for the estimate

    # ---- livestock (premium targets sized to each product's town drain) ----- #
    # Wool absorbs ~338 units and milk ~437 over a season once the shops are up,
    # so those two are capped by market depth. Geese take whatever labour is left:
    # the egg curve is logarithmic and effectively bottomless.
    "target_sheep": 14,
    "target_cow": 18,
    "target_goose": 60,         # capped in practice by livestock_capacity()
    "animal_last_day": {"SHEEP": 20, "COW": 19, "GOOSE": 22},
    "animal_min_productions": 2,  # never buy stock that can't yield this many times
    "early_animal_days": 4,       # days where livestock outranks seeds for cash
    "early_seed_hold_frac": 0.55,
    "score_ratio_cutoff": 0.72,   # only buy species scoring near the best one
    "max_unplaced_animals": 6,  # don't stockpile livestock we can't put down
    "feed_days_cover": 4,       # cash held back to feed everything we own
    "care": True,               # CARE banks a yield bonus; ~$50-200 per action
    # Sweeping this is really sweeping farm size. Lower means a smaller, fully
    # serviced farm, and smaller kept winning head-to-head: an unfed animal is a
    # total write-off and an unwatered plant is a weed, so the downside of
    # over-extending is far worse than the upside of one more tile.
    # Swept: 0.24 > 0.30 > 0.34 > 0.38 > 0.42, and 0.18 gives most of it back.
    "useful_action_frac": 0.24,
    "crop_action_cost": 1.6,    # actions a crop tile costs per day
    "animal_action_cost": 3.4,  # feed + care + collect + harvest share

    # ---- crops ------------------------------------------------------------ #
    # Melon is the best crop in the game per action: 6 units of a $250 base good
    # for ~10 actions, and the only product no shop ever drains, so its price is
    # held up purely by the town centre.
    "target_melon": 20,
    "target_wheat": 10,
    "target_carrot": 0,
    # Only worth a tile once fertilized (4 units -> 8). Every strong ladder opponent
    # ran ~30 of these; the arena agrees but weakly (+16k/+18k/+21k margin for
    # 0/14/26 over 5 seeds), so this is a middle value, not a sharp optimum. The
    # crop_score gate drops it automatically if the price ever crashes.
    "target_strawberry": 20,
    "target_tomato": 0,
    "min_score": 12.0,          # $/worker-action below which a tile isn't worth it
    "spare_tile_cap": 14,       # extra tiles handed to the best-scoring crop
    "score_horizon_days": 8,    # how far ahead a score prices our own supply
    # Staggering sowing keeps watering peaks flat, but on the opening days there is
    # nothing else to water and every day a melon is not in the ground is a day it
    # cannot pay back. Ladder replays showed opponents with 12 melons down on day 0
    # against our 0.
    "max_plant_per_day": 5,
    "plant_burst_per_day": 14,  # ... applies while day <= plant_burst_days
    "plant_burst_days": 2,

    # ---- feed ------------------------------------------------------------- #
    "wheat_buy_price_cap": 62,  # above this, stop topping up and let tiles grow it
    "wheat_days_buffer": 1.25,  # days of feed to hold in the shed
    "wheat_shed_frac": 0.30,    # ... but never more than this share of the shed

    # ---- capital ---------------------------------------------------------- #
    "cash_reserve": 300,        # working float, capped by the fraction below
    "cash_reserve_frac": 0.30,
    "land_slack": 8,            # buy land when free tiles drop under this
    "land_labour_headroom": 5,  # ... and only if we could staff that many more
    "land_last_day": 21,
    "land_reserve": {0: 700, 1: 1500, 2: 3000},   # extra cash to hold per purchase

    # ---- market ----------------------------------------------------------- #
    "hold_frac": {              # don't sell below hold_frac * base ...
        "WHEAT": 0.45, "CARROT": 0.45, "TOMATO": 0.40, "STRAWBERRY": 0.45,
        "MELON": 0.32, "EGG": 0.45, "MILK": 0.42, "WOOL": 0.42,
        "FERTILIZER": 0.12,     # ... except fertilizer, which never recovers
    },
    "hold_frac_default": 0.45,
    # Global multiplier on every hold floor. Lower = sell earlier into a fuller
    # curve. On a ~60-unit-deep premium good, whoever sells first takes the price;
    # holding for a recovery only pays if the town drain arrives before the rival.
    "hold_frac_scale": 1.0,
    "slip_per_turn": 0.965,     # ... and never move a price more than this in one turn
    "liquidate_start_day": 24,  # ramp every floor to $1 by the final turn
    "shed_pressure_start": 0.60,   # shed fullness that starts relaxing the floors
    "shed_panic": 0.85,            # fullness at which we dump at any price
    # Opponent-aware selling. Their farm is public, so their pending supply is
    # knowable; on a shallow curve, selling first is most of the edge.
    "rival_supply_threshold": 8,
    # How heavily a rival's visible pending supply discounts our own buy decision.
    "rival_pipeline_weight": 0.0,   # measured worse at 1.0 and 2.0; see notes
    "rival_floor_discount": 0.62,
    # A collect action competes with CARE on a cow (+1 milk, ~$280). Its market price
    # is the wrong yardstick once we grow ongoing crops, though: applied to a
    # strawberry the same unit is worth several hundred dollars of extra fruit.
    "fert_collect_min_price": 45,
    "fert_per_ongoing_tile": 0.6,   # units held back per standing ongoing crop

    # ---- logistics -------------------------------------------------------- #
    # The end-of-day refresh dumps every inventory into the shed for free, so a
    # mid-day run to the shed is only worth its travel when the shed is at risk
    # of overflowing (overflow is destroyed) or the season is ending.
    "drop_threshold": 5,        # minimum cargo worth a trip once under pressure
    # Farm-wide (shed + all cargo) / capacity that triggers a run to the shed. Tuned
    # against overflow, not score alone: 0.65 -> 0.8 gains 2.7% and *reduces* losses
    # by freeing the actions for tending; 0.9 starts destroying produce (3 overflow
    # events across 3 games) and gives it all back.
    "haul_pressure": 0.8,
    "steal_radius": 6,          # don't cross the farm for one spare action
}

# --------------------------------------------------------------------------- #
# Static tables (mirrored from kaggriculture.py)
# --------------------------------------------------------------------------- #

CROPS = {
    "WHEAT":      {"seed": 10, "first_yield_day": 2, "max_yield_day": 4, "interval": 0, "max_yield": 6, "ongoing": False},
    "CARROT":     {"seed": 20, "first_yield_day": 2, "max_yield_day": 3, "interval": 0, "max_yield": 4, "ongoing": False},
    "TOMATO":     {"seed": 50, "first_yield_day": 8, "max_yield_day": 8, "interval": 1, "max_yield": 4, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first_yield_day": 10, "max_yield_day": 10, "interval": 2, "max_yield": 4, "ongoing": True},
    "MELON":      {"seed": 80, "first_yield_day": 10, "max_yield_day": 12, "interval": 0, "max_yield": 6, "ongoing": False},
}

ANIMALS = {
    "GOOSE": {"cost": 300, "structure": "COOP",    "first_yield_day": 4, "interval": 1, "max_held": 4, "product": "EGG"},
    "COW":   {"cost": 400, "structure": "PASTURE", "first_yield_day": 8, "interval": 2, "max_held": 6, "product": "MILK"},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "first_yield_day": 6, "interval": 3, "max_held": 6, "product": "WOOL"},
}

PRODUCTS = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"]

MARKET_I0 = 10000
PRICE_FLOOR = 1

MARKET_PARAMS = {
    "WHEAT":      {"base":  25, "I0": MARKET_I0, "T": 400, "below_func": "sqrt",   "below_target": 0.80, "above_func": "log",    "above_target": 0.20},
    "CARROT":     {"base":  35, "I0": MARKET_I0, "T": 450, "below_func": "log",    "below_target": 0.20, "above_func": "sqrt",   "above_target": 0.70},
    "TOMATO":     {"base":  60, "I0": MARKET_I0, "T": 200, "below_func": "linear", "below_target": 0.40, "above_func": "sqrt",   "above_target": 0.60},
    "STRAWBERRY": {"base": 120, "I0": MARKET_I0, "T": 100, "below_func": "sqrt",   "below_target": 0.70, "above_func": "linear", "above_target": 1.60},
    "MELON":      {"base": 250, "I0": MARKET_I0, "T": 300, "below_func": "log",    "below_target": 0.20, "above_func": "sq",     "above_target": 3.60},
    "EGG":        {"base":  50, "I0": MARKET_I0, "T": 332, "below_func": "linear", "below_target": 0.40, "above_func": "log",    "above_target": 0.20},
    "MILK":       {"base": 160, "I0": MARKET_I0, "T": 122, "below_func": "sqrt",   "below_target": 0.60, "above_func": "linear", "above_target": 1.60},
    "WOOL":       {"base": 200, "I0": MARKET_I0, "T": 105, "below_func": "log",    "below_target": 0.20, "above_func": "sq",     "above_target": 3.20},
    "FERTILIZER": {"base": 100, "I0": MARKET_I0, "T": 200, "below_func": "linear", "below_target": 0.40, "above_func": "linear", "above_target": 0.40},
}

LAND_PRICES = [1000, 2000, 4000]
MOVES = {"NORTH": (0, -1), "SOUTH": (0, 1), "EAST": (1, 0), "WEST": (-1, 0)}
SAFE_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}
STRUCTURE_OF = dict((s, ANIMALS[s]["structure"]) for s in ANIMALS)


def harvest_age(crop):
    """Age at which a one-time crop stops gaining yield, so we stop watering it.

    Yield starts at 1 and gains 1 per watered day inside [ceil(max_yield_day/2),
    max_yield_day], so melon caps at 6 units on age 10 -- two days before its
    max_yield_day. Harvesting on the cap saves two watering actions per tile.
    """
    cd = CROPS[crop]
    if cd["ongoing"]:
        return cd["first_yield_day"]
    window = (cd["max_yield_day"] + 1) // 2
    age_at_cap = window + (cd["max_yield"] - 1) - 1
    return max(cd["first_yield_day"], min(cd["max_yield_day"], age_at_cap))


HARVEST_AGE = dict((c, harvest_age(c)) for c in CROPS)


def is_production_day(tile, day):
    """True if this ongoing crop fires a scheduled production at tonight's refresh.

    `_daily_refresh_plants` tests `next_day - planted_day - first_yield_day` against
    the interval, and the fertilizer bonus needs the plant watered AND fertilized on
    this same day -- so this is the day that has to be paid for.
    """
    cd = CROPS[tile["crop"]]
    if not cd["ongoing"]:
        return False
    since = day + 1 - int(tile.get("planted_day", day)) - cd["first_yield_day"]
    if since < 0 or since % cd["interval"] != 0:
        return False
    return since // cd["interval"] + 1 <= cd["max_yield"]


def crop_yield(crop, remaining_days, fertilized=False):
    """(units, worker-actions, tile-days) for planting today with this much season left.

    One-time crops: plant, water on the planting day, then every other day to stay
    alive, then every day inside the bonus window, then harvest. Verified against the
    interpreter -- at full horizon wheat is (4, 6, 5), carrot (3, 5, 4), melon
    (6, 10, 11), strawberry (4, 17, 17).

    Truncating by the remaining season matters: a strawberry needs 17 days to pay for
    its tile, so one planted on day 18 is 17 days of watering for a single unit.
    """
    cd = CROPS[crop]
    r = int(remaining_days)
    if r < cd["first_yield_day"]:
        return 0, 0, 0
    if cd["ongoing"]:
        n = min(cd["max_yield"], (r - cd["first_yield_day"]) // cd["interval"] + 1)
        days = cd["first_yield_day"] + cd["interval"] * (n - 1)
        # Each scheduled production yields 1, or 2 if the plant is both watered and
        # fertilized that day. One FERTILIZE covers 3 days, so it spans several
        # productions -- measured on the interpreter: strawberry 4 units -> 8.
        units = 2 * n if fertilized else n
        ferts = int(math.ceil(n * cd["interval"] / 3.0)) if fertilized else 0
        harvests = int(math.ceil(units / float(cd["max_yield"])))
        return units, 1 + days // 2 + n + ferts + harvests, days + 1
    window = (cd["max_yield_day"] + 1) // 2
    age = min(r, HARVEST_AGE[crop])
    bonus = max(0, age - window + 1)
    units = min(cd["max_yield"], 1 + bonus)
    waters = len([d for d in range(0, min(window, age + 1)) if d % 2 == 0]) + bonus
    return units, 1 + waters + 1, age + 1


CROP_PROFILE = dict((c, crop_yield(c, 99)) for c in CROPS)


# --------------------------------------------------------------------------- #
# Market model
# --------------------------------------------------------------------------- #

def _shape(func, x):
    x = max(0.0, x)
    if func == "linear":
        return x
    if func == "sq":
        return x * x
    if func == "sqrt":
        return math.sqrt(x)
    if func == "log":
        return math.log(1.0 + x)
    if func == "log10":
        return math.log10(1.0 + x)
    return x


def market_price(item, inventory, params=None):
    """Exact reimplementation of the interpreter's price curve."""
    p = (params or MARKET_PARAMS)[item]
    base, I0, T = p["base"], p["I0"], p["T"]
    if inventory < I0:
        f = p["below_func"]
        amp = p["below_target"] * base / _shape(f, T)
        price = base + amp * _shape(f, I0 - inventory)
    else:
        f = p["above_func"]
        amp = p["above_target"] * base / _shape(f, T)
        price = base - amp * _shape(f, inventory - I0)
    return max(PRICE_FLOOR, int(round(price)))


def price_after_selling(item, inventory, qty, params=None):
    """Price once `qty` units have been dumped (sales at $1 don't add supply)."""
    inv = inventory
    for _ in range(max(0, qty)):
        if market_price(item, inv, params) <= PRICE_FLOOR:
            break
        inv += 1
    return market_price(item, inv, params)


def price_after_buying(item, inventory, qty, params=None):
    return market_price(item, inventory - max(0, qty), params)


def units_above_floor(item, inventory, floor, cap, params=None):
    """Largest n <= cap such that every one of the n units sells at >= floor.

    Price is monotone in inventory above I0, so this binary-searches rather than
    walking unit by unit -- the agent has a 1s/turn budget.
    """
    if cap <= 0:
        return 0
    if market_price(item, inventory, params) < floor:
        return 0
    lo, hi = 0, int(cap)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if market_price(item, inventory + mid - 1, params) >= floor:
            lo = mid
        else:
            hi = mid - 1
    return lo


def cost_of_buying(item, inventory, qty, params=None):
    """What `qty` units cost, quoted at post-buy inventory like the interpreter."""
    total, inv = 0, inventory
    for _ in range(max(0, qty)):
        inv -= 1
        total += market_price(item, inv, params)
    return total


def affordable_units(item, inventory, budget, cap, params=None):
    """How many units of `item` `budget` buys, given the rising scarcity curve."""
    n, inv, spent = 0, inventory, 0
    while n < cap:
        inv -= 1
        price = market_price(item, inv, params)
        if spent + price > budget:
            break
        spent += price
        n += 1
    return n, spent


def _fib(n):
    a, b = 1, 1
    for _ in range(n):
        a, b = b, a + b
    return a


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

def quadrant_of(x, y, board):
    half = board // 2
    return ("N" if y < half else "S") + ("W" if x < half else "E")


def shed_access_tiles(board):
    half = board // 2
    return [(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)]


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def step_toward(cur, target):
    """Greedy one-step move. Every tile is passable, so greedy is optimal."""
    dx, dy = target[0] - cur[0], target[1] - cur[1]
    if abs(dx) >= abs(dy):
        if dx > 0:
            return "EAST"
        if dx < 0:
            return "WEST"
    if dy > 0:
        return "SOUTH"
    if dy < 0:
        return "NORTH"
    if dx > 0:
        return "EAST"
    if dx < 0:
        return "WEST"
    return None


def build_route(board, unlocked):
    """Serpentine each unlocked quadrant, starting from its shed-side corner.

    Walking a row and acting on each tile costs 1 move per tile; letting workers
    wander costs 4+. Roles are handed out in this order too, so the tiles that
    need four visits a day (animals) end up closest to the shed.
    """
    half = board // 2
    route = []
    for q in ("NW", "NE", "SW", "SE"):
        if q not in unlocked:
            continue
        rows = list(range(half - 1, -1, -1)) if q[0] == "N" else list(range(half, board))
        cols = list(range(half - 1, -1, -1)) if q[1] == "W" else list(range(half, board))
        flip = False
        for y in rows:
            for x in (reversed(cols) if flip else cols):
                route.append((x, y))
            flip = not flip
    return route


# --------------------------------------------------------------------------- #
# Normalised turn state
# --------------------------------------------------------------------------- #

def _cfg(cfg, key, default):
    try:
        val = cfg.get(key, default) if hasattr(cfg, "get") else getattr(cfg, key, default)
    except Exception:
        return default
    return default if val is None else val


class TurnState(object):
    """Typed view over one raw observation."""

    def __init__(self, obs, cfg):
        self.obs = obs
        self.player = int(obs.get("player", 0) or 0)
        self.step = int(obs.get("step", 0) or 0)
        self.day = int(obs.get("day", 0) or 0)
        self.hour = int(obs.get("hour", 0) or 0)

        cfg = cfg or {}
        self.board = int(_cfg(cfg, "boardSize", 10))
        self.turns_per_day = max(1, int(_cfg(cfg, "turnsPerDay", 24)))
        self.shed_cap = int(_cfg(cfg, "shedCapacity", 100))
        self.max_orders = max(1, int(_cfg(cfg, "maxMarketOrdersPerTurn", 10)))
        self.hire_mult = int(_cfg(cfg, "farmHandCostMult", 1))
        self.episode_steps = int(_cfg(cfg, "episodeSteps", 720))
        # The framework fires DONE at episodeSteps-2, so that is the last acting turn.
        self.last_step = self.episode_steps - 2
        self.last_day = max(0, self.last_step // self.turns_per_day)

        farms = obs.get("farms") or []
        self.farm = farms[self.player] if self.player < len(farms) else {}
        self.money = float(self.farm.get("money", 0) or 0)
        self.tiles = self.farm.get("tiles") or []
        self.unlocked = list(self.farm.get("unlocked_quadrants") or ["NW"])
        self.hires_today = int(self.farm.get("hires_today", 0) or 0)

        self.positions = [list(self.farm.get("farmer") or [0, 0])]
        for h in (self.farm.get("hands") or []):
            self.positions.append(list(h))
        self.n_workers = len(self.positions)

        private = obs.get("private") or {}
        self.shed = dict(private.get("shed") or {})
        self.seeds = dict(private.get("seeds") or {})
        self.inv = [dict(v or {}) for v in (private.get("inventories") or [{}])]
        while len(self.inv) < self.n_workers:
            self.inv.append({})

        market = obs.get("market") or {}
        self.mkt_inv = dict(market.get("inventory") or {})
        override = market.get("params")
        if override:
            merged = dict((k, dict(v)) for k, v in MARKET_PARAMS.items())
            for k, patch in override.items():
                if k in merged and isinstance(patch, dict):
                    merged[k].update(patch)
            self.mparams = merged
        else:
            self.mparams = MARKET_PARAMS

        self.access = shed_access_tiles(self.board)
        self.open_access = [t for t in self.access
                            if quadrant_of(t[0], t[1], self.board) in self.unlocked]
        if not self.open_access:
            self.open_access = [self.access[0]]

        self.shed_used = sum(v for v in self.shed.values() if v > 0)

    def tile(self, x, y):
        try:
            return self.tiles[y][x]
        except (IndexError, TypeError):
            return "LOCKED"

    def produce_carried(self, w):
        """Everything except wheat, which is feed rather than cargo."""
        return sum(v for k, v in self.inv[w].items() if v > 0 and k != "WHEAT")

    def price(self, item):
        return market_price(item, self.mkt_inv.get(item, MARKET_I0), self.mparams)


# --------------------------------------------------------------------------- #
# The brain
# --------------------------------------------------------------------------- #

class Brain(object):

    def __init__(self):
        self.reset()

    def reset(self):
        # Movement is the single largest consumer of the action budget, so every
        # move is attributed to the decision that caused it. sim_harness reads this.
        self.diag = {"move_errand": 0, "move_haul": 0, "move_work": 0,
                     "move_steal": 0, "act": 0, "pass": 0}
        self.opp_supply = {}      # rival's visible pending supply, by product
        self.plan_key = None      # (day, n_workers) the current plan was built for
        self.route = []
        self.roles = {}           # (x, y) -> role string
        self.clusters = {}        # worker index -> [(x, y), ...]
        self.hire_target = 0
        self.want_land = False
        self.buy_animals = {}
        self.buy_seeds = {}
        self.counts = {}
        self.n_animals = 0
        self.free_tiles = 0
        self.last_step = -1

    # -- entry point -------------------------------------------------------- #
    def act(self, obs, cfg):
        st = TurnState(obs, cfg)
        if st.step <= self.last_step:      # a reused process starting a new episode
            self.reset()
        self.last_step = st.step

        # Livestock bought at hour 0 only lands in the shed at hour 1, so the stock
        # count is part of the key -- otherwise the day's clusters are built before
        # the animals exist and nothing ever goes out to place them.
        unplaced = sum(int(st.shed.get(s, 0) or 0) for s in ANIMALS)
        # NB: clusters are deliberately NOT rebalanced during the day. Tried it
        # (every 6 turns) and it cost 25% of the score: a worker fetches wheat sized
        # to *its* animals, so reassigning tiles mid-day leaves it holding feed for
        # animals it no longer visits, and animal losses doubled.
        key = (st.day, st.n_workers, unplaced)
        if key != self.plan_key:
            new_day = self.plan_key is None or self.plan_key[0] != st.day
            self.replan(st, new_day)
            self.plan_key = key

        unit_ops = self.execute_turn(st)
        orders = self.market_orders(st)
        return {
            "farmer": unit_ops[0] if unit_ops else ["PASS"],
            "hands": unit_ops[1:],
            "market": orders,
        }

    # ------------------------------------------------------------------ #
    # Daily planning
    # ------------------------------------------------------------------ #
    def replan(self, st, new_day=True):
        self.route = build_route(st.board, st.unlocked)
        self.opp_supply = self.opponent_supply(st)
        self.survey(st)
        self.plan_capital(st)
        self.assign_roles(st)
        self.plan_seeds(st)
        if new_day:
            # Only sized at hour 0. Re-estimating later in the day sees the jobs
            # already done and would talk itself out of hires it still needs.
            self.hire_target = self.plan_hires(st)
        self.assign_clusters(st)

    # -- 1. what have we got ------------------------------------------------ #
    def survey(self, st):
        counts = dict((k, 0) for k in list(CROPS) + list(ANIMALS))
        empty_struct = {"COOP": 0, "PASTURE": 0}
        free = []
        for (x, y) in self.route:
            t = st.tile(x, y)
            if t is None:
                free.append((x, y))
            elif isinstance(t, dict):
                kind = t.get("kind")
                if kind == "WEED":
                    free.append((x, y))
                elif kind == "PLANT":
                    counts[t["crop"]] = counts.get(t["crop"], 0) + 1
                elif "animal" in t:
                    counts[t["animal"]] = counts.get(t["animal"], 0) + 1
                elif kind in empty_struct:
                    empty_struct[kind] += 1
        self.counts = counts
        self.empty_struct = empty_struct
        self.free = free
        self.free_tiles = len(free)
        self.n_animals = sum(counts[a] for a in ANIMALS)

    # -- 2. what can we afford ---------------------------------------------- #
    def plan_capital(self, st):
        """Feed money first, then land, then livestock. Starving an animal is a
        total write-off, so its upkeep is reserved before anything is bought."""
        stock = dict((s, int(st.shed.get(s, 0) or 0)) for s in ANIMALS)
        unplaced = sum(stock.values())
        wheat_price = max(25, st.price("WHEAT"))

        cash = st.money - self.reserve_cash(st)
        cash -= self.feed_cash(st, self.n_animals + unplaced, wheat_price)

        # Land is only worth buying when tiles -- not labour -- are the binding
        # constraint. Extra ground we cannot staff just spreads the workers out and
        # makes every existing tile more expensive to reach.
        self.want_land = False
        n_extra = len(st.unlocked) - 1
        headroom = self.livestock_capacity(st) - self.n_animals
        if (n_extra < len(LAND_PRICES) and st.day <= PARAMS["land_last_day"]
                and self.free_tiles <= PARAMS["land_slack"]
                and headroom >= PARAMS["land_labour_headroom"]):
            price = LAND_PRICES[n_extra]
            reserve = PARAMS["land_reserve"].get(n_extra, 1500)
            if cash >= price + reserve:
                self.want_land = True
                cash -= price

        # Livestock compounds from its first yield day and never stops; seeds do not.
        # In the opening days animals therefore get first claim on cash, and only a
        # fraction of the seed bill is held back -- a cow bought on day 2 outproduces
        # the same cow bought on day 15 several times over.
        hold = self.seed_budget(st)
        if st.day <= PARAMS["early_animal_days"]:
            hold *= PARAMS["early_seed_hold_frac"]
        cash -= hold

        buy = {}
        owned = dict((s, self.counts[s] + stock[s]) for s in ANIMALS)
        n_total = self.n_animals + unplaced
        pending = unplaced
        capacity = self.livestock_capacity(st)

        def target_for(species):
            a = ANIMALS[species]
            # Derived rather than hardcoded, so a shorter episodeSteps doesn't leave
            # us buying $400 cows that never reach their first yield day.
            payback = a["first_yield_day"] + a["interval"] * PARAMS["animal_min_productions"]
            cutoff = min(PARAMS["animal_last_day"].get(species, 99),
                         st.last_day - payback)
            if st.day > cutoff:
                return 0
            # Per-species targets come from market depth; the herd as a whole is
            # capped by labour. An animal we cannot visit every day is worse than
            # no animal at all -- it eats wheat, blocks a tile, and then escapes.
            t = PARAMS["target_%s" % species.lower()]
            room = capacity - sum(owned[s] for s in ANIMALS if s != species)
            return max(0, min(t, room))

        # Buy the best earner first at *current* prices. If an opponent has already
        # crashed milk, cows stop being worth their tile and the money goes
        # elsewhere -- that adaptation is what survives a contested market.
        order = sorted(ANIMALS, key=lambda s: -self.animal_score(st, s))
        order = [s for s in order if self.animal_score(st, s) >= PARAMS["min_score"]]
        # Round-robin stops one species monopolising the budget, but early on it also
        # dilutes scarce capital into the weakest animal -- a $300 goose at $39/action
        # instead of most of a cow at $70. Only compete species that are close.
        if order:
            best = self.animal_score(st, order[0])
            cutoff = best * PARAMS["score_ratio_cutoff"]
            order = [s for s in order if self.animal_score(st, s) >= cutoff]

        # Round-robin rather than filling one species at a time: a sheep bought on
        # day 4 yields from day 10, one bought after the cow target fills does not.
        progress = True
        while progress and pending < PARAMS["max_unplaced_animals"]:
            progress = False
            for species in order:
                if pending >= PARAMS["max_unplaced_animals"]:
                    break
                if owned[species] >= target_for(species):
                    continue
                cost = ANIMALS[species]["cost"]
                upkeep = self.feed_cash(st, n_total + 1, wheat_price) - \
                    self.feed_cash(st, n_total, wheat_price)
                if cash < cost + upkeep:
                    continue
                cash -= cost + upkeep
                owned[species] += 1
                n_total += 1
                pending += 1
                buy[species] = buy.get(species, 0) + 1
                progress = True
        self.buy_animals = buy
        self.stock = stock
        # Whatever survives the feed reserve is all the seed budget there is. Without
        # this, seed orders quietly spend the money set aside to feed the animals we
        # just bought, and they starve two days later.
        self.spare_cash = max(0.0, cash)

    # -- profit per worker-action, at live prices --------------------------- #
    def marginal_price(self, st, item, pipeline):
        """Price once our own committed production -- and the rival's -- has sold.

        Scoring at today's quote makes a crop look equally good on its 1st and 40th
        tile; melon's quadratic glut curve punishes that badly. Pricing the marginal
        tile behind everything already in the ground is what stops the overshoot.

        The rival's herd counts too, and their farm is public. Ladder replays showed
        this agent finishing with 20-27 animals against 2-8 crops: milk and wool look
        wonderful early because nobody has supplied them yet, so it kept buying cows
        until every tile was livestock. When the opponent also ran animals both curves
        collapsed, and with no crops to fall back on our score fell to $28-58k in games
        we were otherwise winning. Supply we can see coming should discount the buy.
        """
        inv = st.mkt_inv.get(item, MARKET_I0) + max(0, int(pipeline))
        inv += int(self.opp_supply.get(item, 0) * PARAMS["rival_pipeline_weight"])
        return market_price(item, inv, st.mparams)

    def crop_score(self, st, crop):
        units, actions, _days = crop_yield(crop, st.last_day - st.day,
                                           fertilized=self.can_fertilize(st))
        if units <= 0 or actions <= 0:
            return -1e9
        if crop == "WHEAT" and self.n_animals > 0:
            # Home-grown feed is worth what we would otherwise pay for it, which is
            # the scarcity side of the curve -- far above wheat's sale price.
            price = market_price("WHEAT", st.mkt_inv.get("WHEAT", MARKET_I0) - 1,
                                 st.mparams)
        else:
            price = self.marginal_price(st, crop, self.counts.get(crop, 0) * units)
        return (units * price - CROPS[crop]["seed"]) / float(actions)

    def animal_score(self, st, species):
        a = ANIMALS[species]
        per_day = (1.0 + (a["interval"] if PARAMS["care"] else 0.0)) / a["interval"]
        fert_price = st.price("FERTILIZER")
        collect = fert_price >= PARAMS["fert_collect_min_price"]
        actions = 1.0 + (1.0 if PARAMS["care"] else 0.0) + 1.0     # feed, care, move
        actions += per_day / float(a["max_held"])                  # amortised harvest
        actions += 1.0 if collect else 0.0
        wheat = market_price("WHEAT", st.mkt_inv.get("WHEAT", MARKET_I0) - 1, st.mparams)
        horizon = min(PARAMS["score_horizon_days"], max(1, st.last_day - st.day))
        pipeline = self.counts.get(species, 0) * per_day * horizon
        value = per_day * self.marginal_price(st, a["product"], pipeline) - wheat
        if collect:
            value += fert_price
        return value / actions

    def opponent_supply(self, st):
        """Units per product the rival's *visible* farm is about to put on sale.

        `farms[1 - player]` is public: every tile, animal and standing yield. The
        premium curves are only ~60 units deep, so on a contested good the winner is
        simply whoever sells first. Knowing what they are about to flood is the
        difference between selling wool at $240 and at the floor.
        """
        farms = st.obs.get("farms") or []
        if len(farms) < 2:
            return {}
        opp = farms[1 - st.player] or {}
        out = {}
        for row in (opp.get("tiles") or []):
            if not isinstance(row, list):
                continue
            for t in row:
                if not isinstance(t, dict):
                    continue
                if t.get("kind") == "PLANT" and t.get("crop") in CROPS:
                    cd = CROPS[t["crop"]]
                    held = int(t.get("yield_units", 0) or 0)
                    out[t["crop"]] = out.get(t["crop"], 0) + max(held, cd["max_yield"] // 2)
                elif "animal" in t and t["animal"] in ANIMALS:
                    prod = ANIMALS[t["animal"]]["product"]
                    out[prod] = out.get(prod, 0) + 1 + int(t.get("yield_units", 0) or 0)
                    out["FERTILIZER"] = out.get("FERTILIZER", 0) + 1
        return out

    def reserve_cash(self, st):
        """Working float to keep back. Proportional as well as absolute, so a low
        `startingMoney` can't leave the reserve larger than the bank and stall the
        agent into buying nothing at all."""
        return min(PARAMS["cash_reserve"], st.money * PARAMS["cash_reserve_frac"])

    def labour_actions(self, st):
        """Useful (non-walking) actions per day from the workforce we can afford."""
        hands, spent = 0, 0
        budget = st.money * PARAMS["hire_cash_frac"]
        while hands < PARAMS["max_hands"]:
            cost = st.hire_mult * _fib(hands)
            if cost > PARAMS["max_hire_cost"] or spent + cost > budget:
                break
            spent += cost
            hands += 1
        return (hands + 1) * st.turns_per_day * PARAMS["useful_action_frac"]

    def livestock_capacity(self, st):
        """How many animals the workforce we can afford could actually service.

        Land is cheap and cash accumulates fast; the real ceiling is worker-actions,
        so herd size is derived from the hire budget rather than fixed in PARAMS.

        NB `crop_action_cost` is deliberately above the true per-tile figure from
        CROP_PROFILE (strawberry is really 0.82 actions/tile-day, not 1.6). Pricing
        crops honestly here was tried and measured *worse* -- 112.0k vs 115.5k -- and
        the extra headroom went to livestock rather than to the crops it was meant to
        fund. The inflated constant is doing duty as a farm-size governor, so it is
        left alone; see also `useful_action_frac`, which understates for the same
        reason.
        """
        crop_tiles = sum(self.counts.get(c, 0) for c in CROPS)
        actions = self.labour_actions(st) - crop_tiles * PARAMS["crop_action_cost"]
        return max(0, int(actions / PARAMS["animal_action_cost"]))

    def crop_capacity(self, st):
        """Crop tiles we can keep watered. A plant we skip twice is a weed, and a
        whole row planted on one day comes due for water on the same day."""
        actions = self.labour_actions(st) - self.n_animals * PARAMS["animal_action_cost"]
        return max(0, int(actions / PARAMS["crop_action_cost"]))

    def feed_cash(self, st, n_animals, wheat_price):
        days = min(PARAMS["feed_days_cover"], max(1, st.last_day - st.day))
        return n_animals * wheat_price * days

    def seed_budget(self, st):
        """Rough cost of the seeds the tile plan is about to want."""
        room = self.free_tiles
        cost = 0
        for crop in sorted(CROPS, key=lambda c: -CROPS[c]["seed"]):
            if not self.can_plant(st, crop):
                continue
            target = PARAMS["target_%s" % crop.lower()]
            n = max(0, min(room, target - self.counts.get(crop, 0)))
            cost += n * CROPS[crop]["seed"]
            room -= n
        return cost

    # -- 3. give every tile a job ------------------------------------------- #
    def assign_roles(self, st):
        roles = {}
        hub = set(st.open_access[:1])       # keep one shed tile clear for logistics
        for (x, y) in self.route:
            t = st.tile(x, y)
            if t == "LOCKED":
                continue
            if isinstance(t, dict):
                kind = t.get("kind")
                if kind == "PLANT":
                    roles[(x, y)] = t["crop"]
                    continue
                if "animal" in t:
                    roles[(x, y)] = t["animal"]
                    continue
            if (x, y) in hub:
                roles[(x, y)] = "HUB"

        # Structures only for livestock we actually hold or are buying this turn.
        need = {"COOP": 0, "PASTURE": 0}
        for s in ANIMALS:
            need[STRUCTURE_OF[s]] += self.stock[s] + self.buy_animals.get(s, 0)
        for k in need:
            need[k] = max(0, need[k] - self.empty_struct[k])

        # Surplus empty structures block the ground; hand them back to the crops.
        surplus = {}
        for k in ("COOP", "PASTURE"):
            held = sum(self.stock[s] + self.buy_animals.get(s, 0)
                       for s in ANIMALS if STRUCTURE_OF[s] == k)
            surplus[k] = max(0, self.empty_struct[k] - held - 1)

        want = {}
        for crop in CROPS:
            target = PARAMS["target_%s" % crop.lower()]
            if not self.can_plant(st, crop) or self.crop_score(st, crop) < PARAMS["min_score"]:
                want[crop] = 0
            else:
                want[crop] = max(0, target - self.counts.get(crop, 0))
        # Spare ground goes to whichever crop is currently worth the most per action.
        spare = self.free_tiles - sum(want.values()) - need["COOP"] - need["PASTURE"]
        if spare > 0:
            best = max(CROPS, key=lambda c: self.crop_score(st, c)
                       if self.can_plant(st, c) else -1e9)
            if self.can_plant(st, best) and self.crop_score(st, best) >= PARAMS["min_score"]:
                want[best] = want.get(best, 0) + min(spare, PARAMS["spare_tile_cap"])

        # Route order runs outward from the shed. Pack everything against the near
        # end so the farm stays compact -- an animal tile is visited four times a
        # day, so structures go first, then crops, and the far edge stays idle.
        # Cap new plantings by what we can water, and stagger them: 20 melons sown
        # on one day all fall due for water on the same day for the next 10 days.
        room = self.crop_capacity(st) - sum(self.counts.get(c, 0) for c in CROPS)
        burst = st.day <= PARAMS["plant_burst_days"]
        room = min(room, PARAMS["plant_burst_per_day"] if burst
                   else PARAMS["max_plant_per_day"])
        queue = ["BUILD_PASTURE"] * need["PASTURE"] + ["BUILD_COOP"] * need["COOP"]
        for crop in sorted(CROPS, key=lambda c: -self.crop_score(st, c)):
            take = max(0, min(want.get(crop, 0), room))
            queue += [crop] * take
            room -= take

        open_tiles = [t for t in self.free if t not in roles]
        idx = 0
        for t in open_tiles:
            roles[t] = queue[idx] if idx < len(queue) else "IDLE"
            idx += 1

        # Empty structures nobody is going to fill just block the ground; give them
        # back to the crops, but only while something is actually waiting for a tile.
        unmet = len(queue) - idx
        for (x, y) in self.route:
            t = st.tile(x, y)
            if isinstance(t, dict) and t.get("kind") in surplus and "animal" not in t:
                kind = t["kind"]
                if surplus[kind] > 0 and unmet > 0:
                    surplus[kind] -= 1
                    unmet -= 1
                    roles[(x, y)] = "CLEAR"
                else:
                    roles[(x, y)] = kind
        self.roles = roles

    def can_fertilize(self, st):
        """Do we have a fertilizer supply? Every surviving animal makes one a day."""
        return self.n_animals > 0 or int(st.shed.get("FERTILIZER", 0) or 0) > 0

    def can_plant(self, st, crop):
        """Only plant what will still be worth its tile by the last turn."""
        remaining = st.last_day - st.day
        units, actions, _days = crop_yield(crop, remaining)
        if units <= 0 or actions <= 0:
            return False
        return self.crop_score(st, crop) >= PARAMS["min_score"]

    def plan_seeds(self, st):
        want = {}
        for (x, y), role in self.roles.items():
            if role in CROPS and st.tile(x, y) is None:
                want[role] = want.get(role, 0) + 1
        self.buy_seeds = {}
        budget = getattr(self, "spare_cash", st.money)
        if st.day <= PARAMS["early_animal_days"]:
            # plan_capital only held back a fraction of the seed bill this early, so
            # allow the rest of the free cash to follow through.
            budget = max(budget, st.money - self.reserve_cash(st)
                         - self.feed_cash(st, self.n_animals, max(25, st.price("WHEAT"))))
        for crop in sorted(want, key=lambda c: -self.crop_score(st, c)):
            short = want[crop] - int(st.seeds.get(crop, 0) or 0)
            if short <= 0:
                continue
            seed = CROPS[crop]["seed"]
            afford = int(max(0.0, budget) // seed)
            take = min(short, afford)
            if take > 0:
                self.buy_seeds[crop] = take
                budget -= take * seed

    # -- 4. workforce -------------------------------------------------------- #
    def plan_hires(self, st):
        work = 0
        for (x, y), role in self.roles.items():
            jobs = self.expected_jobs(st, x, y, role)
            if jobs:
                work += jobs + PARAMS["travel_per_tile"]
        work = int(work * PARAMS["hire_slack"]) + 2 * max(1, st.n_workers)

        want = min(PARAMS["max_hands"],
                   max(0, int(math.ceil(work / float(st.turns_per_day))) - 1))
        # The Fibonacci curve makes the first several hands almost free -- 6 hands is
        # $20, 9 is $88 against a $3,000 bank. An idle hand wastes pocket change; a
        # missing one wastes 24 actions on a setup day. So take every hand whose own
        # price is a rounding error, regardless of what the work estimate says.
        # Capped against the bank as well as absolutely: with a small `startingMoney`
        # a flat $60 ceiling buys 9 hands out of a $200 bank and starves the farm of
        # seed money (measured: $41,987 -> $1,756).
        allowance = min(PARAMS["cheap_hire_cost"], st.money * PARAMS["cheap_hire_frac"])
        cheap, spent_cheap = 0, 0
        while cheap < PARAMS["max_hands"]:
            cost = st.hire_mult * _fib(st.hires_today + cheap)
            if cost > allowance or spent_cheap + cost > allowance:
                break
            spent_cheap += cost
            cheap += 1
        want = max(want, cheap)
        budget = st.money * PARAMS["hire_cash_frac"]
        n, spent = 0, 0
        while n < want:
            cost = st.hire_mult * _fib(st.hires_today + n)
            if cost > PARAMS["max_hire_cost"] or spent + cost > budget:
                break
            spent += cost
            n += 1
        return n

    def expected_jobs(self, st, x, y, role):
        """Rough count of actions this tile wants today (drives hiring + clusters)."""
        t = st.tile(x, y)
        if isinstance(t, dict) and "animal" in t:
            n = 2 + (1 if PARAMS["care"] else 0)          # feed, harvest-ish, care
            if st.price("FERTILIZER") >= PARAMS["fert_collect_min_price"]:
                n += 1
            return n
        if isinstance(t, dict) and t.get("kind") == "PLANT":
            # Must match what the executor will actually do. Counting an idle plant
            # as 1 pads clusters with no-op tiles, and the worker that draws them
            # walks its round, finds nothing, then idles or crosses the farm.
            crop = t["crop"]
            cd = CROPS[crop]
            n = 1 if self.plant_needs_water(st, t) else 0
            held = int(t.get("yield_units", 0) or 0)
            age = st.day - int(t.get("planted_day", st.day))
            if held > 0 and age >= cd["first_yield_day"]:
                mls = int(t.get("max_lifespan_step", -1))
                if cd["ongoing"]:
                    nxt = 2 if int(t.get("fertilized_until_day", -1)) >= st.day else 1
                    ripe = held + nxt > cd["max_yield"]
                else:
                    ripe = held >= cd["max_yield"] or age >= HARVEST_AGE[crop]
                if ripe or (0 <= mls <= st.step + st.turns_per_day) \
                        or st.day >= st.last_day - 1:
                    n += 1
            if int(t.get("fertilized_until_day", -1)) < st.day:
                if cd["ongoing"]:
                    if is_production_day(t, st.day):
                        n += 1
                elif age == (cd["max_yield_day"] + 1) // 2:
                    n += 1
            return n
        if isinstance(t, dict) and t.get("kind") == "WEED":
            return 1 if role not in ("IDLE", "HUB") else 0
        if isinstance(t, dict) and t.get("kind") in ("COOP", "PASTURE"):
            if role == "CLEAR" or self.placeable(st, t):
                return 1
            for s in ANIMALS:                       # arriving in the shed this turn
                if STRUCTURE_OF[s] == t.get("kind") and self.buy_animals.get(s):
                    return 1
            return 0
        if t is None and role not in ("IDLE", "HUB"):
            return 1
        return 0

    def placeable(self, st, tile):
        for s in ANIMALS:
            if STRUCTURE_OF[s] == tile.get("kind") and st.shed.get(s, 0):
                return True
        return False

    # -- 5. worker clusters -------------------------------------------------- #
    def assign_clusters(self, st):
        """Cut the route into chunks each worker can actually finish, then match
        chunks to workers by where they stand.

        Every worker starts at the shed, so a chunk 8 tiles out costs its owner a
        third of the day before any work happens. Splitting the route into chunks of
        *equal work* therefore overloads distant workers -- their tiles go untended --
        while near workers run dry and idle. Two changes:

          * a chunk is sized by `turns_per_day - approach - reserve`, i.e. the budget
            that worker will really have, and the walk between consecutive tiles is
            charged as it is accumulated;
          * chunks are then matched to workers by spawn proximity rather than by
            index, which matters once the outer quadrants unlock and the four shed
            access tiles sit on different sides of the farm.

        Chunks stay whole-day and contiguous: a worker draws feed sized to its own
        animals, so it has to keep them (see the note in `act`).
        """
        jobs = []
        for (x, y) in self.route:
            role = self.roles.get((x, y))
            if role is None:
                continue
            w = self.expected_jobs(st, x, y, role)
            if w > 0:
                jobs.append(((x, y), float(w)))

        n = max(1, st.n_workers)
        self.clusters = dict((i, []) for i in range(n))
        if not jobs:
            return

        # Equal-work contiguous chunks. Two alternatives measured worse and are
        # recorded so they aren't retried: sizing chunks purely by each worker's
        # reachable budget packs the work into the first few and leaves the rest
        # wandering (move_steal 10% -> 21%, score -32%); capping that by an equal
        # share still fragments the chunks (move_steal 16%, score -24%). Contiguous
        # runs of similar size beat any cleverer split I tried.
        total = sum(w for _t, w in jobs) + len(jobs)
        share = total / float(n)
        chunks = [[] for _ in range(n)]
        acc, ci = 0.0, 0
        for tile, w in jobs:
            chunks[ci].append(tile)
            acc += w + 1
            if acc >= share * (ci + 1) and ci < n - 1:
                ci += 1

        # Chunk i -> worker i, deliberately. Matching chunks to the nearest worker
        # instead costs 25%: replan fires several times a day (hands arriving,
        # livestock placed) and a position-based match re-shuffles ownership each
        # time, stranding workers holding feed drawn for animals they no longer
        # tend -- losses 18 -> 34, move_errand 5.8% -> 8.3%. Stability beats
        # optimality here, and every worker spawns at the shed anyway.
        for i, tiles in enumerate(chunks):
            self.clusters[i] = tiles


    # ------------------------------------------------------------------ #
    # Turn execution
    # ------------------------------------------------------------------ #
    def execute_turn(self, st):
        claimed = set()
        plant_budget = dict((c, int(st.seeds.get(c, 0) or 0)) for c in CROPS)
        shed_left = dict(st.shed)
        return [self.worker_op(st, w, claimed, plant_budget, shed_left)
                for w in range(st.n_workers)]

    def worker_op(self, st, w, claimed, plant_budget, shed_left):
        pos = tuple(st.positions[w])
        inv = st.inv[w]
        at_shed = pos in st.open_access
        cluster = self.clusters.get(w) or []

        # ---- 1. fetch feed / livestock from the shed ---------------------- #
        errand = self.shed_errand(st, w, cluster, inv, shed_left)
        if errand is not None:
            if at_shed:
                item, take = errand
                shed_left[item] = max(0, int(shed_left.get(item, 0) or 0) - take)
                self.diag["act"] += 1
                return ["PICKUP", item, take]
            mv = step_toward(pos, min(st.open_access, key=lambda t: manhattan(pos, t)))
            if mv:
                self.diag["move_errand"] += 1
                return [mv]

        # ---- 2. run produce to the shed before it overflows --------------- #
        haul = self.haul_op(st, w, pos, inv, at_shed, cluster)
        if haul is not None:
            return haul

        # ---- 3+4. nearest pending tile in our cluster, rescues first -------- #
        # Nearest rather than first-in-route-order: a worker standing at the far end
        # of its cluster should finish where it is, not walk back to the start.
        target = None
        for risky in (True, False):
            best, best_d = None, 10 ** 9
            for tile in cluster:
                if tile in claimed or (risky and not self.at_risk(st, tile)):
                    continue
                d = manhattan(pos, tile)
                if d < best_d and self.tile_ops(st, tile, w, plant_budget):
                    best, best_d = tile, d
            if best is not None:
                target = best
                break

        # ---- 5. cluster done -> help elsewhere, rescues first -------------- #
        if target is None:
            for risky in (True, False):
                best, best_d = None, 10 ** 9
                limit = st.board if risky else PARAMS["steal_radius"]
                for tile in self.route:
                    if tile in claimed or (risky and not self.at_risk(st, tile)):
                        continue
                    d = manhattan(pos, tile)
                    if d < best_d and d <= limit \
                            and self.tile_ops(st, tile, w, plant_budget):
                        best, best_d = tile, d
                if best is not None:
                    target = best
                    break

        if target is not None:
            claimed.add(target)
            if tuple(target) == pos:
                ops = self.tile_ops(st, target, w, plant_budget)
                if ops:
                    op = ops[0]
                    if op[0] == "PLANT":
                        plant_budget[op[1]] = plant_budget.get(op[1], 0) - 1
                    self.diag["act"] += 1
                    return op
            else:
                mv = step_toward(pos, target)
                if mv:
                    self.diag["move_work" if target in cluster else "move_steal"] += 1
                    return [mv]

        # ---- 6. genuinely idle -> empty our hands ------------------------- #
        out = self.haul_op(st, w, pos, inv, at_shed, cluster, force=True)
        if out is None:
            self.diag["pass"] += 1
            return ["PASS"]
        return out

    def at_risk(self, st, tile):
        """True if this tile turns into a weed / loses its animal tonight."""
        t = st.tile(tile[0], tile[1])
        if not isinstance(t, dict):
            return False
        if "animal" in t:
            return not t.get("fed_today") and int(t.get("consecutive_unfed", 0) or 0) >= 1
        if t.get("kind") == "PLANT":
            return (not t.get("watered_today")
                    and int(t.get("consecutive_unwatered", 0) or 0) >= 1)
        return False

    def shed_errand(self, st, w, cluster, inv, shed_left):
        """(item, qty) this worker still needs from the shed, or None."""
        unfed = 0
        want_animals = {}
        for (x, y) in cluster:
            t = st.tile(x, y)
            if not isinstance(t, dict):
                continue
            if "animal" in t:
                if not t.get("fed_today"):
                    unfed += 1
            elif t.get("kind") in ("COOP", "PASTURE") and self.roles.get((x, y)) != "CLEAR":
                for s in ANIMALS:
                    if STRUCTURE_OF[s] == t["kind"] and int(shed_left.get(s, 0) or 0) > 0:
                        want_animals[s] = want_animals.get(s, 0) + 1
                        break
        short = unfed - int(inv.get("WHEAT", 0) or 0)
        avail = int(shed_left.get("WHEAT", 0) or 0)
        if short > 0 and avail > 0:
            return ("WHEAT", min(short, avail))
        want_fert = self.fert_wanted(st, cluster)
        short = want_fert - int(inv.get("FERTILIZER", 0) or 0)
        avail = int(shed_left.get("FERTILIZER", 0) or 0)
        if short > 0 and avail > 0:
            return ("FERTILIZER", min(short, avail))
        for s, n in want_animals.items():
            have = int(inv.get(s, 0) or 0)
            avail = int(shed_left.get(s, 0) or 0)
            if have < n and avail > 0:
                return (s, min(n - have, avail))
        return None

    def fert_wanted(self, st, cluster):
        """Fertilizer this worker's tiles will consume today."""
        n = 0
        for (x, y) in cluster:
            t = st.tile(x, y)
            if not (isinstance(t, dict) and t.get("kind") == "PLANT"):
                continue
            if int(t.get("fertilized_until_day", -1)) >= st.day:
                continue
            cd = CROPS[t["crop"]]
            if cd["ongoing"]:
                if is_production_day(t, st.day):
                    n += 1
            elif st.day - int(t.get("planted_day", st.day)) == (cd["max_yield_day"] + 1) // 2:
                n += 1
        return n

    def fert_reserve(self, st):
        """Hold back what the standing ongoing crops will want over the next few days.

        A fertilizer spent on a strawberry is worth roughly $375 of extra fruit; the
        same unit sells for about $74. Selling the reserve would be a bad trade.
        """
        ongoing = sum(self.counts.get(c, 0) for c in CROPS if CROPS[c]["ongoing"])
        if ongoing <= 0:
            return 0
        return int(math.ceil(ongoing * PARAMS["fert_per_ongoing_tile"]))

    def haul_op(self, st, w, pos, inv, at_shed, cluster, force=False):
        """Move produce into the shed so it can be sold before the day rolls over."""
        carried = st.produce_carried(w)
        if carried <= 0:
            return None
        feeding = any(isinstance(st.tile(x, y), dict) and "animal" in st.tile(x, y)
                      and not st.tile(x, y).get("fed_today") for (x, y) in cluster)
        if not force:
            load = self.projected_load(st)
            # No end-of-day refresh happens inside the final day, so anything still
            # in a worker's hands on the last turn is simply lost.
            endgame = st.day >= st.last_day
            # Past capacity the surplus is destroyed tonight, which beats any
            # argument for staying in the field -- PLACE keeps the feed wheat.
            spilling = load > st.shed_cap
            if not (endgame or spilling):
                if feeding or load < st.shed_cap * PARAMS["haul_pressure"] \
                        or carried < PARAMS["drop_threshold"]:
                    return None
            elif carried < 2:
                return None
        if not at_shed:
            mv = step_toward(pos, min(st.open_access, key=lambda t: manhattan(pos, t)))
            if mv:
                self.diag["move_haul"] += 1
                return [mv]
            return None

        room = max(0, st.shed_cap - st.shed_used)
        if room <= 0:
            return None
        keep_wheat = feeding and inv.get("WHEAT", 0)
        total = sum(v for v in inv.values() if v > 0)
        # DROP deletes whatever doesn't fit; PLACE leaves the remainder in hand.
        if room >= total and not keep_wheat:
            return ["DROP"]
        best, best_val = None, -1.0
        for item, n in inv.items():
            if n <= 0 or item not in PRODUCTS or (item == "WHEAT" and keep_wheat):
                continue
            val = n * st.price(item)
            if val > best_val:
                best, best_val = item, val
        if best is None:
            return None
        return ["PLACE", best, min(inv[best], room)]

    def projected_load(self, st):
        """Everything that will be in the shed after tonight's automatic drop."""
        carried = 0
        for i in range(st.n_workers):
            carried += sum(v for v in st.inv[i].values() if v > 0)
        return st.shed_used + carried

    # -- what a tile wants, in priority order ------------------------------- #
    def tile_ops(self, st, tile, w, plant_budget):
        x, y = tile
        t = st.tile(x, y)
        if t == "LOCKED":
            return []
        role = self.roles.get((x, y), "IDLE")
        inv = st.inv[w]

        # ---- animals ------------------------------------------------------ #
        if isinstance(t, dict) and "animal" in t:
            a = ANIMALS[t["animal"]]
            ops = []
            # Prevent loss first: an animal already at 1 escapes tonight. On the
            # final day there is no end-of-day refresh, so feeding buys nothing.
            if not t.get("fed_today") and inv.get("WHEAT", 0) and st.day < st.last_day:
                ops.append(["FEED"])
            held = int(t.get("yield_units", 0) or 0)
            nxt = 1 + int(t.get("pending_care_bonus", 0) or 0)
            if held > 0 and (held + nxt > a["max_held"] or st.day >= st.last_day - 1):
                ops.append(["HARVEST"])
            if PARAMS["care"] and not t.get("cared_today") and self.care_pays(st, t):
                ops.append(["CARE"])
            if t.get("fertilizer_available") and \
                    (st.price("FERTILIZER") >= PARAMS["fert_collect_min_price"]
                     or self.fert_reserve(st) > int(st.shed.get("FERTILIZER", 0) or 0)):
                ops.append(["COLLECT_FERTILIZER"])
            if held > 0 and not ops:
                ops.append(["HARVEST"])
            return ops

        # ---- plants ------------------------------------------------------- #
        if isinstance(t, dict) and t.get("kind") == "PLANT":
            crop = t["crop"]
            cd = CROPS[crop]
            ops = []
            age = st.day - int(t.get("planted_day", st.day))
            held = int(t.get("yield_units", 0) or 0)
            if self.plant_needs_water(st, t):
                ops.append(["WATER"])
            if held > 0 and age >= cd["first_yield_day"]:
                mls = int(t.get("max_lifespan_step", -1))
                decaying = 0 <= mls <= st.step + st.turns_per_day
                ripe = held >= cd["max_yield"] or age >= HARVEST_AGE[crop]
                if cd["ongoing"]:
                    # Held units cap at max_yield, so harvest whenever tonight's
                    # production would clip -- 2 units if we have it fertilized.
                    nxt = 2 if int(t.get("fertilized_until_day", -1)) >= st.day else 1
                    ripe = held + nxt > cd["max_yield"]
                if ripe or decaying or st.day >= st.last_day - 1:
                    ops.append(["HARVEST"])
            if inv.get("FERTILIZER", 0) and int(t.get("fertilized_until_day", -1)) < st.day:
                # Ongoing crops double their scheduled production when watered and
                # fertilized. One application covers 3 days and so spans more than one
                # production; a fertilizer is worth far more here than the ~$74 it
                # fetches at market.
                if cd["ongoing"]:
                    if is_production_day(t, st.day):
                        ops.append(["FERTILIZE"])
                elif age == (cd["max_yield_day"] + 1) // 2:
                    ops.append(["FERTILIZE"])
            return ops

        # ---- weeds -------------------------------------------------------- #
        if isinstance(t, dict) and t.get("kind") == "WEED":
            return [["DIG"]] if role not in ("IDLE", "HUB") else []

        # ---- structures ---------------------------------------------------- #
        if isinstance(t, dict) and t.get("kind") in ("COOP", "PASTURE"):
            if role == "CLEAR":
                return [["DIG"]]
            for s in ("SHEEP", "COW", "GOOSE"):
                if STRUCTURE_OF[s] == t["kind"] and inv.get(s, 0):
                    return [["PLACE", s]]
            return []

        # ---- bare ground --------------------------------------------------- #
        if t is None:
            if role in CROPS:
                # A seed planted too late in the day can't be watered before the
                # refresh, and an unwatered seedling is a weed by morning.
                if st.hour > st.turns_per_day - 3:
                    return []
                if plant_budget.get(role, 0) > 0 and self.can_plant(st, role):
                    return [["PLANT", role]]
                return []
            if role in ("BUILD_COOP", "BUILD_PASTURE"):
                return [[role]]
        return []

    def plant_needs_water(self, st, t):
        if t.get("watered_today"):
            return False
        cd = CROPS[t["crop"]]
        age = st.day - int(t.get("planted_day", st.day))
        if int(t.get("consecutive_unwatered", 0) or 0) >= 1:
            return True                      # skip it and the plant is a weed tonight
        if cd["ongoing"]:
            # The fertilizer bonus only pays on a day the plant is also watered, so a
            # production day is worth the watering action even when survival isn't.
            return (int(t.get("fertilized_until_day", -1)) >= st.day
                    and is_production_day(t, st.day))
        window = (cd["max_yield_day"] + 1) // 2
        if window <= age <= cd["max_yield_day"] and \
                int(t.get("yield_units", 0) or 0) < cd["max_yield"]:
            return True                      # bonus window: watering *is* the yield
        return False

    def care_pays(self, st, t):
        """CARE banks +1 for the next production -- worthless if it can't be sold."""
        a = ANIMALS[t["animal"]]
        if st.day >= st.last_day - a["interval"]:
            return False
        return st.price(a["product"]) > MARKET_PARAMS[a["product"]]["base"] * 0.25

    # ------------------------------------------------------------------ #
    # Market
    # ------------------------------------------------------------------ #
    def market_orders(self, st):
        cap = st.max_orders
        head = []

        # Feed first -- a starved animal is a total write-off.
        feed = self.feed_order(st)
        if feed:
            head.append(feed)

        if st.hour <= 2 and self.hire_target > st.hires_today:
            want = self.hire_target - st.hires_today
            money = st.money
            for i in range(min(want, cap - 1 if st.hour == 0 else cap - 3)):
                cost = st.hire_mult * _fib(st.hires_today + i)
                if cost > money:
                    break
                money -= cost
                head.append(["HIRE"])

        if self.want_land and len(head) < cap:
            head.append(["BUY_LAND"])
            self.want_land = False

        sells = self.sell_orders(st)
        buys = []
        for species, n in sorted(self.buy_animals.items()):
            if n > 0:
                buys.append(["BUY_ANIMAL", species, n])
        for crop, n in sorted(self.buy_seeds.items()):
            if n > 0:
                buys.append(["BUY_SEED", crop, n])

        # Sells sit ahead of the discretionary buys so they fund them this turn.
        out = (head + sells + buys)[:cap]
        if any(o[0] == "BUY_ANIMAL" for o in out):
            self.buy_animals = {}
        if any(o[0] == "BUY_SEED" for o in out):
            self.buy_seeds = {}
        return out

    def sell_orders(self, st):
        # Pressure is a property of the whole farm, not just the shed: produce sitting
        # in workers' hands still has to fit tonight, and the overflow is destroyed.
        fullness = self.projected_load(st) / float(max(1, st.shed_cap))
        panic = fullness >= PARAMS["shed_panic"]
        out = []
        for item in PRODUCTS:
            have = int(st.shed.get(item, 0) or 0)
            if have <= 0:
                continue
            if item == "FERTILIZER" and not panic:
                have -= self.fert_reserve(st)
                if have <= 0:
                    continue
            if item == "WHEAT" and self.n_animals > 0:
                # We are structurally short of feed: a buy and a sell are quoted off
                # the same curve, so selling wheat only to buy it back later is pure
                # churn. Hold everything we grow -- even under shed pressure, where
                # dumping produce is the cheaper way to make room.
                continue
            if item == "WHEAT":
                have -= self.wheat_reserve(st)
                if have <= 0:
                    continue
            inv = st.mkt_inv.get(item, MARKET_I0)
            floor = max(self.sell_floor(st, item, fullness),
                        PARAMS["slip_per_turn"] * market_price(item, inv, st.mparams))
            n = units_above_floor(item, inv, floor, have, st.mparams)
            if panic:
                n = have                        # overflow is worth exactly $0
            if n > 0:
                out.append(["SELL", item, n])
        # Orders past maxMarketOrdersPerTurn are silently dropped, so when the cap
        # binds the contested, high-value goods have to be at the front of the queue.
        out.sort(key=lambda o: -(market_price(o[1], st.mkt_inv.get(o[1], MARKET_I0),
                                              st.mparams)
                                 * (1.0 + self.opp_supply.get(o[1], 0) / 20.0)))
        return out

    def sell_floor(self, st, item, fullness):
        base = MARKET_PARAMS[item]["base"]
        floor = (base * PARAMS["hold_frac"].get(item, PARAMS["hold_frac_default"])
                 * PARAMS["hold_frac_scale"])
        # Holding for a better price only pays if the town drain gets there first.
        # If the rival is sitting on a pile of the same good, waiting just means
        # selling behind them into a curve they have already flattened.
        rival = self.opp_supply.get(item, 0)
        if rival >= PARAMS["rival_supply_threshold"]:
            floor *= PARAMS["rival_floor_discount"]
        start = PARAMS["liquidate_start_day"]
        if st.day >= start:
            span = max(1, (st.last_day - start + 1) * st.turns_per_day)
            done = (st.day - start) * st.turns_per_day + st.hour
            t = min(1.0, done / float(span))
            floor = floor * (1.0 - t) + 1.0 * t
        if fullness >= PARAMS["shed_pressure_start"]:
            floor = min(floor, base * 0.18)
        return max(1.0, floor)

    def wheat_reserve(self, st):
        """Enough to feed everyone tomorrow morning, without hogging the shed."""
        if self.n_animals <= 0:
            return 0
        soft = min(self.n_animals * PARAMS["wheat_days_buffer"],
                   st.shed_cap * PARAMS["wheat_shed_frac"])
        return int(max(self.n_animals, soft))

    def feed_order(self, st):
        if self.n_animals <= 0 or st.day >= st.last_day:
            return None
        held = int(st.shed.get("WHEAT", 0) or 0)
        want = self.wheat_reserve(st) - held
        if want <= 0:
            return None
        want = min(want, max(0, st.shed_cap - st.shed_used))
        if want <= 0:
            return None
        inv = st.mkt_inv.get("WHEAT", MARKET_I0)
        if market_price("WHEAT", inv - 1, st.mparams) > PARAMS["wheat_buy_price_cap"]:
            # Too dear to stockpile, but a starving animal escapes: buy the minimum.
            want = min(want, max(0, self.n_animals - held))
            if want <= 0:
                return None
        budget = max(0.0, st.money - self.reserve_cash(st) * 0.5)
        n, _ = affordable_units("WHEAT", inv, budget, want, st.mparams)
        return ["BUY_PRODUCT", "WHEAT", n] if n > 0 else None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

BRAIN = Brain()


def agent(observation, configuration=None):
    """A crash forfeits the episode, so this can never propagate an exception."""
    global BRAIN
    try:
        return BRAIN.act(observation, configuration)
    except Exception:
        try:
            BRAIN = Brain()
        except Exception:
            pass
        return dict(SAFE_ACTION)
