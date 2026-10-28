# Configuring the current implementation

Use `scripts/run_fcr.py` with [configs/fcr.toml](../configs/fcr.toml).
The runner also accepts the `effective_config.json` saved by any current run.
There are no separate teaching, linear-frequency or market-stage runners.

The post-Week-5 study is specified in [NEXT_STEPS.md](NEXT_STEPS.md).
Keep the Week 5 baseline reproducible; introduce source-informed parameter
changes separately using [PARAMETER_SOURCES.md](PARAMETER_SOURCES.md).
The ordinary event settings remain unchanged. The opt-in `[tso]` table now
controls the common-gamma trade-off study; `[tso_optimizer]` controls the
weighted search. Both use the existing physical/provider simulator.

For the first saturation scan, start from `fi_exogenous` and override only
`renewable_uncertain.fi_gamma_eur_per_hz_mw_s` through provider-ID overrides
(as shown below), keeping the seed, availability law and all other inputs fixed.
Every comparison starts from base, so each new FI comparison must also specify
the provider's FI participation, gamma and rho. A comparison does not inherit
another named comparison. Reconstruct the free target from decision records
using the current formula, or add explicit diagnostics in the planned update;
the current output does not include a dedicated unconstrained-target column.

Vary `time_constant_s` and `fi.decision_interval_s` only in separate sensitivity
studies. They change the controller/dynamics; solver refinement instead keeps
both fixed and reduces `maximum_solver_step_s` or tightens tolerances.

## AEMO-inspired FPP fixed-policy benchmark

Current study: use `--fpp-benchmark --config configs/aemo_fpp_metered.toml
--output-dir results/aemo_fpp_metered`. Meters read actual P_i,0 plus delivered
response; dispatch targets are settlement-only references. This overlay selects
`assessment_schedule = "balanced_ramp"`: initial DT equals solved P_i,0, and
the 300 s target is 261 MW for C1, 309 MW for C2, unchanged for the other units.
The 1 MW balanced reallocation is prescribed, not optimized. Future DT selection
remains open. A constant-reference delivery check is saved separately.

