# FCR capacity sensitivity up to 20 MW

The user authorized this study on 16 November 2026: vary initial FCR
procurement above 12 MW, screen up to 20 MW and show only trajectory-peak RoCoF.

Completed outputs are under `results/fcr_capacity/original` and `expanded`,
with the overview in `results/fcr_capacity/ANALYSIS.md`. The study contains
8043 new episode integrations, 504 scalar outcomes referenced from the prior
comparison, and 24 passed refinement comparisons (48 further integrations).
Seven targeted protocol/market/pairing/cache/CLI tests pass. Report-only
regeneration preserves scientific inputs/outcomes and performs no integration.

## Separate fleets

Within each fleet, vary demand over 12, 12.5, 13, 13.5, 14, 16, 18 and 20 MW.
The original fleet offers about 13.543 MW; retain all infeasible demands before
sampling, without partial procurement or invented scarcity prices.

The user authorized a separate expanded scenario: the three conventional
reserve capacities increase from 4 to 8 MW and underlying availability means
from 3.98 to 7.98 MW. Variances stay unchanged and the Gaussian remains
continuously truncated to the physical reserve support. This changes reserve
availability and its law, not only procurement demand across fleets. It is a
synthetic scenario assumption, not empirical calibration. The maintained loader
checks both solved active-power margins before admitting each capacity.

Renewable laws, operating point, online inertia, ratings/droop bases, gains,
response times, cost functions, penalties, price intervals, bidding heuristic
and positive-offer minimum stay unchanged. Total commitment cost changes when
the quantity changes. Preserve the original scenario and completed studies.

The expanded fleet offers about 25.538 MW. Its marginal prices are 18 EUR/MW
per block through q=14, 20 at q=16 and 24 at q=18/20. Its q=12 payment is
216 EUR/block, versus 288 in the original fleet. Keep those scenarios distinct.
A zero-award FCR-only machine retains online inertia and zero response; it
does not automatically become FI. The uncertain renewable still offers zero.

## Frozen policies, pairing and performance

Gamma=50 and lambda2=30000 remain the source's illustrative selections at every
demand in both fleets. They are not recalibrated or claimed optimal for these
new cases. Do not select/refine them or a preferred demand on holdout outcomes.

Use all eight 40/60 s profiles, four design vectors and 16 independent validation
vectors, with one saved third-stream delivery draw. Each vector is reused across
requirements and controls. Pair changed laws through bounded-law CDF/inverse-CDF
quantiles; preserve unchanged renewable draws exactly. Design, validation and
delivery remain distinct streams. Original q=12 scalar outcomes are referenced
in place, rather than rerun or copied as raw simulations.

The maintained plant, causal 0.1 s clock, actuators, full-state passive L2 replay,
sampled bilinear contract and live frequency-error/cost ledgers stay unchanged.
No event preview, secondary restoration, resampling or energy constraint is added.

Report integrated squared frequency error, nadir/zenith, peak absolute error,
terminal error, RMS, transfers, activation cost and profit. RoCoF is only the
maximum absolute value of the unfiltered physical RHS over each full 40/60 s
trajectory, including exact one-sided event limits and no averaging window.
Report the mean of individual peaks and pointwise Student-t 95% intervals;
the peak of a mean trace is a different quantity. Generate no RoCoF time-series
figures; preserve historical figures and raw trajectories.

## Equal expected spending

For each fleet, reference demand q, profile and FI mechanism, use conditional
N=1 event per existing four-hour block:

    P_FCR(q_equal) = P_FCR(q) + mean_design(S_FI(q)).

This is accounting, not an occurrence forecast or repeated-event simulation.
Each FI mechanism has its own comparator. Freeze q_equal on four design vectors
before independent outcomes. Report supply limits and uniform-price gaps rather
than forcing equality; retain paired validation spending gaps and uncertainty.
Only demand changes for the FCR-only comparator within each fleet; disable the
zero-award FI controller without changing online inertia. Keep internal costs
outside TSO transfers. Equal spending does not establish social-cost equality,
provider profitability, voluntary participation or equilibrium.

## Execution and verification

```powershell
.\.venv\Scripts\python.exe scripts\run_fcr.py --fcr-capacity-study --config configs\fcr_capacity.toml --output-dir results\fcr_capacity\original
.\.venv\Scripts\python.exe scripts\run_fcr.py --fcr-capacity-study --config configs\fcr_capacity_expanded.toml --output-dir results\fcr_capacity\expanded
.\.venv\Scripts\python.exe scripts\run_fcr.py --fcr-capacity-report results\fcr_capacity\expanded
```

Save offers, awards/roles, prices, feasibility, draws/mapping, frozen selection,
outcomes, paired gaps and timings. Use ANALYSIS.md for reports. Check every planned
paired outcome exactly once. Resume cached episodes and regenerate reports with
no integration. Verify source preservation by SHA-256. Refine the minimum/maximum
feasible reference demands on the final two stress profiles under all three
controls, halving numerical steps and tightening tolerances tenfold while keeping
the decision clock. Finite studies do not certify Lyapunov stability or optimality.
