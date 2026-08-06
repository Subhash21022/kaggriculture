# kaggriculture
# Kaggriculture — strategy notes

What I read, what I tried, what the numbers said, and what I'd tune next.

---

## 1. Where the source disagrees with the docs

Everything below was verified by driving the real interpreter (`verify_mechanics.py`),
not by reading. These are the differences that change strategy:

| # | Docs say | `kaggriculture.py` actually does | Why it matters |
|---|---|---|---|
| 1 | "Fertilizer can only be bought, not sold" (README:185) | `FERTILIZER` is in `PRODUCTS`, so `SELL` passes the filter at `:571` and pays out | **The single biggest edge.** Fertilizer is also the only product with *zero* town drain, so its curve never recovers — a one-shot ~493-unit pool worth ~$25k. Every competitor reading the README will leave it on the table. |
| 2 | `CARE` banks +2 (README:69) | `:800` adds **+1** | Halves the expected `CARE` return; still worth it (see §3). |
| 3 | Unfed animal produces nothing (README:70) | `:794-797` applies `base = 1` regardless; only the *bonus* is withheld | A one-day feed miss is survivable, not catastrophic. Two is fatal. |
| 4 | `fertilizer_available` "set after CARE" (README:305) | `:801` sets it every end-of-day for every surviving animal | Fertilizer income is independent of `CARE`. |
| 5 | — | Animals have **no lifespan and no cumulative production cap** (unlike ongoing crops) | A cow bought on day 6 yields until the season ends. Livestock is a perpetuity; crops are not. |
| 6 | "must be watered every day" | Death is at `consecutive_unwatered >= 2` | Outside the bonus window you can water **every other day**. Roughly halves crop upkeep. The planting day is the exception — `_new_plant` starts at 1, so skipping it kills the seedling that night. |
| 7 | — | `_spawn_hand` ignores `unlocked_quadrants`; `_apply_unit_action` returns early on `LOCKED` (`:323`) | Hands spawn on locked shed tiles where `DROP`/`PICKUP` silently fail. They must step to `(4,4)` first. |
| 8 | — | `DROP` **deletes** whatever exceeds `shedCapacity`; `PLACE` leaves the remainder in hand | Use `PLACE` when the shed is nearly full. |

Two timing facts the docs don't state: the agent acts on steps **0..718** (719 turns,
not 720), and **day 29 has no end-of-day refresh** — the last one fires at step 695.
So anything still in a worker's hands on the final day is lost, and feeding on day 29
buys nothing.

## 2. The market, quantitatively

Units sellable from a fresh market before hitting the $1 floor, and the daily town
drain once all eight shops are open:

| | WHEAT | EGG | CARROT | TOMATO | FERT | MELON | MILK | STRAW | WOOL |
|---|---|---|---|---|---|---|---|---|---|
| units to floor | 20000+ | 20000+ | 842 | 529 | 493 | 158 | 76 | 62 | 59 |
| town drain/day | 30 | 12 | 18 | 12 | **0** | **0** | 18 | 24 | 12 |
| season absorption | ~635 | ~338 | ~437 | ~338 | **0** | ~140 | ~437 | ~536 | ~338 |

Two regimes. Wheat and egg use log curves and are effectively bottomless. Everything
premium floors after ~60 units — but the town drains it continuously, so **the
sustainable sale rate is the drain rate**, and selling at that rate holds the base
price indefinitely. Melon is the interesting case: no shop ever wants it, so its only
support is the town centre's 140/season, but its base is $250.

The decisive empirical finding: across every run, **every product's price sat above
base all game**. We were never the marginal supplier — the town drains faster than one
farm can produce. That reframes the whole game from "don't crash the market" to
"produce as much as physically possible".

## 3. Value per worker-action — the number that decides everything

Land is cheap ($7k for all of it) and cash compounds fast. The binding constraint is
**worker-actions**: 24 per unit per day, and every planted tile needs a `WATER` and
every animal a `FEED`. So the ranking that matters is $ per action:

