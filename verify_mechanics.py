"""Throwaway: assert the subtle mechanics directly against the env implementation."""
from kaggle_environments.envs.kaggriculture import kaggriculture as K

R = []


def check(name, got, expect):
    R.append((name, got, expect, "OK" if got == expect else "MISMATCH"))


# ---- 1. CARE bonus size, and whether an unfed animal still produces -------------
def fresh_goose_farm(day=0):
    farm = {"tiles": [[None] * 10 for _ in range(10)]}
    farm["tiles"][0][0] = K._new_animal("GOOSE", day)
    return farm


# Goose: first_yield_day 4, interval 1. Place day 0; production starts next_day>=4.
farm = fresh_goose_farm(0)
g = farm["tiles"][0][0]
for d in range(0, 6):
    g["fed_today"] = True
    g["cared_today"] = True
    K._daily_refresh_animals(farm, d)
    g = farm["tiles"][0][0]
    g["yield_units"] = 0  # harvest every day so max_held never clips
check("goose care bonus per fed+cared day (README says 2)", g["pending_care_bonus"], 1)

# Yield on a fed+cared production day, with 1 day of banked bonus.
farm = fresh_goose_farm(0)
g = farm["tiles"][0][0]
g["fed_today"] = True; g["cared_today"] = True
K._daily_refresh_animals(farm, 3)          # next_day=4 -> first production, bank was 0
first = farm["tiles"][0][0]["yield_units"]
farm["tiles"][0][0]["yield_units"] = 0
farm["tiles"][0][0]["fed_today"] = True; farm["tiles"][0][0]["cared_today"] = True
K._daily_refresh_animals(farm, 4)          # bank of 1 from prior day pays out
second = farm["tiles"][0][0]["yield_units"]
check("goose yield, production day #1 (empty bank)", first, 1)
check("goose yield, production day #2 (bank=1)", second, 2)

# Unfed on a production day: README says "no yield is produced".
farm = fresh_goose_farm(0)
farm["tiles"][0][0]["fed_today"] = False
K._daily_refresh_animals(farm, 3)
check("UNFED goose yield on production day (README says 0)",
      farm["tiles"][0][0]["yield_units"], 1)
check("UNFED goose consecutive_unfed", farm["tiles"][0][0]["consecutive_unfed"], 1)

# fertilizer_available: README tile doc says "set after CARE".
farm = fresh_goose_farm(0)
farm["tiles"][0][0]["fed_today"] = False
farm["tiles"][0][0]["cared_today"] = False
K._daily_refresh_animals(farm, 0)
check("fertilizer_available after a day with NO care", farm["tiles"][0][0]["fertilizer_available"], True)

# Animals have no lifespan / cumulative production cap.
farm = fresh_goose_farm(0)
total = 0
for d in range(0, 30):
    farm["tiles"][0][0]["fed_today"] = True
    K._daily_refresh_animals(farm, d)
    t = farm["tiles"][0][0]
    total += t["yield_units"]
    t["yield_units"] = 0
check("goose lifetime eggs over 30 days, fed only (no cap expected)", total, 26)

# ---- 2. FERTILIZER sellable? README: "can only be bought, not sold" -------------
check("FERTILIZER in PRODUCTS (so SELL passes the item filter)", "FERTILIZER" in K.PRODUCTS, True)
farm = {"money": 0.0}
priv = {"shed": {"FERTILIZER": 3}}
mkt = K._new_market()
ok = K._commit_unit("SELL", "FERTILIZER", mkt["prices"]["FERTILIZER"], farm, priv, mkt)
check("SELL FERTILIZER commits", ok, True)
check("money after selling 1 fertilizer at base", farm["money"], 100.0)
check("FERTILIZER excluded from town-center demand", "FERTILIZER" in K.TOWN_CENTER_PRODUCTS, False)

# ---- 3. Watering every other day keeps a plant alive ----------------------------
farm = {"tiles": [[None] * 10 for _ in range(10)]}
farm["tiles"][0][0] = K._new_plant("WHEAT", 0, 24)
alive = True
for d in range(0, 10):
    # water only on even days
    farm["tiles"][0][0]["watered_today"] = (d % 2 == 0)
    K._daily_refresh_plants(farm, d, 24)
    if farm["tiles"][0][0].get("kind") != "PLANT":
        alive = False
        break
check("wheat survives watering every OTHER day for 10 days", alive, True)

# Skipping the planting day kills it.
farm = {"tiles": [[None] * 10 for _ in range(10)]}
farm["tiles"][0][0] = K._new_plant("WHEAT", 0, 24)
check("new plant starts at consecutive_unwatered", farm["tiles"][0][0]["consecutive_unwatered"], 1)
K._daily_refresh_plants(farm, 0, 24)  # unwatered on planting day
check("unwatered on planting day -> weed", farm["tiles"][0][0].get("kind"), "WEED")

