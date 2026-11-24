# Frozen-policy comparison and equal-budget procurement

Protocol dated 15 November 2026. The user requested additional metric-specific
Pareto curves, a curve-based TSO selection for L2 and bilinear FI, significant
new disturbance simulations and FCR-only comparators at the same spending.
The preserved source design is `results/lyapunov_l2_lower_uncertainty/`.

Completed study: `results/lyapunov_comparison/`. It contains 687 new episode
integrations, 48 scalar design outcomes referenced from the source, and eight
passed refinement comparisons (16 further integrations). Seven additional
Pareto views and the 16-draw independent assessment are saved. See ANALYSIS.md
for quantitative results and budget residuals. L2 has negative mean private
profit on the unseen moderate sustained case; this finding is retained and
does not trigger holdout-based coefficient changes.

## Explicit TSO choice

The illustrative choices are lambda2=30000 EUR/Hz^2 per decision interval and
gamma=50 EUR/(Hz MW s), using only the four-draw, equal-weight original design
mixture. Gamma 50 obtains about 93% of the best tested bilinear error reduction
before the expensive plateau. L2 30000 is near its error plateau and has
positive empirical mean provider profit, whereas L2 10000 has negative mean
profit. These are transparent analyst choices from the curves, not certified
optima, calibrated defaults, a new provider participation constraint or a new
weighted TSO objective. The original weighted optimizer remains separate.

Freeze the choice and hash of the source design before evaluating independent
outcomes. No holdout or new disturbance outcome selects/refines either gain.
Positive design mean profit does not guarantee profit on every draw or unseen
profile under the myopic controller. The L2 proposal observes current model-RHS
RoCoF and knows the frequency mass; the bilinear implementation has its original
information set. This compares complete contract/controller packages.

## Additional Pareto views

Report signed event transfers against integrated squared frequency error,
nadir, zenith, peak absolute error, terminal absolute error, RMS error and peak
absolute RoCoF, per design profile and for the original equal-weight mixture.
Peak absolute error is formed per trajectory before averaging. RMS is derived
from sqrt(E_f/horizon), not independent evidence beyond the saved integral.
Raw empirical frontier flags are metric-specific and do not resolve numerical
plateaus or prove population dominance. Show all tested candidates and mark
the explicit selected values. Keep four design draws and pointwise 95%
Student-t intervals, with covariance retained by aggregating profiles within
each draw before mixture statistics.

RoCoF always uses the unfiltered physical RHS in Hz/s, including exact left
and right event limits and no measurement/averaging window. The summary is
maximum absolute RoCoF over each complete 40/60 s simulation. The other
extrema use the output grid supplemented with exact event instants.

## Independent assessment and significant disturbances

The source's 16 independent validation draws assess the original four profiles
and four new scenarios. A third stream with seed 20261115 supplies one common
independent delivery draw, generated after policies and procurement demands
have frozen. Delivery figures are illustrations, separate from validation
statistics. All controls share the same physical fleet, awards unless explicitly
changed for FCR procurement, and matched availability draws.

| New profile | Absolute deficit levels (time s, MW) | Horizon | Purpose |
|---|---|---:|---|
| Moderate sustained | (1.03,5) | 60 s | Unseen amplitude, residual error and persistent activation |
| Short pulse | (1.03,10), (3.07,0) | 40 s | Clearance before conventional delivery fully develops |
| Bipolar doublet | (1.03,10), (3.07,-10), (5.11,0) | 40 s | Equal-duration opposite disturbances, inherited response |
| Staircase and recovery | (1.03,4), (8.07,8), (15.11,12), (22.13,6), (30.17,0) | 60 s | Escalation, saturation, release and recovery |

These are declared synthetic stress cases, not calibrated event records.
The bipolar disturbance has zero net disturbance energy over its two pulses,
but that does not imply zero frequency impact, payment or activation cost.
Physical states, availability and causal 0.1 s decisions carry through every
change. No event preview, secondary restoration or energy constraint is added.