| option | units/day | actions/day | $/action (at base) | $/action (at observed prices) |
|---|---|---|---|---|
| Melon (per cycle) | 6 per 10 actions | — | **$142** | $80–142 |
| Cow + `CARE` | 1.5 milk | ~4.5 | $60 | **$95** (milk realised $281–320) |
| Sheep + `CARE` | 1.33 wool | ~4.3 | $67 | $91 (wool realised $240) |
| Goose + `CARE` | 2 eggs | ~4.4 | $28 | $32 |
| `COLLECT_FERTILIZER` | 1 fert | 1 | $100 | $74 avg realised |
| Wheat *as feed* | 4 per 6 actions | — | $32 | $32 (priced at the *buy* curve) |
| Wheat *as a sale* | 4 per 6 actions | — | $15 | $15 |
| Carrot | 3 per 5 actions | — | $17 | $17 |
| Strawberry | 4 per 17 actions | — | $22 | $67 (price sits at ~$310) |

Consequences that drove the design:

- **Geese are the *worst* of the three animals**, which inverts the obvious read. They
  are cheapest and pay earliest, but eggs sit near base while milk and wool trade at
  1.5–2× base because nobody supplies them. Cows and sheep first; geese only absorb
  leftover labour.
- **Wheat grown for feed is worth its *purchase* price (~$50), not its sale price
  (~$20).** `crop_score` quotes it off the scarcity side of the curve for that reason.
- **Market orders cost zero actions.** Buying feed is therefore strictly better than
  growing it while wheat stays cheap — this is why `wheat_buy_price_cap` exists.
- `CARE` on a cow is +1 milk for one action ≈ $280/action. Never skipped.

## 4. What I tried, in order, and what it cost

Each row is a full 719-turn episode, seed 11, versus the Phase-1 wheat bot.

| change | score | what the diagnostics showed |
|---|---:|---|
| Phase 1 baseline (`phase1_agent.py`) | $8.2k | the bar |
| First full agent | $40.5k | 5 sheep bought day 1 → cash hit $12 → **all starved**. 38 empty coops built on lookahead. 7× shed overflow. |
| Feed cash reserved before livestock; structures only for owned animals | $69.8k | starvation gone, overflow gone |
| Never sell wheat while we own animals | $92.0k | had been buying 951 wheat for $41k while selling 495 for $22k — **a net −$19k of pure churn** |
| Hire target computed once at hour 0 | $96.8k | had been re-estimating at hour 1, seeing the jobs already done, and talking itself out of hires it still needed |
| Cap *total* herd by labour (was capping geese only) | $82.9k | 35 animals on ~118 useful actions/day — over-extended; fewer, fed animals scored better |
| Price-responsive scoring (`crop_score`/`animal_score`) | $70.6k solo | solo *fell*, but **mirror rose $25k → $37k** — see §5 |
| Marginal pricing + land gated on labour | $70.6k | melon had over-expanded to 37 tiles and crashed itself to $131 |
| Horizon-truncated crop yields | $71.7k | had been planting 32 strawberries around day 14–18; a 17-day crop planted on day 18 returns 1 unit |
| **Nearest-first tile targeting** | **$77.0k** | the big one — see §5 |
| Crop tiles capped by labour + staggered sowing | $70–89k | 6 melons were dying in a single night (~$9k); planting is now smoothed |
| **`useful_action_frac` 0.45 → 0.24** (swept) | **$108k** | see §5 — shrinking the farm was worth more than everything after the routing fix |
| Livestock cutoffs derived from payback, not hardcoded | $108k | fixed an outright **loss to `random`** on a short season |
| **Opening ramp, from ladder replays** (§7) | **$124k** | day 0 went from 0 melons / $1,800 idle to 15 melons / $398 |
| Seed spend capped by the feed reserve | $124k | the aggressive opening had reintroduced starvation — cash hit $37 on day 1 and animals died on day 2 |
| Proportional cash reserve | $124k | a flat $300 floor exceeded a $200 starting bank and stalled the agent into buying *nothing* |

Final: **$110–134k solo (median $126k over seeds 11–15, 5-0-0), $62–89k in a mirror
match.** Zero crashes, zero illegal actions, zero wasted (no-op) actions across every
run; worst turn 2 ms against the 1 s `actTimeout`. Verified to load by file path
(`env.run(["main.py", …])`) exactly as Kaggle loads a submission, with empty stderr.

