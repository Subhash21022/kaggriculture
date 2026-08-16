# Kaggriculture, explained from scratch

A beginner's guide to the game, to the agent in `main.py`, and to the method used to
improve it. No prior knowledge assumed.

---

# Part 1 — What is this game?

## The one-sentence version

Two players each run a farm for 30 in-game days. Whoever has the most **coins in the
bank** at the end wins. Things you never sold are worth **zero**.

## The board

Each player has a private 10×10 grid of tiles, split into four 5×5 quadrants:

```
        NW        NE
      . . . . | . . . . .        You start owning only NW (25 tiles).
      . . . . | . . . . .        The other three are LOCKED and cost
      . . . .[S]. . . . .   <-- shed sits in the middle
      . . . . | . . . . .        $1,000 / $2,000 / $4,000 to unlock.
        SW    |   SE
```

The **shed** is at the centre. It's your warehouse: harvested goods live there, and
**you can only sell things that are in the shed**. It holds a maximum of **100 items** —
anything over that at the end of the day is destroyed.

## Time

- 1 day = **24 turns**
- 1 season = **30 days** = 720 turns
- You actually act on turns 0–718 (719 of them; the last turn never happens)

## Your workers

You have **one farmer**, permanently. You can also **hire farm hands** each morning.
Hands vanish at the end of each day, so you re-hire every morning.

Hiring is cheap at first and gets expensive fast (a Fibonacci curve):

| hand # | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 | 12 | 14 |
|---|---|---|---|---|---|---|---|---|---|---|
| cost of *that* hand | $1 | $1 | $2 | $3 | $5 | $8 | $21 | $55 | $144 | $377 |

**Every worker does exactly ONE action per turn.** That's the single most important rule
in the game — see Part 2.

## What a worker can do

One of these, per worker, per turn:

- **Move** one tile: `NORTH` / `SOUTH` / `EAST` / `WEST`
- **Plant** a seed, **water** a plant, **harvest** it, **fertilize** it
- **Build** a coop or pasture, **place** an animal on it
- **Feed** an animal, **care** for it, **collect fertilizer** from it
- **Dig** (clear a weed or an old plant)
- **Pickup** from the shed / **drop** into it
- **Pass** (do nothing)

## What grows

**Crops** — you buy a seed, plant it, water it, harvest it.

| crop | seed cost | days to harvest | units you get | base price |
|---|---|---|---|---|
| Wheat | $10 | 4 | 4 | $25 |
| Carrot | $20 | 3 | 3 | $35 |
| Melon | $80 | 10 | 6 | **$250** |
| Strawberry | $100 | 17 | 4 (8 if fertilized) | $120 |
| Tomato | $50 | 12 | 4 (8 if fertilized) | $60 |

**Animals** — you buy them, build a home, place them, then feed them wheat every day.
They then produce **forever** until the season ends.

| animal | cost | home | produces | first yield | base price |
|---|---|---|---|---|---|
| Goose | $300 | coop | eggs | day 4 | $50 |
| Cow | $400 | pasture | milk | day 8 | **$160** |
| Sheep | $500 | pasture | wool | day 6 | **$200** |

Every animal also produces **1 fertilizer per day**, free, if you spend an action
collecting it.

## The two rules that kill beginners

1. **A plant not watered for 2 days in a row turns into a weed.** You lose everything
   invested in it. (Watering *every other* day is enough to survive — you only need to
   water daily during its "bonus window", which is what actually grows the yield.)
2. **An animal not fed for 2 days in a row escapes forever.** A $500 sheep, gone.

So an animal you can't feed is worse than no animal at all.

## The market — the part that's genuinely tricky

Prices are **not fixed**. Each product has a price that moves with supply:

- **You sell → the price drops.**
- **The town buys → the price recovers.**

Both players sell into **the same shared market**. If your opponent dumps 300 wool, wool
is worthless for you too.

How far a price falls varies enormously by product. Selling this many units from a fresh
market before the price hits the $1 floor:

| WHEAT | EGG | CARROT | TOMATO | FERTILIZER | MELON | MILK | STRAWBERRY | WOOL |
|---|---|---|---|---|---|---|---|---|
| 20,000+ | 20,000+ | 842 | 529 | 493 | 158 | 76 | 62 | 59 |

**Wool collapses after 59 units. Wheat basically never collapses.** That difference
drives the whole strategy.

The saving grace: the **town** continuously buys produce, which pushes prices back up.
Shops unlock every 3 days, and each one eats a fixed amount per day forever after. So
the *sustainable* selling rate is roughly the town's buying rate.

## The bit almost everyone misses

**Market orders are FREE.** Buying and selling do **not** cost your workers any actions —
you get up to 10 orders every single turn regardless. Only *farm* work costs actions.

This means: if you need wheat to feed animals, **buying it costs money but zero actions**,
while growing it costs a tile *and* several actions. Money is usually easier to get than
actions.

---

# Part 2 — The one idea the whole agent is built on

> **Actions are the real currency, not coins.**

