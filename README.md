# Functional Incentives for Frequency Control

ETH Zürich semester project. The current implementation covers **provider
decisions, the FCR capacity market, and frequency dynamics**.

## Run

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
# Base scenario plus the configured penalty/uncertainty comparisons
.\.venv\Scripts\python.exe scripts\run_fcr.py --compare

# One named scenario
.\.venv\Scripts\python.exe scripts\run_fcr.py --scenario no_penalty --output-dir results\no_penalty

# Different parameters, seed or destination
.\.venv\Scripts\python.exe scripts\run_fcr.py --config configs\fcr.toml --seed 123 --output-dir results\my_run

# Current test suite
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

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
| Different shortage penalties | Provider `penalty_eur_per_missing_mw_block` | Change 200 to 100 EUR per missing MW per block. The existing `no_penalty` comparison sets this to zero for all providers. |
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

Prices, cost coefficients and penalties apply to the **whole delivery block**.
Changing `market.block_duration_hours` does not automatically rescale them or
change the simulated event horizon. The additional bidding safety margin
defaults to zero; physical headroom is already enforced. If its bid ceiling
falls below the minimum bid, that provider offers zero. If total offered supply
falls below the market requirement, the run stops with an insufficient-reserve
error. Adjust the scenario's demand or provider assumptions to study a feasible
case; the simulator does not procure a partial requirement automatically.

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
settings; `scenario`, `decision` and `availability` work the same way.
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
5. Simulate frequency and provider response with
   $q_i^{\mathrm{eff}}=\min(q_i^\star,A_i)$.
6. Save decisions, payments, penalties, profit, trajectories and numerical checks.

The equations, units, assumptions and current results are in
[docs/MODEL.md](docs/MODEL.md). Configuration examples, including fixed offers,
fixed awards, deterministic availability and earlier parameter sets, are in
[docs/CONFIGURATION.md](docs/CONFIGURATION.md). All use the same implementation.

## Current result

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
or individual provider IDs. The `no_penalty` case now removes every provider's
penalty. See the configuration guide for examples and fixed modes.

## Outputs

Single runs write to `results/fcr/` by default. With `--compare`, each scenario
gets a subdirectory and the parent contains combined tables and a comparison
figure. Reusing a destination replaces its generated files.

| File | Contents |
|---|---|
| `effective_config.json` | Actual parameters and seed; can be passed back to `--config` |
| `operating_point.json` | Solved dispatch, ratings and inertia |
| `market_outcomes.csv` | Offers, awards, draws, effective reserve and block accounting |
| `offer_rewards.csv` | Diagnostic reward at zero, feasible endpoints and the selected offer; not a search grid |
| `time_series.csv` | Frequency, requested/delivered responses and total generator powers |
| `summary.json` | Price, payments and numerical checks |
| `frequency_comparison.png`, `provider_responses.png` | Physical trajectories |
| `comparison_summary.csv`, `comparison_providers.csv`, `scenario_comparison.png` | Additional outputs for `--compare` |

Generated results are ignored by Git.

## Code map

The package has one module per responsibility; there are no stage-specific
implementations or empty incentive packages.

| Module in `src/functional_incentives/` | Responsibility |
|---|---|
| `grid.py` | ANDES operating point, configurable fleet and inertia |
| `availability.py` | Gaussian sampling and expected shortage |
| `providers.py` | Capability, cost parameters and droop response |
| `decision.py` | Offer evaluation, exact price optimum and bounded continuous quantity optimization |
| `market.py` | Clearing, explicit fixed allocation and settlement |
| `frequency.py` | Frequency equation and analytical no-FCR reference |
| `simulation.py` | Block sequencing and coupled numerical dynamics |
| `metrics.py` | Bounds, analytical agreement and solver refinement |
| `plotting.py` | Trajectory and parameter-comparison figures |
| `experiment.py` | Configuration, orchestration, output and command line |

There are five focused test files covering decisions, market, dynamics,
operating points and the complete experiment. **All 46 tests pass.** All three
configured scenarios were rerun with regenerated plots. The model guide records
old/new offers, awards, prices and frequency responses. Generated baseline
records are in `results/discrete_baseline/`, with a detailed provider comparison
in `results/fcr/bidding_update_comparison.csv`.

Functional incentives remain future work. Local research PDFs in `references/`
and notes in `local_context/` are intentionally outside Git. Consult
`local_context/NOW.md` and `DECISIONS.md` when present.
