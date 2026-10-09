# Configuring the current implementation

Use `scripts/run_fcr.py` with [configs/fcr.toml](../configs/fcr.toml).
The runner also accepts the `effective_config.json` saved by any current run.
There are no separate teaching, linear-frequency or market-stage runners.

## Change ordinary experiment parameters

| Goal | Parameters |
|---|---|
| Change deficit or duration | `scenario.power_deficit_mw`, `event_time_s`, `final_time_s` |
| Change sampling or seed | `scenario.output_time_step_s`, `seed`, or CLI `--seed` |
| Change reserve demand / minimum positive offer | `market.requirement_mw`, `market.minimum_bid_mw` (default 1 MW) |
| Change uncertainty | Provider `mean_mw`, `variance_mw2`, `reserve_capacity_mw` |
| Change boundary treatment | `availability.boundary_mode = "truncated"` or `"clipped"` |
| Change economics | Provider linear/quadratic cost, penalty, `minimum_bid_price_eur_per_mw_block`, `maximum_bid_price_eur_per_mw_block` |
| Limit offered capacity | Provider `reserve_safety_margin_fraction` (default 0, range 0–1) |
| Change machine/converter size | Provider `rating_mva` in a configured fleet |
| Change synchronous inertia | Conventional provider `inertia_s`; renewables retain zero inertia in this model |
| Change dispatch / physical limits | Provider `power_setpoint_mw`, `minimum_power_mw`, `maximum_power_mw`, `reserve_capacity_mw` |
| Change frequency response | Provider `time_constant_s` and `scenario.droop_pu` |
| Add/remove a generator | Add/remove its `[[providers]]` entry; keep exactly one conventional slack |
| Repeat a saved experiment | `--config results/.../effective_config.json` |

Prices and costs apply to the entire delivery block. Specifying a different
block duration does not rescale coefficients automatically.

## Continuous offers and reserve safety margins

Each positive offer uses its provider's maximum admissible price. The reward
increases with price because acceptance probability is absent. Prices may have
equal bounds; both must be finite and nonnegative, with minimum <= maximum.

Quantity is optimized continuously over
`{0} union [market.minimum_bid_mw, (1 - reserve_safety_margin_fraction) * reserve_capacity_mw]`.
The market minimum must be positive. An empty positive interval or no strictly
positive reward gives a zero offer. Equality of the bounds permits that single
positive quantity. Clearing remains divisible, so an award can be below the
minimum bid. `decision.quantity_tolerance_mw` defaults to `1e-8` and controls
the bounded SciPy optimizer's absolute quantity tolerance; endpoints are also
evaluated exactly. No new dependency or candidate grid is used.

`reserve_capacity_mw` is an explicit physical capability already checked against
**both** `maximum_power_mw - solved_power_mw` and
`solved_power_mw - minimum_power_mw`. The non-slack setpoint determines dispatch;
the slack setpoint is only an initialization guess. All headroom checks use
the solved ANDES powers. Inconsistent physical capacity raises an error.

The safety margin is an extra conservative bidding limit: cap 4 MW and margin
0.1 give a maximum offer of 3.6 MW. It does not subtract physical headroom again
or change the availability law. A margin of 1 allows only zero. Awards still
feed `min(award, availability)` and never change droop gains. This optional
policy assumption may restrict the reward-maximizing bid; it is not a calibrated
reliability guarantee or an additional uncertainty penalty.

To migrate earlier configurations, replace each price list with its minimum
and maximum, remove `quantity_candidates_mw`, and set `market.minimum_bid_mw`.
Old grids are rejected explicitly. Historical saved JSON files also need this
migration. Current `effective_config.json` files remain directly replayable.

## Fixed offers, with normal market clearing

Set:

~~~toml
[decision]
mode = "fixed"

[market]
mode = "clear"
requirement_mw = 12.0
block_duration_hours = 4.0
~~~

Then put these fields in every provider entry:

~~~toml
fixed_bid_eur_per_mw_block = 20.0
fixed_offer_mw = 3.0
~~~

This bypasses the provider optimizer while using the normal auction.
Price intervals can be omitted in fixed-decision mode; prescribed prices bypass
the optimizer's price intervals. Quantity must still be zero or satisfy the
market minimum and provider's safety-adjusted maximum. Fixed offers may be
unprofitable by design; expected reward is recorded without changing them.

## Fixed awards and clearing price

Use fixed offers as above, then:

~~~toml
[market]
mode = "fixed"
requirement_mw = 5.0
block_duration_hours = 4.0
fixed_price_eur_per_mw_block = 7.0
~~~

Give each of the five providers:

~~~toml
fixed_offer_mw = 2.0
fixed_bid_eur_per_mw_block = 1.0
fixed_award_mw = 1.0
~~~

Awards must sum to the requirement and cannot exceed offers. The same
availability, settlement, dynamics and output functions run afterward.
For programmatic use the single handoff is `prepare_fcr_block` in
`simulation.py`, with optional `fixed_offers`, `fixed_awards_mw` and
`fixed_price_eur_per_mw_block` arguments.

## Deterministic reserve, prescribed gains, or no FCR

Set `variance_mw2 = 0.0` and `mean_mw` to the desired available reserve.
It must fit the physical reserve interval.

Normally $K_i=P_{B,i}/(R f_N)$. An explicit provider
`gain_mw_per_hz = 20.0` overrides that calculation for that provider.
Overrides are visible in the saved configuration and market table. They do
not silently change when the market award or availability changes.

Set `market.requirement_mw = 0.0` with normal clearing for zero awards and
no FCR delivery. If using fixed allocation, all fixed awards and the fixed
clearing price must also be zero. The same numerical model still runs.