Here's the arithmetic. Each worker gets 24 actions per day. Suppose you have a farmer plus
10 hands — that's 11 × 24 = **264 actions per day**.

Now count what things cost, *per day*:

- An animal needs: feed (1) + care (1) + collect fertilizer (1) + harvest (~0.4) ≈ **3.4 actions/day**
- A crop tile needs roughly **1 action/day**
- **Plus walking.** A worker in the far corner spends 8 turns just getting there.

So 264 actions supports maybe 30 animals and 20 crops — *if nobody wastes time walking*.
In practice **~55–60% of all actions in this agent are movement**. That leaves only about
110 actions of real work.

Land is cheap ($7,000 buys the whole board). Money compounds quickly. **Labour is the
thing you actually run out of.** So the question the agent asks about every tile is:

> How many dollars does this tile earn per worker-action?

Measured at real in-game prices:

| what you do with a tile | $ per action |
|---|---|
| Melon | **$142** |
| Cow (+ care) | $95 |
| Sheep (+ care) | $91 |
| Strawberry, fertilized | $86 |
| Collect fertilizer | $74 |
| Wheat grown *as animal feed* | $32 |
| Goose | $32 |
| Carrot | $17 |
| Wheat grown *to sell* | $15 |

Two things here surprise people:

- **Geese are the worst animal**, even though they're cheapest and pay earliest. Eggs sit
  near their base price, while nobody supplies milk and wool so those trade at 1.5–2×
  base.
- **Wheat is worth twice as much as feed than as a product.** If you grow wheat and eat
  it, you avoid *buying* it at ~$50. If you sell it, you get ~$25. Same wheat, double the
  value, just by not selling it.

---

# Part 3 — How the agent is built

The whole thing is one file, `main.py`, standard library only. Kaggle calls one function
each turn:

```python
def agent(observation, configuration):
    # observation = everything you can see this turn
    # return one action per worker, plus market orders
    return {
        "farmer": ["WATER"],                  # what the main farmer does
        "hands":  [["FEED"], ["HARVEST"]],    # one action per hired hand
        "market": [["SELL", "MILK", 12]],     # up to 10 market orders
    }
```

That's it. It's called 719 times and the farm state persists between calls.

It's organised in **layers**, so each piece can be tested alone:

```
observation
    |
    v
[1] TurnState        -- tidy up the raw data into something readable
    |
    v
[2] Market model     -- "if I sell 20 wool, what price do I get?"
    |
    v
[3] Daily planner    -- runs ONCE per day, decides the big stuff:
    |                     how many hands to hire, what to buy,
    |                     which tile does what, who works where
    v
[4] Turn executor    -- runs EVERY turn: one concrete action per worker
    |
    v
[5] Market executor  -- what to buy and sell this turn
    |
    v
action
```

### Why plan once a day instead of every turn?