## 5. The two findings I didn't expect

**Movement was half the game.** I instrumented every move by cause rather than guessing:

```
before:  act=30.3%  move_work=48.6%  move_steal=8.0%  move_haul=4.9%  move_errand=4.1%  pass=4.2%
after:   act=35.5%  move_work=42.9%  move_steal=6.7%  move_haul=6.9%  move_errand=3.1%  pass=4.9%
```

`move_work` — walking to tiles in a worker's *own* cluster — dwarfed hauling and
errands combined. The cause was that workers targeted the *first* pending tile in
route order, so a worker standing at the far end of its cluster walked all the way
back to the start. Switching to nearest-pending was worth **+68% on seed 13**
($42.8k → $71.7k) and +25% on seed 11 — far more than any market tuning.

**The optimal farm is much smaller than the land or the cash allows.** `useful_action_frac`
is the single knob controlling how much the planner believes it can service; sweeping it
head-to-head gave a clean monotone ordering — `0.24 ≻ 0.30 ≻ 0.34 ≻ 0.38 ≻ 0.42`, with
0.18 giving most of it back. Dropping from 0.45 to 0.24 took the median from $70k to
$108k. The asymmetry is the reason: an unfed animal *escapes permanently* and an
unwatered plant *becomes a weed*, so over-extending doesn't cost you the marginal tile's
profit — it costs you tiles you already own. Ending the season with 18 empty tiles and
$108k beats filling them and losing the herd. This is the finding I'd have been least
likely to guess: the brief's "farm size is capped by your action budget" is right, but
the safe operating point is well *below* the nominal budget.

**Solo score and contested score pull in opposite directions.** Adding price-responsive
scoring *lowered* the solo score by $26k while *raising* the mirror-match score by $12k.
Against a passive opponent, the greedy fixed plan (dump 37 tiles into melon) maximises
coins. Against a real opponent competing for the same shallow curves, the adaptive plan
wins. Since only win/loss affects ladder rating, **I kept the change that lost solo
coins.** That is also why the mirror match — not the solo score — is the metric I tuned
against.

## 6. Architecture

Layers, per the brief, all in `main.py`:

- `TurnState` — normalises one observation; derives per-tile status, worker positions,
  shed load, live market params (honouring `marketParams` overrides).
- Market model — `market_price` reimplemented exactly from `MARKET_PARAMS`, plus
  `price_after_selling`, `price_after_buying`, `cost_of_buying`, `affordable_units`, and
  `units_above_floor` (binary search, since price is monotone in inventory).
- Forecasting — `crop_yield(crop, remaining_days)` returns (units, actions, tile-days)
  truncated by the remaining season; `crop_score`/`animal_score` convert that to $/action
  at *marginal* prices (pricing the next tile behind everything already in the ground).
- Daily planner — `survey` → `plan_capital` (feed cash first, then land, then livestock)
  → `assign_roles` → `plan_seeds` → `plan_hires` → `assign_clusters`. Runs once per day,
  plus on worker-count or livestock-stock changes.
- Turn executor — strict priority: shed errand → overflow haul → at-risk rescue →
  nearest cluster job → bounded steal → haul.
- Market executor — feed, hires, land, sells, then discretionary buys, capped at
  `maxMarketOrdersPerTurn`. Sells precede buys so they fund them within the turn.

All tunables live in one `PARAMS` dict; `sim_harness.py sweep` mutates it per candidate.

## 7. What the real ladder taught us (submission 55256850)

First submission scored **675.5 after 5 episodes, record 3-2**, no errors. Mean score
$79.7k against opponents' $55.0k — but both losses were narrow ($-3.8k, $-7.2k) against
strong agents. Pulling the replays was worth more than every local sweep combined.

**The self-play sweep had been lying to me.** Tuning candidate-vs-baseline optimises
against a copy of yourself, which shares your own blind spots. `max_plant_per_day = 5`
won that way because *both* sides opened slowly. The replay showed what it actually cost:

| day | opponent (Richard G Atkinson) | us |
|---|---|---|
| 0 | 2 cows, 12 melons, **$10 left** | 0 cows, 0 melons, **$1,800 idle** |
| 6 | 4 cows, 20 melons | 0 cows, 20 melons |
| 15 | 4 cows | 4 cows |
| final | **$78,001** on *one* quadrant | $70,792 on two |

They beat us with 4 cows and 25 tiles. We reached 10 cows — but not until day 21, and a
cow bought on day 21 yields once before the season ends. Livestock compounds from its
first yield day, so the opening is worth more than the endgame.

Three fixes came directly out of that replay:

1. **Burst planting on days 0–2** (`plant_burst_per_day = 14`). The staggering rule was
   right for mid-season watering peaks and wrong for day 0, when nothing else needs water.
2. **Livestock outranks seeds for early cash** (`early_seed_hold_frac`). `plan_capital`
   was subtracting the full seed bill before it ever considered a cow.
3. **Don't dilute early capital across species** (`score_ratio_cutoff`). Round-robin was
   buying a $300 goose at $39/action instead of most of a cow at $70/action.

That took the local median from **$111.9k to $126.0k (+13%)** — comfortably more than the
$3.8k and $7.2k the two ladder losses turned on.

Two things the replays also settled: the opponent **also sells fertilizer** (109 units),
so that edge is real but not exclusive; and they ran **199 hires to our 165**, which
suggests `useful_action_frac = 0.24` may still be a shade conservative against the field
even though it swept well against ourselves.

## 7b. Second submission: 824.4, and what 19 episodes showed

The opening fixes took the ladder score **663.0 → 824.4**. Record over 19 real
episodes: **11-8 (58%)**, mean $95.6k against opponents' $85.7k, no errors.

But the losses were to opponents scoring **$114k–$138k**, above my local best. Pulling
those replays found a genuine bug and a genuine strategic miss.

**The bug: I never fertilized ongoing crops.** `tile_ops` guarded the FERTILIZE branch
with `if not cd["ongoing"]`. Measured on the interpreter, fertilizing a strawberry takes
it from **4 units to 8** — $47/action to $86/action at a $250 price. Worse, a fertilizer
*applied* to a strawberry is worth roughly $375 of extra fruit against the ~$74 it
fetches at market, so my agent was selling its best input. Fixed: ongoing crops are now
fertilized on their production days (one application spans two, since it lasts 3 days),
watered on those days so the bonus actually lands, and a reserve is held back from sale.

**The miss: volume.** In the $138k loss the comparison was brutal:

| units sold | them | me |
|---|---|---|
| WHEAT | 799 | 0 |
| STRAWBERRY | 374 | 22 |
| MILK | 342 | 170 |
| FERTILIZER | 255 | 151 |
| WOOL | 239 | 199 |
| **hire orders** | **303** | **162** |

~2,150 units against my ~709, on double the labour. And prices *rose* all game
(strawberry 128→274, wheat 30→61, milk 169→221), so the town absorbed every unit of it.
All three top opponents ran the same shape: ~30 strawberry tiles, 6–8 cows, 6 sheep,
12 hires/day, no geese.

**The methodological lesson, which is the important part.** My sweeps had been scoring
candidates against *a copy of the same agent*, and my duels against `random`/`phase1` —
none of which sell anything. In that world every price stays high and shrinking the farm
looks free, which is exactly how `useful_action_frac = 0.24` won 5-0-0. Against a real
opponent competing for the same shallow curves that reasoning inverts.

So `sim_harness.py` gained an **arena**: a `META_PARAMS` sparring partner built to the
observed field archetype, plus `arena` and `sweepvs` commands that score every candidate
against it. Re-running the same sweeps there immediately disagreed with the old ones —
forcing strawberry looked *bad* against `random` (0-5) and good against a competitor,
because only the competitor is also draining the milk and wool curves I was leaning on.

## 7c. Reading the 2881-rated public notebook

Decoded the public v20 notebook (`sha256 231cf97f…`, 26,894 bytes) to see what a
2881 rating is made of. It is worth being precise, because it is not a farming agent:

