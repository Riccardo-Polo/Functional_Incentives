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

FCR prices and costs apply to the entire delivery block. Specifying a different
block duration does not rescale coefficients automatically. FI coefficients
below define rates integrated over the simulated event in seconds.

## Functional incentives: participation, gamma and timing

The default base scenario remains FCR-only for comparison. Run the included FI
scenarios through the same runner:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario fi_exogenous --output-dir results\fi_exogenous
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario fi_endogenous --output-dir results\fi_endogenous
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario fi_strong --output-dir results\fi_strong
.\.venv\Scripts\python.exe scripts\run_fcr.py --compare --output-dir results\fcr_fi
~~~

`fi_exogenous` assigns the uncertain renewable to FI. `fi_endogenous` lets it
try the existing FCR offer optimizer and enter FI only on a zero offer.
Both use gamma 50 and rho 1. `fi_strong` uses the same endogenous rule with
gamma 200. These are illustrative coefficients, not calibrated market prices.
The base 1 MW minimum makes the uncertain renewable opt out in both endogenous
cases; lowering the minimum or changing penalties can change that outcome.

To enable FI directly in a provider's existing `[[providers]]` table, add:

~~~toml
participation = "fi"  # or "fcr_or_fi" for the sequential FCR opt-out rule
fi_gamma_eur_per_hz_mw_s = 50.0
fi_rho_eur_per_mw2_s = 1.0
~~~

Keep the existing physical and FCR economic fields. The latter are unused for
an assigned FI provider's bid but are needed when trying `fcr_or_fi`.
Gamma must be finite and nonnegative; rho must be finite and strictly positive.
Both are explicit for `fi` and `fcr_or_fi`; neither is inferred from FCR costs.
Gamma remains fixed during the event. Gamma zero gives no payment and, from
the default zero initial response, no FI action.

| Setting | Meaning |
|---|---|
| Provider `participation = "fcr"` | Default. Existing FCR behavior; zero awards do not enable FI. |
| Provider `participation = "fi"` | Exogenous FI assignment; zero FCR offer and award. |
| Provider `participation = "fcr_or_fi"` | FI only if the optimized FCR offer is zero; positive rejected offers stay FCR. |
| Provider `participation = "inactive"` | No controlled response; dispatch and online inertia remain. |
| Provider `fi_gamma_eur_per_hz_mw_s` | Fixed coefficient in `s = -gamma * sampled_error * delivered_MW`. |
| Provider `fi_rho_eur_per_mw2_s` | Positive coefficient in activation-cost rate `rho * delivered_MW² / 2`. |
| `fi.decision_interval_s` | Positive shared FI measurement/decision interval, required when FI is active. Default config: 0.1 s. |
| `scenario.maximum_solver_step_s` | Optional positive RK45 maximum step; defaults to the output step for backward compatibility. |
| `scenario.output_time_step_s` | Saved/plot sample spacing. Set the solver maximum explicitly to vary this independently. |

The clock starts at zero. All FI providers decide simultaneously; a final short
interval is optimized using its actual length. A disturbance between decisions
does not trigger a new measurement. The sample is held for settlement throughout
the interval, while actual power evolves continuously with `time_constant_s`.
FI capability is the sampled `A_i`, bounded by the physical symmetric reserve
cap. There is no FCR minimum bid or bidding safety margin on FI targets.

Assigned FI/inactive providers must have zero prescribed offers and awards in
fixed modes. `fcr_or_fi` is incompatible with `decision.mode = "fixed"`, since
a prescribed zero is not a voluntary optimizer result. With optimized offers,
fixed allocation is allowed subject to the same offer bounds and exclusion.
FI never fills the FCR procurement requirement; infeasible clearing still stops
the run before availability is sampled.

### Save a gamma experiment

Edit gamma in an existing FI comparison to change that named scenario, or
append the following comparison at the end of the TOML:

~~~toml
[[comparisons]]
name = "fi_gamma100_slow_sampling"
label = "FI gamma=100, decision every 0.5 s"
fi = { decision_interval_s = 0.5 }
scenario = { maximum_solver_step_s = 0.02 }
providers = { renewable_uncertain = { participation = "fcr_or_fi", fi_gamma_eur_per_hz_mw_s = 100.0, fi_rho_eur_per_mw2_s = 1.0 } }
~~~

Run it with `--scenario fi_gamma100_slow_sampling`. Each comparison starts from
base, so a new FI comparison must include its participation and coefficients;
it does not inherit them from `fi_endogenous`. Individual provider overrides
win over `all_providers`; the `[fi]` table supports ordinary comparison overrides.
To observe multiple FI providers, assign them explicitly and keep enough FCR
offers to meet the requirement. No role assignment changes the operating point.

### Read FI outputs

| Output | Interpretation |
|---|---|
| `market_outcomes.csv` | `configured_participation` and resolved `participation`; FCR-only block payments/profit; realized response limit and fixed FI parameters. FI's FCR profit is zero. |
| `time_series.csv` | Common `frequency_hz`, separate total FCR/FI power, each target/request, delivered response and `P_i,0 + response`. Historical `frequency_fcr_hz` is an alias of the combined controlled frequency. |
| `fi_decisions.csv` | Every FI decision, even between output samples: sample, initial response, target, final response, target benchmark, predicted/actual settlement and interval cost/profit. |
| `fi_time_series.csv` | Per-provider and aggregate signed payment rates and cumulative ledgers. Units are EUR/s for rates and EUR for balances. |
| `fi_outcomes.csv` | Per-provider final cumulative target benchmark, prediction, gross rewards/penalties, net settlement, activation cost and profit. |
| `summary.json` | `fi` gives aggregate event amounts, fixed-gamma policy and decision interval; `metrics` gives prediction and solver-refinement errors. |
| `provider_responses.png` | Delivered response, FI targets and each mechanism's capability bounds. |
| `fi_financials.png` | Individual and aggregate rates, three settlement quantities, rewards, penalties, costs and profit. |
| `comparison_summary.csv` | Frequency metrics and FI totals for every scenario. |

Gross penalties are positive magnitudes subtracted from gross rewards. Net
settlement is the cash transfer; net profit also subtracts activation costs.
The instantaneous-target benchmark is not money paid. Prediction and actual
net settlement should agree to solver tolerance with these perfectly known
dynamics, despite differing from the target benchmark. No-FI runs write empty
FI outcome/decision tables, zero aggregate time series and an explanatory plot.

All FI amounts cover `[0, final_time_s]`, including the shortened last interval.
Residual response after that endpoint is not settled. FCR figures cover the
whole delivery block. Do not directly rank the two products' profitability
without defining a common horizon and event occurrence assumptions.

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

The `lower_uncertainty` case changes only the uncertain renewable's variance. All
variants use the same runner and inherit the seed unless overridden in their
`scenario` table. CLI `--seed` overrides every selected case's seed. With the
same seed and unchanged availability laws, the default cases preserve draws.

From the repository root in PowerShell:

~~~powershell
# One base simulation
.\.venv\Scripts\python.exe scripts\run_fcr.py

# One named configured scenario
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario lower_uncertainty --output-dir results\lower_uncertainty

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
