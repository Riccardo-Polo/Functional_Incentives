# Functional Incentives for Frequency Control

ETH Zürich semester project. The implementation covers **provider decisions,
the FCR capacity market, functional incentives (FI), and coupled frequency
dynamics**. FI providers optimize their own commands at each decision time.
Opt-in studies evaluate a common-gamma sweep, the weighted TSO objective
`w_f * E[E_f] + E[S_FI]`, sampling stability, fleet and FCR-procurement changes,
and metric-specific Pareto diagnostics. A separate AEMO-inspired benchmark
compares fixed FCR, FI and FPP policies on matched draws.

The [post-Week-5 work plan](docs/NEXT_STEPS.md) records the supervisor feedback,
the FI payment/cost and saturation study, parameter calibration, and the staged
path to repeated market cycles. The
[parameter evidence register](docs/PARAMETER_SOURCES.md) separates sourced
inputs from the current pedagogical assumptions. Calibration and repeated market
cycles remain planned extensions; [TSO_DESIGN.md](docs/TSO_DESIGN.md) records the
implemented objective, search method and limits of the numerical conclusions.
The next research task is **FPP provider optimization**; its objective,
information timing and economic inputs are open. See
[AEMO_FPP.md](docs/AEMO_FPP.md#next-research-step-fpp-provider-optimization).

**Repository language:** all project-authored code, comments, configuration labels,
documentation, local notes, reports, figures and filenames must remain in English.
This also applies to ignored project outputs. Original third-party sources and
proper names retain their original wording; conversation language may follow the user.

## Run

The [AEMO-inspired FPP benchmark](docs/AEMO_FPP.md) compares FCR alone,
bilinear FI at gamma=50 and fixed-droop FPP over matched 300-second events.
The current metered study uses `--fpp-benchmark --config
configs/aemo_fpp_metered.toml --output-dir results/aemo_fpp_metered`.
Meters read actual delivered power; a prescribed balanced dispatch ramp is a
settlement reference only. The study includes auditable directional settlement,
RoCoF, numerical checks, eight matched assessment draws and an independent
delivery. It reports FPP repricing coefficients in MW h because external prices
are absent. Use `--fpp-report results/aemo_fpp_metered` for report regeneration.
The earlier constant-reference study and the separate deficit-to-surplus trial
remain available in their original result directories.

The [Pareto study](docs/PARETO_STUDY.md) extends a completed common-gamma sweep
with cost versus nadir, terminal error, integrated error and RoCoF, plus tail
and saturation diagnostics. Use `--tso-pareto results/tso_tradeoff --output-dir
results/tso_pareto`; the original study is preserved. Subsequent
`--tso-pareto-report results/tso_pareto` regenerates figures without simulations.

The [FCR requirement study](docs/FCR_REQUIREMENT_STUDY.md) compares 12, 10 and
8 MW procurement within each preserved A/B/C fleet, changing no other inputs.
Use `--tso-procurement --config configs/fcr_requirement.toml --output-dir results/fcr_requirement`,
then `--tso-procurement-report results/fcr_requirement`. Previous runs are
referenced in place; FCR block payments and FI event payments remain separate.

Python 3.11+ is required. From the repository root in PowerShell:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe scripts\run_fcr.py
~~~

There is one runner and one default configuration:
[configs/fcr.toml](configs/fcr.toml). The default fleet has three conventional
and two renewable generators on the PJM 5-bus network.

~~~powershell
# Base scenario plus all configured FCR/FI comparisons
.\.venv\Scripts\python.exe scripts\run_fcr.py --compare

# One named scenario
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario lower_uncertainty --output-dir results\lower_uncertainty

# FI for eligible providers with zero FCR award (opt-out or rejected bid)
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario fi_endogenous --output-dir results\fi_endogenous

# Different parameters, seed or destination
.\.venv\Scripts\python.exe scripts\run_fcr.py --config configs\fcr.toml --seed 123 --output-dir results\my_run

# Common-gamma curve with independent design/validation availability banks
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario fi_endogenous --tso-sweep --output-dir results\tso_tradeoff

# Weighted TSO optimization; 10000 is a demonstration weight, not calibrated
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario fi_endogenous --tso-optimize --frequency-weight 10000 --output-dir results\tso_optimization

# Sampling stability: nested banks, three seeds, then a common independent holdout
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario fi_endogenous --tso-stability --frequency-weight 10000 --output-dir results\tso_stability

# Current test suite
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

The sweep uses `[tso]` computational settings in the same configuration. It
freezes the cleared market, reuses each bank across gamma values, and saves one
trade-off figure, sample outcomes, mean/paired statistics, numerical checks and
wall times under the chosen ignored `results/` directory. No frequency weight
or budget is assigned automatically. See [configuration details](docs/CONFIGURATION.md#tso-trade-off-curve).

Optimization uses `[tso_optimizer]`: initially 8 design and 8 validation draws,
gamma in [0, 300], a coarse grid and refinement around observed minima. Only
design draws select gamma. The chosen gamma is validated, checked with tighter
numerics and applied to one fresh delivery draw. Weight is mandatory on the CLI
or in the configuration. Outputs include weighted uncertainty, boundary/search
status, wall and CPU times; no figure batch is generated. These small sample
counts verify the workflow, not statistical precision or a universal policy.
See [optimizer configuration](docs/CONFIGURATION.md#weighted-tso-optimization).

To compare different user weights, run the optimizer with the same effective
config/seed and a different `--frequency-weight`, then pass the completed run
directories to `--tso-report`. It checks that all other inputs and draws match,
adds gamma-zero references on the saved validation/delivery draws, and exports
four Week-5-style PNG/PDF figures plus separate validation statistics. Existing
run directories are referenced, not copied. See
[weight comparisons](docs/CONFIGURATION.md#comparing-tso-weights).

`--tso-stability` repeats the same gamma search on increasing nested design banks
and several seeds, using `[tso_stability]`. After every candidate is fixed, it
assesses them on a common independent bank and reports paired objective
differences against a predeclared reference gamma. This separates variability
of the selected gamma from changes in performance. It does not choose a new
policy on the holdout. See [sampling stability](docs/CONFIGURATION.md#tso-sampling-stability).

The completed initial study at w_f=10000 selected 46.875/46.875/45.3125 across
three seeds with N=128, compared with 52.34375/49.21875/42.1875 at N=8.
On 256 independent common draws, gamma=46.875 improves mean J by only
0.7167 EUR/event (0.2066%) over the earlier 52.34375. This is a conditional
sampling diagnostic, not a calibrated policy. Seven targeted tests passed;
the report and one PNG/PDF figure pair are under `results/tso_stability/`.

## Experiment with the simulation

You can create different simulations by editing [configs/fcr.toml](configs/fcr.toml);
no Python changes are needed. The `[scenario]` table controls the event and
simulation, `[market]` controls procurement, and each `[[providers]]` table
describes one provider. Find the desired provider by its `provider_id` before
editing its characteristics. The parameter values are illustrative assumptions.

### Change a parameter and rerun

First save a baseline so you can compare the results:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_fcr.py --output-dir results\baseline
~~~

Then edit an existing value in the TOML, for example change
`power_deficit_mw = 10.0` to `power_deficit_mw = 15.0` under `[scenario]`.
Rerun into a different directory:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_fcr.py --output-dir results\larger_event
~~~

Compare the two `frequency_comparison.png` plots and `summary.json` files.
For economic changes, also compare `market_outcomes.csv`: it records offers,
awards, availability, payments and profit. Change one parameter at a time
initially, and keep the same seed to make the comparison easier to interpret.
Reusing an output directory replaces its generated files.

### Choose what to vary

In this table, `scenario.x` means the key `x` inside `[scenario]`, and
"Provider" means a field in the relevant `[[providers]]` entry. Change existing
keys rather than adding a second copy of the same TOML table or key.

| Experiment | Where to edit | Example and interpretation |
|---|---|---|
| Larger disturbance | `scenario.power_deficit_mw` | Change 10 to 15 MW. A positive deficit lowers frequency; reserve procurement stays at its configured requirement. |
| Later event or longer observation | `scenario.event_time_s`, `scenario.final_time_s` | Start at 5 s or observe until 60 s; final time must be after the event. |
| More frequent output samples | `scenario.output_time_step_s` | Change 0.05 to 0.02 s for finer recorded trajectories. |
| Different availability realization | `scenario.seed`, or CLI `--seed` | Use `--seed 123`. The draw changes; optimized offers are chosen before the draw. |
| More or less reserve procurement | `market.requirement_mw` | Change 12 to 10 MW; use 0 for zero awards and no FCR delivery with normal clearing. |
| Allow smaller positive bids | `market.minimum_bid_mw` | Change 1 to 0.1 MW. Small profitable offers may then replace voluntary opt-out; partial awards can already be below the minimum bid. |
| Different bidding prices | Provider `minimum_bid_price_eur_per_mw_block`, `maximum_bid_price_eur_per_mw_block` | Change a provider's ceiling from 20 to 25. Positive optimized bids always use the ceiling because the reward has no acceptance model. |
| More expensive reserve commitment | Provider `linear_cost_eur_per_mw_block`, `quadratic_cost_eur_per_mw2_block` | Increase either coefficient in `C(q) = a*q + 0.5*beta*q^2` and inspect the resulting offer. |
| Different shortage penalties | Provider `penalty_eur_per_missing_mw_block` | Change 200 to 100 EUR per missing MW per block. |
| Different reserve uncertainty | Provider `mean_mw`, `variance_mw2` | For `renewable_uncertain`, change variance from 4 to 0.04 MW². Variance is the square of standard deviation; these moments describe the underlying Gaussian before bounding. |
| Deterministic availability | Provider `variance_mw2`, `mean_mw` | Set variance to 0 and mean to 2 MW for exactly 2 MW available in every block. |
| Different availability boundaries | `availability.boundary_mode` | Use `"truncated"` or `"clipped"`; clipping creates probability masses at zero and the physical cap. |
| Withhold reserve from bidding | Provider `reserve_safety_margin_fraction` | Change 0 to 0.1: a physical cap of 4 MW gives a bid ceiling of 3.6 MW. Physical availability retains its original cap. |
| Change physical capability or dispatch | Provider `reserve_capacity_mw`, `minimum_power_mw`, `maximum_power_mw`, `power_setpoint_mw` | Change these together consistently: symmetric reserve must fit both margins around solved dispatch. The slack setpoint is only an initialization guess. |
| Faster or slower provider response | Provider `time_constant_s` | Change a renewable's 0.25 to 0.1 s for a faster response to its droop request, then inspect frequency and provider-response plots. |
| Stronger or weaker droop response | `scenario.droop_pu` | Change 0.05 to 0.04 for stronger unsaturated response. Reserve limits still apply. |
| Less synchronous inertia | Conventional provider `inertia_s` | Change `conventional_1` from 5 to 2.5 s and inspect the initial frequency fall. Renewable inertia stays zero in this model. |
| Different machine or converter size | Provider `rating_mva` | The rating sets the droop power base and, for conventional machines, contributes to inertia. Active-power limits remain separate inputs. |
| Different load sensitivity | `scenario.load_frequency_sensitivity` | Change 1 to 0.5 to reduce frequency-dependent load damping. |
| Stronger/weaker FI payment | Provider `fi_gamma_eur_per_hz_mw_s` | Change 50 to 100 in an FI scenario; gamma stays fixed during the event. |
| More expensive FI activation | Provider `fi_rho_eur_per_mw2_s` | Increase the positive coefficient of delivered-power cost `rho*z²/2` in EUR/s. |
| Faster/slower FI decisions | `fi.decision_interval_s` | Change 0.1 to 0.5 s; frequency samples and commands are held between decisions. |
| Refine numerical integration | `scenario.maximum_solver_step_s` | Set 0.02 s explicitly; this does not change the FI decision clock. |

FCR prices, cost coefficients and penalties apply to the **whole delivery block**.
Changing `market.block_duration_hours` does not automatically rescale them or
change the simulated event horizon. The additional bidding safety margin
defaults to zero; physical headroom is already enforced. If its bid ceiling
falls below the minimum bid, that provider offers zero. If total offered supply
falls below the market requirement, the run stops with an insufficient-reserve
error. Adjust the scenario's demand or provider assumptions to study a feasible
case; the simulator does not procure a partial requirement automatically.

### Play with functional incentives

The base scenario preserves the FCR-only experiment. Three additional scenarios
at the end of the TOML enable FI for `renewable_uncertain`:

| Scenario | Participation rule | Gamma |
|---|---|---:|
| `fi_exogenous` | Assigned to FI before bidding | 50 |
| `fi_endogenous` | FI only after an optimized zero FCR offer | 50 |
| `fi_strong` | Same endogenous rule | 200 |

Edit `fi_gamma_eur_per_hz_mw_s` in the selected comparison to vary incentive
strength. For example, change 50 to 100 in `fi_endogenous`, then rerun the
command above into a new output directory. Gamma has units EUR/(Hz MW s).
`fi_rho_eur_per_mw2_s = 1.0` sets the activation-cost rate coefficient and must
be positive. These values are pedagogical assumptions.

To configure participation directly, put these fields in an existing provider
table (or its named comparison override):

~~~toml
participation = "fcr_or_fi"
fi_gamma_eur_per_hz_mw_s = 50.0
fi_rho_eur_per_mw2_s = 1.0
~~~

Use `"fi"` for an exogenous assignment, `"fcr"` for the default FCR-only policy,
or `"inactive"` for zero controlled response. With `"fcr_or_fi"`, a **zero FCR
award enables FI**, including after a rejected positive bid. A positive award,
even partial, keeps the provider exclusively in FCR. Roles are fixed after
clearing, before availability is drawn; fixed offers/awards obey the same rule.
The rejected offer remains recorded. This FCR-first fallback does not compare
expected profits from both products.
FI capability is the provider's realized availability, and FI power does not
count toward contracted FCR procurement.

[TSO_DESIGN.md](docs/TSO_DESIGN.md) documents the accepted selection of common
gamma from frequency performance and actual FI expenditure. The trade-off
evaluator and bounded optimizer are implemented through `--tso-sweep` and
`--tso-optimize`; the weighted objective takes an explicit user-supplied `w_f`.
Its demonstration weight does not calibrate a real policy.

The provider observes sampled frequency error `e_k` and its current delivered
power, then chooses a target maximizing predicted payment minus activation cost
over the next decision interval. Its first-order dynamics determine actual
power `z(t)`. Settlement integrates `-gamma * e_k * z(t)` over time; it can
reward or charge the provider. Gamma is held fixed within each simulated event.

Inspect `provider_responses.png` for command versus delivery and
`fi_financials.png` for the corresponding money. In `fi_outcomes.csv`:

- `target_benchmark_eur` assumes immediate delivery of every command.
- `predicted_settlement_eur` accounts for known response lag.
- `net_settlement_eur` integrates actual delivery, subtracting gross penalties
  from gross rewards. `profit_eur` also subtracts activation costs.

Predicted and actual settlement agree within solver tolerance with the current
perfect-information assumptions. FI amounts cover the simulated event, whereas
FCR financial quantities cover the whole delivery block. The model has no TSO
outer optimization, secondary restoration or energy-storage constraints.
Larger gamma is not guaranteed to improve frequency or profitability.

The `[fi]` decision interval is independent of output sampling and the RK45
step limit. Set `scenario.maximum_solver_step_s` explicitly when changing the
output grid alone; its backward-compatible default is the output step.
The [configuration guide](docs/CONFIGURATION.md) explains timing, all financial
outputs, fixed modes and how to add a gamma comparison.

### Save experiments as named scenarios

To keep several experiments in one file, append `[[comparisons]]` entries to
the **end** of `configs/fcr.toml`. For example:

~~~toml
[[comparisons]]
name = "larger_event"
label = "15 MW deficit"
scenario = { power_deficit_mw = 15.0 }

[[comparisons]]
name = "faster_renewables"
label = "Faster renewable response"
providers = { renewable_reliable = { time_constant_s = 0.1 }, renewable_uncertain = { time_constant_s = 0.1 } }

[[comparisons]]
name = "reserve_margin"
label = "10 percent bidding margin"
all_providers = { reserve_safety_margin_fraction = 0.1 }
~~~

Each scenario starts from the base values in the file, independently of the
other comparisons. A `market = { requirement_mw = 10.0 }` entry overrides market
settings; `scenario`, `decision`, `availability` and `fi` work the same way.
`all_providers` changes every provider, while `providers` changes the named IDs
and takes precedence over common changes. For a common penalty policy, use
`all_providers = { penalty_eur_per_missing_mw_block = 100.0 }` inside a comparison.
Use unique names made of lowercase letters, digits, underscores or hyphens;
`base` is reserved. Labels must also be unique.

~~~powershell
# Run one of the appended scenarios
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario faster_renewables --output-dir results\faster_renewables

# Run base plus every comparison, with combined tables and a comparison plot
.\.venv\Scripts\python.exe scripts\run_fcr.py --compare --output-dir results\experiments

# Repeat the comparison with a different seed for all cases
.\.venv\Scripts\python.exe scripts\run_fcr.py --compare --seed 123 --output-dir results\experiments_seed123

# Replay the exact saved inputs of a previous run
.\.venv\Scripts\python.exe scripts\run_fcr.py --config results\experiments\base\effective_config.json --output-dir results\replay
~~~

For comparisons, open `scenario_comparison.png` to see frequency and offers,
`comparison_summary.csv` for prices and frequency metrics, and
`comparison_providers.csv` for provider-level outcomes. Keeping the same seed
and availability laws preserves the draws across cases; changing a law can
change its provider's draw. Try several seeds before interpreting a result as
a general effect.

For prescribed offers, use `decision.mode = "fixed"` with per-provider
`fixed_bid_eur_per_mw_block` and `fixed_offer_mw`. For prescribed awards, use
`market.mode = "fixed"` with `fixed_price_eur_per_mw_block` and per-provider
`fixed_award_mw`. Worked examples and the corresponding constraints are in
[the configuration guide](docs/CONFIGURATION.md).

## Workflow

1. Solve the operating point and check each provider's physical headroom.
2. Choose $(c_i,\bar q_i)$ continuously using commitment cost and expected
   Gaussian reserve-shortage penalties, the minimum bid and safety margin.
3. Clear the FCR market for awards $q_i^\star$ and a uniform price.
4. Draw availability once per provider.
5. Simulate FCR response with $q_i^{\mathrm{eff}}=\min(q_i^\star,A_i)$, FI
   decisions and delayed response within $[-A_i,A_i]$, and their common frequency.
6. Save decisions, payments, penalties, profit, trajectories and numerical checks.

The equations, units, assumptions and current results are in
[docs/MODEL.md](docs/MODEL.md). Configuration examples, including fixed offers,
fixed awards, deterministic availability and earlier parameter sets, are in
[docs/CONFIGURATION.md](docs/CONFIGURATION.md). All use the same implementation.

## Current FCR baseline

With the default seed 20261008:

| Provider | Offer [MW] | Award [MW] |
|---|---:|---:|
| Conventional 1 | 3.945482 | 3.945482 |
| Conventional 2 | 3.947217 | 3.947217 |
| Conventional 3 | 3.950723 | 2.407773 |
| Reliable renewable | 1.699528 | 1.699528 |
| Uncertain renewable | 0 | 0 |

The market procures 12 MW at **24 EUR/MW for the whole four-hour block**.
The sampled frequency nadir is **59.919129 Hz** and the final frequency is
**59.974692 Hz**.

The uncertain renewable opts out because its expected penalty makes every
feasible positive offer unprofitable. This example uses a **1 MW minimum
positive offer**. Smaller bids could remain profitable. The offer objective
also selects the highest allowed price for positive quantity; it does not
predict auction acceptance or clearing prices.

Provider price intervals, costs, uncertainty, inertia, response times, reserve
caps and `reserve_safety_margin_fraction` are configurable in the TOML. The
safety margin defaults to zero and limits bids after physical headroom checks;
it does not change the availability law. `market.minimum_bid_mw` sets the market
minimum. Each comparison can override configuration sections, all providers,
or individual provider IDs. The configured cases compare base with lower
renewable uncertainty and three FI policies. See the configuration guide for
examples and fixed modes.

## Outputs

Single runs write to `results/fcr/` by default. With `--compare`, each scenario
gets a subdirectory and the parent contains combined tables and a comparison
figure. Reusing a destination replaces its generated files.

| File | Contents |
|---|---|
| `effective_config.json` | Actual parameters and seed; can be passed back to `--config` |
| `operating_point.json` | Solved dispatch, ratings and inertia |
| `market_outcomes.csv` | Participation roles, offers, awards, draws, effective reserve and FCR block accounting |
| `offer_rewards.csv` | Diagnostic reward at zero, feasible endpoints and the selected offer; not a search grid |
| `time_series.csv` | Frequency, separate FCR/FI totals, requested/delivered responses and generator powers |
| `fi_decisions.csv` | Every FI decision with interval benchmark, prediction and actual settlement |
| `fi_time_series.csv`, `fi_outcomes.csv` | Individual and aggregate FI rates, balances and final profit |
| `summary.json` | FCR price/payments, aggregate FI amounts and numerical checks |
| `frequency_comparison.png`, `provider_responses.png` | Physical trajectories |
| `fi_financials.png` | FI signed rates, settlement comparisons, costs and profit |
| `comparison_summary.csv`, `comparison_providers.csv`, `scenario_comparison.png` | Additional outputs for `--compare` |

Generated results are ignored by Git.

## Source-inspired fleet comparison

The fleet comparison uses the same solver and optimizer:

```powershell
.venv/Scripts/python.exe scripts/run_fcr.py --tso-fleet --config configs/fleet_scenarios.toml --output-dir results/fleet_scenarios
.venv/Scripts/python.exe scripts/run_fcr.py --tso-fleet-report results/fleet_scenarios
```

Three mixed thermal/PV fleets, common economics and w_f=10000, 32 design
and 32 independent validation draws per fleet. Five figures show frequency,
all provider responses, FI targets/availability, FI finances and TSO spending.
FCR is per four-hour block; FI is per 40-second event. See
[individual machine parameters](docs/FLEET_STUDY.md#individual-machine-parameters),
including common availability/economic inputs and source provenance.
Completed cases can be reused only with an identical frozen plan; report-only
mode does not repeat optimization. Partial or conflicting runs are preserved
and rejected rather than overwritten.

Completed A/B/C results: gamma 33.59375 / 40.625 / 33.59375; independent
validation mean nadirs 59.91528 / 59.90226 / 59.86921 Hz and mean FI payments
37.16 / 63.97 / 67.17 EUR/event. FCR capacity costs 288 EUR/block in each.
Seven targeted tests and six numerical refinement checks passed; final
verification on 17 October. The full report is `results/fleet_scenarios/ANALYSIS.md`.

## Code map

The package has one module per responsibility; there are no stage-specific
implementations or empty incentive packages.

| Module in `src/functional_incentives/` | Responsibility |
|---|---|
| `grid.py` | ANDES operating point, configurable fleet and inertia |
| `availability.py` | Gaussian sampling and expected shortage |
| `providers.py` | Capability, FCR/FI dynamics, fixed incentive and cost parameters |
| `decision.py` | FCR offer optimization and exact FI interval best response |
| `incentives.py` | FI rates, analytical payment prediction and actual financial records |
| `market.py` | Clearing, explicit fixed allocation and settlement |
| `frequency.py` | Frequency equation and analytical no-FCR reference |
| `simulation.py` | Block sequencing and coupled numerical dynamics |
| `tso.py` | Common-gamma evaluation/search, independent validation and delivery |
| `fleet.py` | Controlled physical comparisons and saved-result reports, reusing the existing optimizer |
| `procurement.py` | FCR-requirement comparisons on the preserved fleets |
| `pareto.py` | Matched gamma replay and metric-specific Pareto diagnostics |
| `fpp.py` | Fixed FPP control, metering, filtering and settlement |
| `fpp_study.py` | Matched FCR/FI/FPP benchmark and saved-result reports |
| `metrics.py` | Numerical checks and event/Monte Carlo statistics |
| `plotting.py` | Trajectory, comparison and trade-off figures |
| `experiment.py` | Configuration, orchestration, output and command line |

There are five focused test files covering decisions, market, dynamics,
operating points and the complete experiment. The full suite passed **107 tests**
on 28 October 2026 after the repository audit. The metered
study has 33 integrations including three numerical refinements and saves
physical, settlement and source-preservation checks under
`results/aemo_fpp_metered/`. The Week 7 Beamer source, compiled PDF and audit
are under `results/week7/`; generated results are ignored by Git.
With the default seed, gamma 50 improves the sampled nadir from 59.919129 to
59.948176 Hz and yields FI net settlement of EUR 61.4103 and profit of EUR 30.7072
over 40 seconds. FCR awards and its 24 EUR/MW/block price stay the same.
These are single-event illustrative outcomes, not a general policy result.
The 15 October weighted optimizer passed **44 dynamics/experiment tests**.
One five-provider run with w_f=10000 selected gamma=52.34375 on eight design
draws, assessed it on eight independent validation draws and simulated one
fresh delivery event. It took 82.46 s wall / 71.17 s CPU (163 ODE runs including
two numerical checks). These are conditional demonstration results; the
improvement over gamma 50 in design J was only EUR 0.1623, so the refined
decimal value is not evidence of a statistically distinct policy.

Local research PDFs in `references/`
and notes in `local_context/` are intentionally outside Git. Consult
`local_context/NOW.md` and `DECISIONS.md` when present.