```python
def agent(obs):
    step = min(max(0, int(obs["step"])), len(_ACTIONS) - 1)
    action = _weed_repair_action(obs, _copy_action(_ACTIONS[step]), step)
    return _hybrid_action(obs, action, step)
```

`_ACTIONS` is a **hardcoded 719-turn script** decompressed from a base85 blob — the
"medoid" of eight top-player trajectories lifted from public replays (their cell 8 names
episode `90049120`, player 0). Three wrappers sit on top: a weed-slip patcher (an
open-loop script cannot see the board change), a 7-feature logistic model that detects
whether the opponent is running *the same copied script*, and a market front-runner that
sells just ahead of them when it is. Their own ablation puts the copied route at 98/108
and front-running at +4 — so the rating is mostly *the copy*, amplified by the fact that
much of the leaderboard is running the same copy.

Two things there are genuinely transferable, and both are legitimate:

1. **`obs["farms"][1 - player]` is public.** Every rival tile, animal and standing yield
   is visible. Implemented as `opponent_supply()`: it projects what the rival is about to
   put on sale and (a) discounts our hold floor on those goods, and (b) pushes them to
   the front of the order queue when the 10-order cap binds. On a ~60-unit-deep curve,
   selling first *is* the edge.
2. **Selling earlier beats holding** when a rival is about to flood the same curve.
   Exposed as `hold_frac_scale`.

Both measured **neutral against the arena** (+17.3k margin either way, and
`hold_frac_scale` 0.45 vs 1.0 gave byte-identical results). The reason is diagnostic
rather than disappointing: against META the hold floors *never bind*, because prices stay
above base all game. They only bind against an opponent dumping hundreds of units — which
is exactly the ladder opponent this was built for, and exactly what my local sparring
cannot reproduce. Kept on those grounds, flagged as unvalidated locally.

## 7d. Two things that turned out to be false

**The hold-floor market model is dead code.** Sweeping `hold_frac_scale` over
0.45/0.7/1.0/1.3 returned *byte-identical* results. Printing the realised price path
against the floors explains why:

| | STRAW | MILK | WOOL | MELON | FERT |
|---|---|---|---|---|---|
| realised, day 5→25 | 163→248 | 204→287 | 221→248 | 272→138 | 100→51 |
| hold floor | $54 | $67 | $84 | $80 | $12 |

Prices sit 3–4× above the floors for the whole season, so the floors never fire. The
executor is effectively "sell everything," and that is near-optimal — the town drains
faster than two farms can supply. The one knob that *does* bind is `slip_per_turn`, and
sweeping it (0.90 / 0.94 / **0.965** / 0.99 → margins +8.3k / +17.0k / **+20.4k** /
−11.2k) shows the current value is already the peak. 0.99 collapses to 0-5 because it
throttles selling so hard the shed overflows.

Conclusion: **the market was never the constraint. Worker-actions are.** That retires a
whole branch of tuning.

**Mid-day cluster rebalancing is actively harmful.** `pass = 10%` alongside 53% movement
looked like uneven load, so I tried re-splitting clusters every 6 turns. Score fell
$111.6k → $83.2k and animal losses doubled (3 → 6). The cause is a coupling I had not
made explicit: a worker fetches wheat *sized to the animals in its own cluster*, so
reassigning tiles mid-day strands it holding feed for animals it no longer visits.
Reverted, and the constraint is now written down in the code so it isn't retried.

## 7e. The estimator bug behind both idling *and* over-walking

`pass = 10%` sitting next to `move_steal = 9.5%` was the tell: workers idle while other
workers cross the farm. The cause was in `expected_jobs`, which counted **any** standing
plant as 1 unit of work — including one that needs no water and has nothing to harvest
today. `assign_clusters` therefore padded clusters with no-op tiles, and whoever drew
them walked the round, found nothing, then idled or stole.

Making the estimate mirror what the executor actually does moved every diagnostic the
right way — `act` 36.0→40.1%, `pass` 10.0→3.8%, `steal` 9.5→4.8% — and **the score fell**
$111.6k → $98.8k.