Use `--fpp-benchmark --config configs/aemo_fpp.toml --output-dir results/aemo_fpp`.
This overlay starts from `fi_endogenous` and extends the horizon to 300 seconds,
while retaining solved machine parameters, market inputs and availability laws.
It prescribes a constant assessment reference and a balanced-reference illustration.
Historical saved runs used ideal tracking; new runs always meter actual output.
Source-defined sampling/filter/reliability constants are fixed in a versioned
profile, not ordinary tuning parameters. External directional price metadata is
optional; absent prices produce MW h repricing coefficients and missing monetary
fields. No FCR-price substitution or implicit currency conversion is permitted.
See [AEMO_FPP.md](AEMO_FPP.md) for fields, equations, provenance and commands.
Use `--fpp-report STUDY_DIR` for report-only regeneration; a new simulation needs
a new output directory. FPP and TSO optimization flags are mutually exclusive.
There is currently no FPP-provider optimization flag or configured monetary
objective. Its design is the next task; see
[AEMO_FPP.md](AEMO_FPP.md#next-research-step-fpp-provider-optimization).

For the separate deficit-to-surplus study, use `configs/aemo_fpp_reversal.toml`
and `results/aemo_fpp_reversal/`. It preserves the initial +10 MW deficit at 1 s
and sets the **absolute net deficit** to -7 MW at 120.05 s (a -17 MW change):

```toml
[[disturbance_changes]]
time_s = 120.05
net_power_deficit_mw = -7.0
```

Further entries must be strictly ordered inside the horizon. These are
prescribed scenario inputs, not new decisions or availability draws. All other
FPP overlay inputs, assessment/delivery streams and prices match the original
benchmark. The base `fcr.toml` and accepted TSO experiments remain unchanged.

## Weighted TSO optimization

The controlled [FCR procurement study](FCR_REQUIREMENT_STUDY.md) uses
`configs/fcr_requirement.toml` to derive reduced-demand cases from saved
A/B/C fleet runs. `--tso-procurement` permits only a change to
`market.requirement_mw`; gamma is reoptimized after each clearing.
`--tso-procurement-report` reads the completed runs without integrations.
Neither mode overwrites the original fleet results or changes machine parameters.

Use `--scenario fi_endogenous --tso-optimize --frequency-weight 10000
--output-dir results/tso_optimization`. The user authorized choosing 10000
EUR/(Hz² s) as a demonstration weight on 15 October and confirmed the current
five-provider FI configuration (the original request called it Week4).
Machine/market parameters are unchanged; the disturbance is 10 MW at 1 s,
H = 40 s and the FI decision interval is 0.1 s. This is not a calibrated valuation.

`--tso-optimize` and `--tso-sweep` are mutually exclusive; neither accepts
`--compare`. `--frequency-weight` is accepted only in optimization mode and
overrides `tso_optimizer.frequency_weight_eur_per_hz2_s`. The repository gives
no default weight: missing, zero, negative or nonfinite values are rejected.
There is no automatic budget or hard frequency constraint.

| `[tso_optimizer]` setting | Initial value | Meaning |
|---|---:|---|
| `frequency_weight_eur_per_hz2_s` | required explicitly | Positive w_f; CLI can supply/override it. |
| `gamma_max` | 300 | Finite positive upper search limit, never enlarged automatically. |
| `grid_points` | 7 | Uniform initial grid including zero and upper limit; add 50/200 when in range. |
| `gamma_tolerance` | 1 | Desired spacing next to observed local minima; not a global error bound. |
| `max_refinement_rounds` | 8 | Cap on midpoint refinement rounds; unfinished local resolution is reported. |
| `design_samples` | 8 | IID draws shared by all candidates; at least two. |
| `validation_samples` | 8 | Independent draws evaluated only at the selected gamma; at least two. |
| `seed` | 20261015 | Fresh SeedSequence children: design 0, validation 1, delivery 2. |

`--seed` overrides this optimizer seed. The ordinary scenario seed is unused
in this mode. A single outer experiment entails multiple candidate/draw ODE
runs, followed by one actual delivery event. These small sample counts are an
initial functional check; independent validation intervals expose sampling
uncertainty and do not certify optimality. A larger study is a separate action.

Offers/awards/roles are frozen before sampling. Every eligible FI response uses
the same candidate gamma; rho, physical capability, FCR gains and dynamics stay
configured. Optimization mode does not require a redundant per-provider manual
gamma: zero is used during clearing, which is independent of gamma. Fixed-gamma
ordinary mode still requires explicit provider coefficients.

Grid evaluation is followed by midpoint refinement beside every observed local
minimum (plus the best candidate). Exact ties choose smaller gamma. All tested
values compete with endpoints, without a convexity/unimodality assumption.
Report the best tested candidate, local spacing and boundary status; narrow
unobserved minima can be missed. Validation does not refine or choose gamma.
The two extreme design FI-capacity draws (one if identical) receive tighter
solver checks at the selected gamma, using the same provider clock and draws.
Failed realizations/checks invalidate the run; no selective sample dropping.

Outputs under the selected directory:

- `effective_config.json`, `operating_point.json`, `market.json`: replay inputs,
  cleared allocation/roles and physical parameters. Replay with `--tso-optimize`.
- `availability_samples.csv`, `sample_outcomes.csv`: design/validation draws;
  all candidate metrics, per-draw J and wall/CPU times, plus the delivery outcome.
- `gamma_summary.csv`, `summary.json`: separate E_f, net/gross FI money, cost,
  saturation and J, means/SE/Student-t 95% intervals, search settings/status/counts.
  J is composed per sample before uncertainty estimation to retain covariance.
  Design intervals do not correct selection bias; one delivery has no SE/interval.
- `numerical_checks.csv`: selected-candidate solver refinement.
- `selected_config.json`, `delivery_availability.json`: announced gamma and the
  actual third-stream draw. A fixed-gamma replay must also reuse this saved draw;
  invoking ordinary mode alone uses its own seed and does not reproduce delivery.
- `time_series.csv`, `fi_decisions.csv`, `fi_time_series.csv`, `fi_outcomes.csv`:
  the single delivered event, including actual powers and FI ledgers.
- `run_status.json`: authoritative completion/failure flag when reusing a folder.

Timings use both `perf_counter` wall time and process CPU time. Setup, banks,
candidate evaluations, numerical checks, validation, delivery and exports are
recorded. Search timing includes checkpoint exports; export totals overlap it
and must not be added again. Total excludes Python/module startup and final
summary writes. No plots are generated in optimization mode. FCR block payments
remain separate from event FI money. Interpretation is conditional on the
configured parameters, disturbance, sample bank and user weight.

## Comparing TSO weights

On 15 October the user selected a first comparison of three frequency weights,
before changing availability laws. The demonstration uses 5000, 10000 and
20000 EUR/(Hz² s). All other machine, market, disturbance, horizon, numerical
and sample-bank settings remain fixed. These are preference scenarios, not
three different physical systems or calibrated market valuations.

Reuse the existing central run, and compute only the two additional weights:

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --config results/tso_optimization/effective_config.json --tso-optimize --frequency-weight 5000 --output-dir results/tso_weights/wf5000
.\.venv\Scripts\python.exe scripts/run_fcr.py --config results/tso_optimization/effective_config.json --tso-optimize --frequency-weight 20000 --output-dir results/tso_weights/wf20000
.\.venv\Scripts\python.exe scripts/run_fcr.py --tso-report results/tso_weights/wf5000 results/tso_optimization results/tso_weights/wf20000 --output-dir results/tso_weights
```

`--tso-report` reads completed optimizer output directories, and accepts no
config/scenario/seed overrides or other simulation mode. It rejects differences
in physical/economic settings, search settings other than weight, cleared market,
availability banks, delivery draw or trajectory output times. Input weights must
be distinct. Put the report outside the individual run directories; its folder
may contain new runs as subdirectories. `comparison.json` references all inputs,
so the existing 10000 run is not duplicated. Keep those referenced directories.

The report recomputes only gamma-zero references: once for each saved validation
draw and once for the common delivery draw (nine integrations with N=8). It
verifies that current preparation reproduces the saved market and physical
parameters. It does not optimize gamma again or use validation to select it.
The FCR-only reference retains the same market and availability; FI commands
and transfers are zero at gamma zero.

Outputs include `frequency`, `fi_response`, `fi_finances` and
`provider_responses`, each as a vector PDF and PNG preview in Week 5 style.
These show the **same single delivery realization** for all three policies;
they are not mean trajectories. Capacity lines are read from saved availability,
not hard-coded from old slides. The FI response figure separates held target
and delivered power and zooms into the first decisions. Financial panels use a
common monetary scale and retain actual net payment, activation cost and profit.

`comparison_summary.csv` separates validation means/uncertainty from delivery
outcomes. `baseline_samples.csv` and `baseline_delivery.csv` save the matched
reference, while `paired_baseline_differences.csv` contains per-draw reductions
in E_f and in each policy's own weighted J. Do not rank absolute J values across
different w_f: they represent different valuations. Small N=8 results are
exploratory; the same draws permit controlled comparisons, not a global policy
claim. `report_status.json` must be complete before using the output.

## TSO trade-off curve

The separate `--tso-pareto SWEEP_DIR --output-dir STUDY_DIR` replays a completed
sweep to add RoCoF, terminal-window diagnostics and metric-specific Pareto
reports while preserving the source. `--tso-pareto-report STUDY_DIR` regenerates
reports without integrations. See [PARETO_STUDY.md](PARETO_STUDY.md) for definitions,
diagnostic thresholds, numerical checks and the immutable-input protocol.

On 15 October the user selected the weighted objective
`w_f * E[E_f] + E[S_FI]`, with w_f supplied as an input. This is an accepted
design decision implemented by `--tso-optimize` above. The sweep interface
below remains available separately; see [TSO_DESIGN.md](TSO_DESIGN.md).

Use the same runner with `--scenario fi_endogenous --tso-sweep --output-dir
results/tso_tradeoff`. This cannot be combined with `--compare`. The base
FCR-only case has no eligible FI provider and is rejected for this mode.

The `[tso]` table contains exactly four computational settings:

| Setting | Initial study | Meaning |
|---|---|---|
| `gamma_values` | 0, 10, 25, 50, 75, 100, 150, 200, 300 | Distinct finite nonnegative candidates, including zero; common to all eligible FI providers. |
| `design_samples` | 64 | IID availability realizations for the exploratory curve, at least two. |
| `validation_samples` | 64 | Independent realizations evaluating the same predeclared candidates, at least two. |
| `seed` | 20261014 | Root for NumPy SeedSequence children 0 (design) and 1 (validation). In sweep mode `--seed` overrides this root, not `scenario.seed`. |

These settings specify an exploratory computation, not calibrated market
parameters, a selected optimum, or an actual future delivery draw. Ordinary
fixed-gamma runs ignore `[tso]`. Sweep mode overrides gamma in the FI response
parameters for each candidate; rho, delays, awards and membership stay fixed.
The current `fi_endogenous` preset has one eligible FI provider.

The configured physical scenario supplies the 10 MW event at 1 s, horizon
0–40 s, output spacing 0.05 s and FI decision interval 0.1 s. Numerical maximum
step defaults to output spacing unless explicitly configured. Frequency error
is an ODE-integrated ledger, not a trapezoidal integral of CSV samples. Changing
only output spacing with a fixed solver maximum step leaves endpoint ledgers
unchanged. Sampled nadir still depends on output spacing.

Sweep integration uses rtol = 1e-11 and atol = 1e-13. Numerical refinement
divides both by ten and halves the maximum step. These tighter sweep tolerances
resolve a near-saturation FCR interpolation overshoot found in the initial
study; physical response bounds retain the existing 1e-8 MW check tolerance.

Files in the output directory:

- `effective_config.json`, `operating_point.json`, `market.json`: reproducible
  inputs, cleared awards, original offers and resolved roles;
- `availability_samples.csv`: all providers' actual study draws by bank/sample;
- `sample_outcomes.csv`: one row per bank/sample/gamma, checkpointed after each
  completed candidate, with error, transfers, cost, frequency and saturation;
- `gamma_summary.csv`: means, standard errors, pointwise Student-t 95% intervals,
  paired improvements versus zero and the previous gamma, empirical Pareto flags;
- `numerical_checks.csv`: tighter integration on extreme FI availability draws
  at zero, the candidate nearest 50, and the upper grid endpoint;
- `tradeoff.png`: one figure with frequency/expenditure and target saturation;
- `summary.json`: counts, solver settings, environment and separate preparation,
  design, validation, refinement and export timings;
- `run_status.json`: running/complete/failed; a failed or incomplete run is not
  a valid curve, even if partial checkpoints or older summaries exist.

`target_bound_fraction` counts post-event provider-time at a nonzero symmetric
target bound, with absolute comparison tolerance 1e-9 MW. `any_target_bound`
indicates at least one such interval per realization. Zero availability is
reported separately; delivered power need not equal a saturated held target.
Confidence intervals concern means, not individual event guarantees, and are
pointwise rather than simultaneous. No failed realization is silently dropped.
Reuse a saved effective config with `--tso-sweep` to reproduce a study.

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
try the existing FCR offer optimizer and enter FI on a zero FCR award, whether
the offer was zero or a positive bid was rejected.
Both use gamma 50 and rho 1. `fi_strong` uses the same endogenous rule with
gamma 200. These are illustrative coefficients, not calibrated market prices.
The base 1 MW minimum makes the uncertain renewable opt out in both endogenous
cases; lowering the minimum or changing penalties can change that outcome.

To enable FI directly in a provider's existing `[[providers]]` table, add:

~~~toml
participation = "fi"  # or "fcr_or_fi" for FI after a zero FCR award
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
| Provider `participation = "fcr_or_fi"` | FI after a zero FCR award, including rejected positive offers; any positive/partial award remains FCR. |
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
fixed modes. `fcr_or_fi` works with optimized or fixed offers and normal or fixed
allocation: the award determines the role. Prescribed zero offers are not
labelled voluntary opt-outs; rejected positive offers retain their bid records.
Roles are determined after clearing and before the draw. Zero availability
does not move a provider with a positive award into FI. To allow a currently
FCR-only provider to use the fallback, set `participation = "fcr_or_fi"` and
specify its gamma and rho explicitly; FI coefficients are not inferred.
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

## TSO sampling stability

Use `--scenario fi_endogenous --tso-stability --frequency-weight 10000
--output-dir results/tso_stability` with the existing runner. The weight remains
explicit and the output directory must be empty, so stale complete results
cannot be confused with an interrupted study. `--seed` and `--compare` are
incompatible with this mode; edit the explicit seed list instead.

The opt-in `[tso_stability]` computational settings are:

| Key | Initial study | Meaning |
|---|---|---|
| `sample_sizes` | `[8, 32, 128]` | Increasing design-bank sizes, nested within each seed |
| `seeds` | `[20261015, 20261016, 20261017]` | Independent design streams, child 0 |
| `holdout_samples` | `256` | Common independent assessment size |
| `holdout_seed` | `20261018` | Separate root seed, child 1 |
| `reference_gamma` | `52.34375` | Previous N=8 selection, fixed before assessment |
| `workers` | `3` | Process workers for independent seed jobs and held-out policies |

All nine searches use the existing `[tso_optimizer]` bounds/grid/refinement and
unchanged simulation tolerances. Its ordinary `design_samples`,
`validation_samples` and `seed` do not control this study; the table above does.
Exact gamma/draw outcomes are cached within each seed. An N-sample objective
always averages its first N draws; caching never substitutes a larger-bank mean.
The operating point, market and participation are solved once and fixed.

After all searches finish, the union of their selected gammas and the reference
is frozen in `frozen_candidates.json`. Only then is the holdout drawn and
assessed. It must not be reused to select/refine gamma. No new delivery event
or trajectory plot batch is produced. Numerical checks compare ordinary and
tighter integration at the largest-N selection, using the two extreme design
FI-capacity draws for each seed. A failure invalidates the study.

Outputs include the protocol/effective configuration, market and operating point,
design/holdout draws, per-seed cached outcomes and selections, held-out outcomes,
`selection_summary.csv`, `holdout_summary.csv`, numerical checks, wall/CPU
timings and one `stability.png`/`.pdf` figure. Generated data stay under `results/`.
The held-out comparison uses each draw's difference
`w_f*(E_f(gamma)-E_f(reference)) + S(gamma)-S(reference)` and pointwise Student-t
95% intervals. These are not simultaneous intervals, equivalence tests or regret
against the unknown true optimum. Three seeds cannot establish convergence or
generalize beyond the configured system. No practical-loss tolerance is implied.

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

## Source-inspired fleet comparison (16 October 2026)

`configs/fleet_scenarios.toml` is an opt-in manifest resolving the existing
`fi_endogenous` preset. It changes only physical provider fields; the original
`configs/fcr.toml` numerical baseline is unchanged. See
[FLEET_STUDY.md](FLEET_STUDY.md) for exact values, source locators, reductions,
common economics, dispatch checks and reproduction commands.

Use `--tso-fleet --config configs/fleet_scenarios.toml --output-dir
results/fleet_scenarios`. Each fleet chooses its own common gamma using 32
availability draws, then evaluates it on 32 independent draws. Draws are matched
across fleets. The separate `--tso-fleet-report results/fleet_scenarios` mode
regenerates the five plots and accounting tables from the saved runs. Outputs
include a frozen resolved plan and per-case timings/numerical checks. FCR block
payments and FI event settlements are never extrapolated to the same horizon.