## Parameter comparisons

`--compare` runs the base configuration followed by its `[[comparisons]]`
entries. Every variant starts independently from base. Sections `scenario`,
`market`, `decision` and `availability` override their corresponding flat tables.
`all_providers` applies changes to every provider, then `providers` applies
changes by provider ID, taking precedence over common changes:

~~~toml
[[comparisons]]
name = "no_penalty"
label = "No penalty (all providers)"
all_providers = { penalty_eur_per_missing_mw_block = 0.0 }

[[comparisons]]
name = "custom_uncertainty"
label = "Higher uncertainty with bidding margins"
market = { requirement_mw = 8.0, minimum_bid_mw = 0.5 }
scenario = { power_deficit_mw = 5.0 }
all_providers = { reserve_safety_margin_fraction = 0.1 }
providers = { renewable_uncertain = { variance_mw2 = 9.0, reserve_safety_margin_fraction = 0.25 } }
~~~

No Python change is needed to add these cases. Names must be unique simple
lowercase directory names; `base` is reserved. Labels must also be unique.
Provider IDs must exist and cannot be renamed by overrides. Fields must exist
in the base table or be supported optional fields (such as safety margin, fixed
offers/awards, or manual gain). Misspelled override fields raise an error.

The default `no_penalty` changes only all providers' penalty coefficients. The
`lower_uncertainty` case changes only the uncertain renewable's variance. All
variants use the same runner and inherit the seed unless overridden in their
`scenario` table. CLI `--seed` overrides every selected case's seed. With the
same seed and unchanged availability laws, the default cases preserve draws.

From the repository root in PowerShell:

~~~powershell
# One base simulation
.\.venv\Scripts\python.exe scripts\run_fcr.py

# One named configured scenario
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario no_penalty --output-dir results\no_penalty

# Base and every configured comparison, including combined plots
.\.venv\Scripts\python.exe scripts\run_fcr.py --compare

# Replay a resolved scenario
.\.venv\Scripts\python.exe scripts\run_fcr.py --config results\fcr\base\effective_config.json --output-dir results\replay

# Existing test suite
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

Each run saves the resolved inputs and seed. `market_outcomes.csv` includes the
physical cap, bidding cap, safety fraction and applied penalty. `offer_rewards.csv`
records zero, feasible interval endpoints and the selected offer at the price
ceiling (or prescribed fixed price); these are diagnostics, not a search grid.

## Reproduce the earlier four-generator operating point

Set `scenario.operating_point = "bundled_pjm5"` and use exactly four provider
entries with generator IDs `0, 2, 4, 3`. ANDES supplies their solved powers
and machine bases; configured-fleet fields such as bus, inertia and setpoint
are unused in this mode. Keep `power_base = "machine_rating"`.

The original Gaussian-market parameter set is recorded here as data, not a
second implementation. Use 5% droop, seed 20261008, 10 MW market requirement
and deficit, event time 1 s, horizon 40 s, output step 0.05 s, sensitivity 1,
truncated availability, normal optimization and normal clearing.

| Provider ID | Generator | $P_{\min},P_{\max}$ [MW] | Cap [MW] | $T$ [s] | $\mu$ [MW] | Variance [MW²] | $a$ | $\beta$ | Penalty | Prices |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| provider_1 | 0 | 206, 214 | 4 | 0.4 | 3 | 0.49 | 3 | 1 | 25 | 10,15,20 |
| provider_2 | 2 | 317, 330 | 6 | 0.8 | 4.5 | 2.25 | 4 | 1 | 30 | 12,18,24 |
| provider_3 | 4 | 460, 473 | 6 | 1.5 | 4 | 2.25 | 5 | 1.5 | 35 | 14,21,28 |
| provider_4 | 3 | 0, 10 | 3 | 3 | 2.5 | 0.64 | 6 | 2 | 40 | 16,24,32 |

The historical grid ran from zero to each cap in 0.5 MW increments and gave
offers [3,4.5,4,2.5] MW, awards [3,4.5,2.5,0] MW, price 28,
nadir 59.893497 Hz and final frequency 59.949798 Hz. To reproduce those dynamics
now, prescribe those offers at prices [20,24,28,32] in fixed-decision mode.
Using the historical price endpoints with continuous optimization will
generally produce different offers; the obsolete search is not retained.

The earlier deterministic example uses the same bundled operating point,
explicit gains [10,15,20,25] MW/Hz, time constants [0.4,0.8,1.5,3] s and
fixed awards [1,2,3,4] MW. Set reserve caps and deterministic availability to
those awards, fixed offers equal to them, and omit the unused price intervals.
For a dynamics-only reproduction, set fixed
prices, costs and penalties to zero. Its nadir/final frequency is
59.827191 / 59.882609 Hz. The old slack award of 4 MW around approximately
3.939 MW requires an explicitly negative lower active-power bound to reproduce
symmetric headroom; a lower bound of -1 MW is an illustrative mathematical
assumption, not a realistic capability claim. Current validation is not bypassed.

Before the continuous-bidding update, both earlier parameter sets were checked:
their frequency trajectories agree with the saved outputs to within
$7.2\times10^{-15}$ Hz (CSV round-trip precision).

## Where to change the model itself

- `decision.choose_capacity_offer`: provider objective and offer selection.
- `market.clear_fcr_market`: allocation/pricing rule.
- `providers.FCRProviderParameters`: requested response and time constant.
- `frequency.frequency_derivative_hz_per_s`: aggregate frequency equation.
- `simulation.prepare_fcr_block`: the interface between economics and dynamics.

Keep signs, units, information timing, physical capability and settlement
assumptions explicit when changing these functions.