The reason is the interesting part. The same estimator drives `plan_hires`, so an honest
estimate hired fewer hands (`acts` 4232 → 3955). And it *should* under-hire: it can only
count the work it can see, never the ~55% of a worker-day that disappears into walking.
Correcting for that with `hire_slack`:

| `hire_slack` | 1.1 | 1.35 | **1.6** | 2.0 | 2.6 | 3.2 |
|---|---:|---:|---:|---:|---:|---:|
| score (seed 13) | $99k | $124k | **$133k** | $129k | $129k | $128k |
| `act` share | 40.1% | 36.6% | 34.0% | 28.9% | 26.0% | 23.4% |

**More hands keep winning while the efficiency ratio keeps falling.** That is the whole
lesson of this phase: the objective is absolute useful actions, not the fraction of
actions that are useful. Optimising the ratio — which is what the movement diagnostics
tempt you to do — is optimising the denominator.

Result: arena margin +17.3k → **+19.7k**, solo median **$118k → $136k**, mirror
$80–96k → $108–115k.

## 7f. Why a 11% stronger agent gained 0% win rate

Comparing the two submissions on their *actual* episode records rather than the
leaderboard number:

| | old (55258235) | new (55274635) |
|---|---|---|
| record | 18-17 (51%), n=35 | 14-14 (50%), n=28 |
| my mean score | $96,144 | **$106,293** |
| opponent mean score | $94,848 | **$103,911** |
| median margin | +2,393 | +1,923 |

The agent got ~11% stronger, matching the local gain — **and the opponents got 9.6%
stronger at the same time**, because a higher rating draws higher-rated pairings. Win
rate is the ratio of two quantities that rise together, so absolute improvements are
consumed by matchmaking until a new equilibrium is found.

Two practical consequences:

1. **Never compare two ratings read at different times.** The old submission had drifted
   824.4 → 799.5 over ten hours; the "drop" to 813.5 was actually the new one *leading*.
   Only the paired episode records settle it.
2. **The median margin is under 2% of score.** Games at this rating are decided by a
   couple of thousand coins, so an improvement is only worth a win if it exceeds ~$2k.
   That makes small, reliable gains (and avoided losses) worth as much as big risky ones
   — and it is why `haul_pressure` was tuned on *overflow events* rather than score
   alone.

## 7g. The routing rewrite: a negative result

Movement had sat at ~60% of every action for the whole project, and I had been
carrying an estimate that a real routing solve was worth 20–30%. It was tried
properly. **All four variants lost to the existing design.**

| cluster scheme | mean score (3 arena games) | losses | `move_steal` |
|---|---:|---:|---:|
| chunks sized by each worker's reachable budget | $78,439 | 36 | 21.0% |
| ...capped by an equal share of remaining work | $87,688 | 23 | 16.2% |
| equal split + chunks matched to nearest worker | $86,839 | 34 | 8.2% |
| **equal-work contiguous chunks, chunk i -> worker i** | **$115,465** | **21** | 12.9% |

Each failure was informative:

1. **Budget-sized chunks** pack the work into the first few workers, because a chunk
   stops as soon as that worker is full. The remainder of the workforce gets nothing
   and converts its whole day into stealing — `move_steal` doubled to 21%.
2. **Spawn-proximity matching** re-shuffles cluster ownership on every replan, and
   replan fires several times a day (hands arriving at hour 1, livestock placed).
   A worker that drew wheat for *its* animals gets handed a different region and the
   animals it fed for go hungry: losses 18 → 34, `move_errand` 5.8% → 8.3%. This is
   the same coupling that killed mid-day rebalancing in §7d, arriving by another road.

**Fixed secondary regions also failed.** The follow-up idea was to give each worker a
permanent backup region — the chunks either side of its own in route order, derived from
the same stable index-based split so ownership never reassigns. It behaved exactly as
designed and still lost:

| backup radius | mean score | losses | `pass` |
|---|---:|---:|---:|
| none (baseline) | **$115,465** | 21 | 8.8% |
| 3 | $102,687 | 17 | 5.4% |
| 5 | $110,559 | 18 | 5.3% |
| 8 (unbounded ≈) | $110,449 | 20 | 6.4% |