Speed (there's a 1-second-per-turn limit) but mostly **stability**. I learned this the
hard way — see the traps in Part 5.

### The turn executor's priority order

Every turn, each worker asks in order:

1. Do I need to fetch wheat/fertilizer from the shed? → go get it
2. Is the shed about to overflow? → go dump what I'm carrying
3. Is anything in my area about to **die** tonight? → save it first
4. Otherwise: work the nearest unfinished tile in my area
5. My area's done → help nearby
6. Nothing to do → pass

"Prevent loss first" matters because a dead animal is a total write-off, whereas a
harvest done tomorrow is merely late.

### The safety net

```python
def agent(observation, configuration=None):
    try:
        return BRAIN.act(observation, configuration)
    except Exception:
        return {"farmer": ["PASS"], "hands": [], "market": []}
```

**A crash forfeits the entire game.** So the whole agent is wrapped in a try/except that
falls back to doing nothing. Over hundreds of games it has never once fired, but it costs
nothing to have.

---

# Part 4 — How I actually improve it

This is the part worth copying, more than any of the farming specifics.

## Step 1: Read the source, don't trust the docs

The competition ships a README *and* the actual game code (`kaggriculture.py`). I read
the code and tested my understanding against it. **The docs were wrong in several
places**, and one of those was worth real money:

> README: *"Fertilizer can only be bought, not sold."*
> Reality: the code sells it happily, for $100 a unit.

Every competitor who trusted the README left that on the table.

Lesson: **when there's a source of truth, check it yourself.**

## Step 2: Measure, don't guess

`sim_harness.py` plays full games and prints what happened:

```
day    money  move%  wast%  shed  hands  tiles
12     12207   62.4    0.0    15      3  cow=1, goose=5, sheep=7, melon=14 ...
```

Plus realised sale prices, invariant violations ("a plant died from thirst on day 12"),
and where every single action went.

That last one mattered most. Instead of guessing why the farm was slow, I **labelled
every move by its cause**:

```
act=30.3%  move_work=48.6%  move_steal=8.0%  move_haul=4.9%  pass=4.2%
```

Half of all actions were workers walking to their own tiles. The cause turned out to be
one line: workers walked to the *first* tile on their list rather than the *nearest* one,
so someone at the far end would trudge all the way back to the start. Fixing that was
worth **+68% on one test** — more than every market improvement combined.

I would never have found that by thinking about it. The diagnostic found it.

## Step 3: Change ONE thing, then measure again

Every idea gets a number before and after. Here's the actual history:

| change | score |
|---|---|
| First working version | $40,500 |
| Reserve cash for feed before buying animals | $69,800 |
| Stop selling wheat while I own animals | $92,000 |
| Decide hiring once, at dawn | $96,800 |
| **Walk to the nearest tile, not the first** | **$77,000 → then $126,000 with later fixes** |

The wheat one is a nice example of a bug you'd never spot by reading code. The diagnostics
showed I bought 951 wheat for $41,000 while selling 495 for $22,000 — buying and selling
the *same thing* all game, losing $19,000 to nothing but churn.

## Step 4: Test against a realistic opponent

This is the mistake I made for a long time, and it cost the most.

I tuned by playing my agent against **a copy of itself**, and against weak built-in bots.
Those bots **never sell anything**, so prices stayed high all game and my farm always
looked fine.

Then I downloaded the actual game recordings ("replays") from the real competition — and
found this:

| day 0 | strong opponent | me |
|---|---|---|
| tiles planted | 12 melons | **0** |
| cows | 2 | 0 |
| money left | $10 | **$1,800 sitting idle** |

They spent everything immediately; I dithered. A cow bought on day 2 earns for 20 days; a
cow bought on day 21 earns once. Fixing my opening was worth **+13%**, and I only found it
because I looked at what real opponents did.

**Lesson: if you tune against yourself, you can only find bugs you don't already have.**

## Step 5: Keep the failures written down

Roughly half of everything I tried made the agent *worse*. Those are recorded in
`strategy_notes.md` with their numbers, so they don't get retried:

- Rebalancing worker assignments mid-day: **−25%**
- Four different smarter routing schemes: **all worse**
- Giving idle workers a backup area: **worse at every setting**
- "Fixing" a cost constant that is provably wrong: **worse**

Negative results are real results. Writing them down is how you stop going in circles.

---

# Part 5 — Three traps worth knowing

These generalise well beyond this game.

### 1. Optimising a ratio instead of the goal

The diagnostics show `act%` — the share of actions doing real work. It's tempting to
maximise it. **Every single change that improved that percentage lost money.**

Hiring more workers *lowers* the percentage (more people means more walking) but *raises*
the total work done. The goal was coins, not efficiency:

| hire aggressiveness | score | `act%` |
|---|---|---|
| low | $99,000 | 40.1% |
| **medium** | **$133,000** | 34.0% |
| high | $128,000 | 23.4% |

The best setting has a *worse* efficiency ratio than the worst setting.

### 2. Idle time isn't always waste

Workers were passing ~10% of turns. I sent them to help elsewhere. `pass` dropped to 5% —
and the score fell.

Why: a worker standing in its own area is **in position** when one of its animals produces
or a plant ripens. A worker that wandered off isn't. The idling was *slack that absorbs
unpredictable work*, not waste.

### 3. Coupled state punishes clever reassignment

Each worker collects wheat in the morning sized to *its own* animals. I tried
reassigning workers to balance the load better. Animal deaths doubled — because a worker
would get moved to a new area while still holding feed for animals nobody was now
visiting.

**Stability beat optimality.** I hit this same trap twice by two different routes before
writing it into the code as a comment.

---

# Part 6 — Where it ended up

Leaderboard rating over four submissions: **653 → 799 → 825 → 875**.

Every one of those gains came from reading real game replays. Nothing I invented purely
from local testing survived contact with the data.

The remaining gap: the very top agents score ~$150,000 against my ~$115,000. I know *what*
they do (about 35–40 strawberry tiles and 13 hires a day) but couldn't make my agent
execute it — my workers can't service that many tiles, and every attempt to lift that
ceiling failed. That's an honest limitation, not a to-do list.

Also worth knowing: the agents at the very top of the board (rating ~2,800) aren't farming
better at all. They **replay a copied recording** of someone else's game and exploit the
fact that many opponents are running the same copy. That's a different game from the one
this agent plays.

---

# If you want to try this yourself

```bash
pip install kaggle-environments
```

```python
from kaggle_environments import make

env = make("kaggriculture", debug=True)
env.run(["main.py", "random"])      # your agent vs the random bot
print([s.reward for s in env.steps[-1]])
```

The files in this project:

| file | what it is |
|---|---|
| `main.py` | **the agent** — the only file you submit |
| `sim_harness.py` | test runner + diagnostics |
| `phase1_agent.py` | a deliberately simple bot, used as a measuring stick |
| `verify_mechanics.py` | checks my understanding of the rules against the real code |
| `strategy_notes.md` | the full technical log, including everything that failed |

A good first project: open `main.py`, find the `PARAMS` dictionary at the top, change one
number, and run the harness to see what happens. That is exactly how everything above was
built.