# ---- 4. Max realistic wheat yield & the action cost to get it -------------------
def run_wheat(water_days, fertilize_on=None, harvest_age=4):
    farm = {"tiles": [[None] * 10 for _ in range(10)], "farmer": [0, 0]}
    farm["tiles"][0][0] = K._new_plant("WHEAT", 0, 24)
    priv = {"shed": {}, "seeds": {}, "inventories": [{"FERTILIZER": 5}]}
    acts = 1  # the PLANT action
    for d in range(0, harvest_age + 1):
        t = farm["tiles"][0][0]
        if t.get("kind") != "PLANT":
            return None, None
        if fertilize_on is not None and d == fertilize_on:
            K._apply_unit_action(farm, priv, 0, ["FERTILIZE"], 10, d, 24); acts += 1
        if d in water_days:
            K._apply_unit_action(farm, priv, 0, ["WATER"], 10, d, 24); acts += 1
        if d == harvest_age:
            y = farm["tiles"][0][0]["yield_units"]
            K._apply_unit_action(farm, priv, 0, ["HARVEST"], 10, d, 24); acts += 1
            return y, acts
        K._daily_refresh_plants(farm, d, 24)
    return None, None


check("wheat: water {0,2,3,4}, harvest age 4 -> (units, actions)", run_wheat({0, 2, 3, 4}), (4, 6))
check("wheat: same + FERTILIZE at age 2 -> (units, actions)", run_wheat({0, 2, 3, 4}, fertilize_on=2), (6, 7))
check("wheat: minimal water {0,2}, harvest age 2 -> (units, actions)", run_wheat({0, 2}, harvest_age=2), (2, 4))

# Carrot: max_yield_day 3, window starts at ceil(3/2)=2
def run_carrot():
    farm = {"tiles": [[None] * 10 for _ in range(10)], "farmer": [0, 0]}
    farm["tiles"][0][0] = K._new_plant("CARROT", 0, 24)
    priv = {"shed": {}, "seeds": {}, "inventories": [{}]}
    acts = 1
    for d in range(0, 4):
        if d in (0, 2, 3):
            K._apply_unit_action(farm, priv, 0, ["WATER"], 10, d, 24); acts += 1
        if d == 3:
            y = farm["tiles"][0][0]["yield_units"]
            K._apply_unit_action(farm, priv, 0, ["HARVEST"], 10, d, 24); acts += 1
            return y, acts
        K._daily_refresh_plants(farm, d, 24)


check("carrot: water {0,2,3}, harvest age 3 -> (units, actions)", run_carrot(), (3, 5))

# ---- 5. Market curve sanity vs the README table ---------------------------------
for item, T in (("WHEAT", 400), ("CARROT", 450), ("MELON", 300), ("MILK", 122), ("WOOL", 105)):
    lo = K.market_price(item, K.MARKET_I0 - T)
    hi = K.market_price(item, K.MARKET_I0 + T)
    R.append((f"price {item}: P(I0-T)={lo} P(I0+T)={hi}", "", "", "info"))

# Units sellable before hitting the $1 floor, from a fresh market.
for item in K.PRODUCTS:
    n = 0
    inv = K.MARKET_I0
    while K.market_price(item, inv) > 1 and n < 20000:
        inv += 1
        n += 1
    R.append((f"units to floor {item}", n, "", "info"))

# ---- 6. Hand spawn tiles vs locked quadrants ------------------------------------
farm = K._new_farm(10, 3000)
spawns = []
for _ in range(5):
    farm["hands"].append(K._spawn_hand(farm, 10))
    spawns.append(tuple(farm["hands"][-1]))
check("first 5 hand spawn tiles", spawns, [(5, 4), (4, 5), (5, 5), (4, 4), (5, 4)])
check("spawn (5,4) is LOCKED at start", farm["tiles"][4][5], "LOCKED")

# ---- 7. Hire cost curve ----------------------------------------------------------
cum, tot = [], 0
for n in range(20):
    tot += K._hire_cost(n, 1)
    cum.append(tot)
R.append(("cumulative cost to hire 1..20 hands (mult=1)", cum, "", "info"))

w = max(len(r[0]) for r in R)
bad = 0
for name, got, exp, status in R:
    if status == "info":
        print(f"  ..  {name.ljust(w)}  {got}")
    else:
        if status != "OK":
            bad += 1
        print(f"[{status:^8}] {name.ljust(w)}  got={got!r} expected={exp!r}")
print(f"\n{bad} mismatch(es) out of {sum(1 for r in R if r[3] != 'info')} checks")