`pass` fell from 8.8% to ~5.4% at every radius, so idle turns really were converted into
work — the work simply wasn't worth the walk. The lesson is that **idling next to your
own cluster has option value**: a worker that passes is still in position when one of its
own tiles comes due (an animal produces, a plant ripens, cargo needs hauling), whereas
one that wandered off to help is not. `pass` is not pure waste, and the movement
diagnostics make it look like waste.

The conclusion is that **the ~60% movement is structural, not a defect**. Every worker
spawns at the shed, so the approach walk is a fixed tax; contiguous route-order runs are
already close to optimal for the travel *within* a chunk; and cluster **stability is
worth more than cluster optimality** because workers carry state (feed, fertilizer,
livestock) matched to the tiles they were given at dawn.

That retires the largest remaining hypothesis on the list. What is left is genuinely
smaller-grained: `move_steal` 12.9% + `pass` 8.8% is still ~21% of actions spent because
clusters finish at different times, and the only fix that does not destabilise ownership
would be giving each worker a *fixed* secondary region adjacent to its own.

## 8. What I'd tune next

1. **Movement is still 37–43%.** The remaining win is spatial: cluster assignment is
   contiguous in *route* order, not a real routing solve. A cheap 2-opt over each
   worker's tile set, or assigning clusters by proximity to spawn tile rather than by
   equal work, should recover several more points.
2. **Fertilizer is under-exploited.** We realise ~$19k of a ~$25k pool. Because there is
   no drain, sale *timing* is irrelevant to us but not to the opponent — whoever sells
   first gets the high end. An explicit "sell fertilizer early and fast" rule is
   probably worth a few thousand.
3. **Opponent modelling.** `obs["farms"][1-player]` exposes the opponent's whole farm.
   Counting their pastures/coops predicts milk and wool supply several days ahead, which
   would let us pre-empt a crash rather than react to it. Nothing currently reads it.
4. **The premium targets (`target_cow` 18, `target_sheep` 14) are still hand-set.** They
   should fall out of the depth table in §2 divided by per-animal output, adjusted for
   observed opponent supply.
5. **Endgame liquidation is a linear ramp** from `liquidate_start_day`. The correct
   schedule is per-resource and derived from each curve's recovery rate; melon (no shop
   drain) should start drawing down much earlier than wheat (bottomless).
6. **Sweeps are noisy at 5 seeds.** Differences under ~$5k are not distinguishable from
   seed variance; the weed RNG alone diverges the two farms in a mirror match. Only
   `useful_action_frac` produced a signal (5-0-0) large enough to trust without more runs.
7. **Only two quadrants get bought** in a typical game now, because land is gated on
   labour headroom. If the routing work in (1) lands, that gate should re-open and the
   third quadrant becomes worth its $2k.

## 9. Robustness

Checked against non-default configuration, since the brief warns not to assume the
defaults hold — all read from `configuration`, none hardcoded:

| config | result |
|---|---|
| `farmHandCostMult: 20` | $82k, no errors (hire cap self-adjusts) |
| `shedCapacity: 40` | $17k, no errors |
| `boardSize: 8` | $121k |
| `turnsPerDay: 12` | $127k |
| `maxMarketOrdersPerTurn: 3` | $45k |
| `startingMoney: 200` | $43.6k � needed a *proportional* cash reserve; a flat $300 floor exceeded the whole bank and stalled the agent into buying nothing |
| `episodeSteps: 200` | $4.6k — **was a loss to `random`** until livestock cutoffs were derived from payback |
| `marketParams` overrides (wool nerfed, melon base 20) | $85k — the scoring layer re-ranks the mix automatically |

The agent reads `boardSize`, `turnsPerDay`, `shedCapacity`, `maxMarketOrdersPerTurn`,
`farmHandCostMult`, `episodeSteps`, and per-resource `marketParams` overrides from
config, and derives the last actionable step as `episodeSteps - 2`. The top-level
`agent` wraps everything in try/except and falls back to `PASS` with no market orders;
it also resets its cached plan if `step` ever moves backwards, so a reused process
starting a second episode cannot inherit stale state.