## What equal spending means

The existing configuration defines a four-hour FCR block. Prices already have
whole-block units; the 12 MW baseline payment is 288 EUR/block. Four hours is a
saved pedagogical accounting convention, not a newly asserted market rule.
The FI ledgers cover the actual simulated event, not four hours of service.

For an explicit conditional accounting count N of events of one profile per
block, the design budget target is

    P_FCR(q_equal) = P_FCR(12 MW) + N * E_design[S_FI].

The manifest shows N=1,4,12 as separate sensitivities. No value is adopted as an
event-rate forecast. The one-event plots are conditional examples. Different
profiles are alternative scenarios, not all charged simultaneously in a block.
N changes accounting only: no repeated market cycles, inter-event resets or
secondary restoration are simulated. Do not prorate FCR payment to 40/60 s or
extrapolate FI transfers without the explicit event-count assumption.

Recompute provider offers with unchanged availability laws, costs, penalties,
price bounds, minimum positive bid and ordinary uniform-price clearing. Change
only the procurement requirement. Awards remain divisible and gains are not
retuned. Disable FI control for FCR-only comparisons; a zero-award resource
has zero response with its operating point and online inertia unchanged.

The current offers total about 13.543 MW and allow at most about 325.03
EUR/block, just 37.03 EUR above the reference. Report supply-limited targets
and unattainable uniform-price jumps rather than inventing supply, prices or
subsidies. For this source the upper procurement tier has price 24 EUR/MW/block.

Procurement matches spending exactly in design expectation. Freeze q_equal
before independent outcomes. On validation/delivery the FI transfer changes
with the draw, so report the realized budget difference and its uncertainty:

    gap = P_FCR(q_equal) - [P_FCR(12 MW) + N*S_FI].

Do not retune q_equal on validation or delivery to manufacture exact realized
equality. Such a construction would use future FI outcomes and would not be
the causal procurement experiment. Compare paired performance at each frozen
design budget and retain residual spending gaps transparently.

Keep provider activation costs, FCR commitment/shortage costs and TSO payments
distinct. FCR activation cost is absent by the maintained market convention;
zero FI cost on an FCR-only row is not a claim of zero total resource cost.

## Implementation, checks and reproduction

Use the existing plant, incentive and market functions through the flat
`lyapunov_comparison.py` orchestration module and the maintained runner:

```powershell
.\.venv\Scripts\python.exe scripts\run_fcr.py --lyapunov-compare --config configs\lyapunov_comparison.toml --output-dir results\lyapunov_comparison
.\.venv\Scripts\python.exe scripts\run_fcr.py --lyapunov-comparison-report results\lyapunov_comparison
```

Saved source studies are referenced in place and checked by SHA-256. Save
selected policies before independent evaluation and budget matches before
drawing the delivery stream. Independent process jobs use explicit fixed
draws and sample identities. Require every planned bank/profile/policy/sample
exactly once; never average a selectively surviving subset.

Numerical checks halve solver/output steps and tighten tolerances tenfold for
the doublet/staircase under all three base policies and L2-budget FCR pulse/
doublet cases. Limits: 1e-4 Hz sampled extrema, 1e-6 Hz terminal/common-time
frequency, 1e-5 MW common-time response, 1e-4 Hz/s peak RoCoF, 1e-7 Hz^2 s
integrated error and 1e-5 EUR transfer/cost. The decision clock stays fixed.

Outputs include expanded Pareto diagnostics, frozen selection, independent
performance and paired differences, budget feasibility/matches, paired
equal-budget outcomes and spending residuals, complete representative delivery
trajectories, numerical checks, timings and `ANALYSIS.md`. Report-only mode
does no integration. Tests cover supply ceilings, price gaps, exact design
matching, independent pairing, reference preservation and resume/report without
new simulation. No asymptotic Lyapunov stability or population-optimal policy
is certified by these finite studies.
