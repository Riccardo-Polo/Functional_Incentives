# Reference-budget replacement and L2 provider power

## Fixed-price coefficient sweep after Week 11 (24 November 2026)

`results/l2_fixed_budget/study.py` implements the requested denser lambda2
comparison at 288 EUR. It uses the existing fixed-allocation market API with
24 EUR/MW per block. Each positive reference award is scaled by q/12, so
provider shares, offers, roles and droop gains stay fixed for q>0. This is a
prescribed allocation experiment, distinct from the earlier merit-order search.
For each of 15 coefficients and settlement periods 0.25/0.30/0.50 s, simulate
the four design availability vectors and four equal-weight profiles and solve
24*q + mean(signed FI transfer) = 288 EUR to 0.001 EUR tolerance. Procurement
is chosen before delivery; it is not adjusted using a realized availability.

The main ordinate is E[integral(delta_f(t)^2 dt)] in Hz² s, using the live
numerical ledger over 40 s. The abscissa is lambda2 in EUR/Hz² per settlement.
The single-target controller runs every 0.1 s, endpoint_only_v1 is explicit,
and the current zero-target replay retains actuator decay. No passive-cost
reimbursement or ideal zero-delivered-power counterfactual is introduced.

The matched budget uses one event per four-hour block as conditional accounting.
Freeze quantities before assessment on the 16 historical validation vectors;
show their actual mean spending and gaps without retuning. These reused draws
are diagnostic, not a fresh untouched holdout. Uncertainty intervals aggregate
profiles within each draw first. Report mean trajectory-peak absolute RoCoF
from the unfiltered model RHS over [0,40] s, including one-sided event/ramp
limits and without measurement averaging. Report signed mean FI payment divided
by mean activation cost, leaving the zero-cost ratio undefined.

Run `study.py`, then `report.py` in that output directory with the project
Python environment. Saved protocol hashes protect cached search results;
the report regenerates figures without integration. Earlier outputs are
preserved in place. This study does not select an optimal lambda2.

## Historical 18 November experiment

The 18 November meeting study uses the traditional fleet, the reduced FI
availability variance and the Week 10 coefficients. It reads the solved ANDES
operating point, physical parameters, design and validation draws, and frozen
policy choice from the completed studies named in `configs/fi_budget.toml`.

The FCR-only reference purchases 12 MW. For each specified profile, reduce
FCR demand and simulate the mixed policy at every candidate. The design target
is the actual reference FCR payment per four-hour block. Add one signed FI
event transfer per block as an explicit conditional accounting assumption.
Find a tested demand at which the four-draw average is within EUR 0.01 of the
reference. Freeze it for all 16 paired validation draws and a separate delivery
example. This compares profile-specific contracts conditional on the event
scenario. A single policy across unknown event types would require an ex ante
mixture and event-count assumption.

New profiles apply a 0.5 s linear transition to each commanded change in
absolute net deficit. This illustrative rise time equals the window length of
a causal moving average of the step command. It acts on the physical plant
input. The frequency observation and 0.1 s decision clock retain their previous
definitions. Solver segments include the ramp corners. The L2 passive replay
sees the same realized forcing. Earlier step experiments retain their original
inputs. The common solver accepts `disturbance_rise_s=0` for exact step replay.

The input comparison evaluates Jonas Matt's Eq. (22) at the same local passive
error and matched one-interval quadratic curvature. It separates the scalar
target, the actuator-memory correction, clipping and delivered MW. The profit
audit reconstructs the profit of a zero command from each inherited state.
The historical saturation check crosses all four FI draws with all four
other-provider vectors at the selected weight and adds intermediate weights
to the original sustained-deficit curve.

Run the full study into a fresh directory, or regenerate a completed report.

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --fi-budget-study --config configs/fi_budget.toml --output-dir results/fi_budget_ramps_reproduction
.\.venv\Scripts\python.exe scripts/run_fcr.py --fi-budget-report results/fi_budget_ramps
```

Results are in `results/fi_budget_ramps/ANALYSIS.md`. Source snapshots, search
samples, exact demands, market awards, paired validation differences, delivery
power traces and numerical refinement comparisons accompany the report.
