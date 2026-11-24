# Single-provider Lyapunov L2 experiment

The 19 November supervisor-requested interval sensitivity is documented in
`results/l2_interval_sensitivity/ANALYSIS.md`. It varies coupled decision and
settlement periods at fixed weight and preserves both settlement definitions.
It is a diagnostic study, not a replacement of the maintained 0.1 s clock.

## Current contract: passive-cost compensation (19 November 2026)

The completed matched economic audit is in
`results/l2_economic_audit/ANALYSIS.md`. Across 200 historical-input cases,
event-profit losses fall from 61 to zero. For the 16 independent 5 MW draws,
mean profit changes from -47.375924 to +17.131129 EUR/event. Small realized
interval losses remain under prediction error, with a worst loss of 0.025793
EUR; their numerical persistence and a specific unforeseen-disturbance cause
are checked separately. Physical behavior is unchanged. These finite-case
findings do not establish universal participation or profit guarantees.

The user adopted `passive_cost_compensated_v1`. For each actual decision
interval of length h, including a shortened final interval, define

    B_k = rho*z_k^2*T*(1-exp(-2*h/T))/4
    predicted_payment(v) = lambda2*(alpha^2-(alpha+beta*v)^2) + B_k
    S_k = lambda2*(e_passive_end^2-e_actual_end^2) + B_k
    profit_k = S_k - integral(rho*z_actual(t)^2/2 dt)

B_k is the exact cost of the zero-target decay from the inherited response.
It is independent of the current target, so the existing myopic optimum,
clipping and causal clock are unchanged. Target zero gives zero profit, to
integration tolerance, even from nonzero inherited response. The predicted
optimum has nonnegative profit; prediction error can still cause actual losses.
No participation choice, longer decision horizon or new stability claim is added.

The performance component can still be negative. Total payment is negative
when that charge exceeds B_k. Gross rewards/penalties split the signed TOTAL
interval payment; `performance_payment_eur` and `performance_penalty_eur`
separately retain the performance incentive. Interval exports also identify
`l2_contract`, `passive_activation_cost_eur`, `passive_cost_reimbursement_eur`
and `predicted_performance_payment_eur`. Actual activation cost remains a live
ledger and is not replaced by incremental cost in resource accounting.

New `--lyapunov-study` runs resolve and save this contract in their plan.
The direct simulation and target APIs use it by default. Explicit
`l2_contract="endpoint_only_v1"` retains the original contract for reproduction.
Unversioned saved study plans are interpreted as endpoint-only by candidate
replay and source-based comparison studies. New comparisons must use a new
compensated source study; earlier lambda selections and equal-budget matches
are not automatically valid for the new spending. Code fingerprints prevent
resuming old caches with changed implementation. Use a fresh output directory.
Historical outputs and report-only regeneration retain their original contract.

The sections below document the original endpoint-only experiment and its
historical results; its payment/profit statements are superseded only for new
compensated runs by the equations above.

Updated 13 November 2026. This implements the first simulation proposed in the
Week 9 ETH slides, following the Week 9 meeting. The TSO selects the incentive
coefficient explicitly by inspecting design Pareto curves, as requested by the
user on 13 November. It does not call the separate weighted TSO optimizer.

The first corrected design study is complete under `results/lyapunov_l2_week10/`:
224 paired candidate integrations and 16 successful refinement comparisons
(32 further integrations). The explicit TSO coefficient choice is pending.
The common model passed 130 tests; final L2 protocol/event-grid/parallel tests
also pass. Verification logs and the numerical-grid audit are saved with the
study. The four-draw design remains exploratory.

## Source and scope

The primary theoretical source is Jonas Matt, *Functional Incentives for
Control: Derivations for Benchmark and Lyapunov-type Incentives*, 17 July 2026,
`references/lyapunov_incentives (1).pdf`, Section 3, equations (18)-(22), printed
pages 7-9. The dynamic frequency adaptation is the proposal in
`results/week9/slides/sections/main_content.tex`, from the frame "First proposed
simulation: L2 without effort weighting" through the closing experiment slide.
The actuator, bounded target, causal local predictor and replay below are this
project's explicit adaptation, not a theorem from the scalar source model.

There is exactly one resolved FI provider. FCR roles/awards are frozen before
availability is drawn. Other providers, inertia, operating points, droop,
availability laws and costs retain `configs/fcr.toml`'s `fi_endogenous` scenario.
Both contracts use its original rho=1 EUR/(MW^2 s); D-040's separate FPP preset
is not imported. No state, sign, plant equation or original contract changes.

## Decision and payment

For interval length h, delivered response follows T dz/dt = -z+v with a held
target |v|<=A. Its exact first-order moments are shared with bilinear FI:

    a(t)=exp(-t/T), b(t)=1-a(t), z(t)=a(t)*z_k+b(t)*v
    I_a=integral a, I_b=integral b
    I_aa=integral a^2, I_ab=integral a*b, I_bb=integral b^2

The provider observes current frequency error e_k, current unfiltered model
RoCoF r_k, its own delivered response z_k and availability A. It knows its T,
rho, the announced coefficient and the frequency mass M_f. It freezes the
other power-balance contributions only in its local prediction:

    predicted_e(v) = alpha + beta*v
    alpha = e_k + h*r_k - I_b*z_k/M_f
    beta = I_b/M_f
    predicted_cost(v) = rho/2*(I_aa*z_k^2 + 2*I_ab*z_k*v + I_bb*v^2)
    predicted_payment(v) = lambda2*(alpha^2 - (alpha+beta*v)^2)
    v* = clip(-(2*lambda2*beta*alpha + rho*I_ab*z_k)
               /(2*lambda2*beta^2 + rho*I_bb), -A, A)

The positive quadratic coefficient gives a unique optimum for this approximate
one-interval problem. It does not optimize actual future settlement with perfect
foresight. No future event enters the decision. At an event exactly on the
decision clock, the current right-limit RoCoF is observed. Events between ticks
do not trigger decisions. The clock starts at zero, stays at 0.1 s and shortens
only its last interval, independently of solver/output grids.

After delivery, replay that interval from the complete actual initial state,
with the own target set to zero, the same realized disturbance and FCR responding
to the replay frequency. Inherited own response decays from z_k; no physical
response is reset. Discard the replay state after settling:

    S_k = lambda2*((e_passive_end)^2 - (e_actual_end)^2)

Lambda2 has units EUR/Hz^2 per decision interval. Positive S_k is a reward;
negative S_k is a charge. The cost ledger is the live integral rho*z^2/2 on the
actual delivered trajectory. Replay costs do not enter actual accounting.
Unlike bilinear FI, this transfer has no continuous payment rate: gross rewards
and charges split the signed interval transfers and post only at closure.
Target zero gives zero transfer while inherited response can still incur cost.
Thus the scalar source's cost-free zero-action outside option, nonnegative rent
and unconditional stability do not automatically hold in this adaptation.

## Meaningful synthetic profiles

All profiles use a 40 s horizon on the existing approximately 1000 MW teaching
system. Positive values denote absolute net deficit; negative values denote
surplus. Later values replace, rather than add to, previous levels. Values and
timings are declared stress scenarios, not calibrated event statistics.

| Profile | Absolute levels (time s, deficit MW) | Question |
|---|---|---|
| Sustained deficit | (1,10) | Containment, steady residual and persistent cost |
| Temporary deficit | (1.03,10), (6.07,0) | Recovery with inherited response after unannounced clearance |
| Deficit to surplus | (1,10), (20.05,-7) | Direction reversal after FCR response develops |
| Rapid reversals | (1.03,10), (3.07,-7), (4.23,5), (6.11,0) | Actuator memory, benefit signs and local forecast error |

Off-clock events separate plant evolution, causal decisions and settlement.
The first step is 1% of system load. The 17 MW reversal is an explicit stress
transition, not an isolated 7 MW event. The fast changes are comparable to the
conventional 2-4 s time constants and challenge their remaining response.
Recovery occupies over 30 s in the temporary/rapid cases. The historical 300 s
studies remain unchanged; this horizon neither models secondary control nor
supports long-horizon energy adequacy claims.

## Availability-variance sensitivity (15 November 2026)

The user requested a moderately less uncertain FI provider while preserving
nonparticipation in FCR. `configs/lyapunov_lower_uncertainty.toml` changes only
the uncertain renewable's underlying Gaussian variance from 4 to 1.75 MW^2.
The mean stays 2 MW and support stays [0,4] MW. The actual truncated-law
standard deviation decreases from 1.079120 to 0.987767 MW (8.47%), rather than
following the full reduction in the underlying Gaussian variance.

The parameter was selected from the FCR offer diagnostic before inspecting
new frequency outcomes. With the same 1 MW minimum bid, price ceiling 20,
cost a=5/beta=1 and shortage penalty 200, the best admissible positive offer
at variance 1.75 has reward -1.310074 EUR/block, so the optimizer selects zero.
At variance about 1.485591 MW^2 the 1 MW offer's reward crosses zero; variance
1.25 makes the provider offer 1 MW. This boundary is conditional on this preset,
not a general uncertainty threshold. No fixed zero offer or new penalty is used.

The reference remains under `results/lyapunov_l2_week10/`; new results belong
under `results/lyapunov_l2_lower_uncertainty/`. Every draw is paired by
`F_new_inverse(F_reference(A_reference))` within its original design/validation
bank. Other providers retain their exact saved draws. The market is recomputed
and all offers, awards and roles must match the reference. Costs, actuator and
frequency dynamics, disturbance profiles, coefficients, four design draws and
pointwise 95% Student-t convention remain unchanged. No independent policy
evaluation occurs before an explicit TSO coefficient selection.

Report regeneration adds `uncertainty_comparison.csv` and
`uncertainty_comparison.png`: measured halfwidth ratios and paired differences
for frequency error, nadir/zenith, terminal error, model-RHS peak absolute RoCoF,
payment, activation cost and profit. A smaller availability variance need not
shrink every outcome interval under nonlinear saturation. Changes in curve
means are part of the new scenario, not a cosmetic change to error bars.

## Experiment and TSO selection

`configs/lyapunov.toml` specifies four exploratory design availability draws,
16 independent validation draws and the initial computational grids. Every
candidate uses the same design draws for all four profiles. Equal profile
weights are an illustrative design mixture, not an observed event distribution.
The TSO knows this design set and availability laws, but not future delivery
draws. The provider never receives a profile label or future event sequence.

Three process workers evaluate independent design integrations with already
frozen draws. Completion order does not define sample identity or statistics.
`workers` is an explicit positive integer; checkpoints are published atomically.
Per-run CPU time is measured inside each worker. Parent process CPU alone is
not total study CPU, and concurrent execution affects wall-time measurements.

The TSO reviews cost/performance curves for each profile and the mixture, then
records a common tested lambda2 and a tested bilinear gamma with a reason:

```toml
[selection]
lambda2 = 30000 # Syntax example only; not an adopted choice.
gamma = 50     # Syntax example only; not an adopted choice.
reason = "Replace with the explicit TSO rationale after inspecting design curves"
```

There is no automatic knee, invented frequency weight or theoretical upper
coefficient bound. Different metrics can favor different candidates. Finite
grids and empirical frontier flags do not certify population dominance or a
resolved ordering near numerical plateaus. Four design draws are deliberately
small; pointwise Student-t intervals are exploratory. Mixture statistics first
aggregate within each draw to retain cross-profile covariance.

The L2 proposal observes current model-RHS RoCoF and knows the frequency mass;
the existing bilinear decision uses sampled frequency and its own actuator
model. The experiment compares these complete contract/controller packages.
It does not isolate the payment formula at an identical information set or
establish a general ranking of the incentive families.

Before independent validation outcomes are computed, the selection and hash of
the design data are frozen. Validation compares those policies with gamma zero
on matched draws; it must not select/refine coefficients. Changing selection
after validation is rejected. Repeating a study requires a new output directory.
Inputs/code hashes are checked before resuming saved candidate evaluations.

## Metrics and numerical checks

Always report sampled nadir/zenith, signed terminal deviation, live integrated
squared frequency error and peak absolute RoCoF. RoCoF is the unfiltered model
RHS in Hz/s over [0,40] s, including exact left/right limits of every event,
without a measurement or averaging window. Output-grid extrema receive a
denser-grid check; no security threshold is introduced.

The numerical output grid additionally includes every exact disturbance time.
This captures frequency extrema at off-grid reversal cusps without changing
integration boundaries, targets, provider information or the decision clock.
The initial regular-grid attempt is retained under
`results/lyapunov_l2_initial_grid/`: its rapid-reversal nadir at 3.07 s failed
the 1e-4 Hz sampling-refinement tolerance despite common-time trajectories
agreeing within 5.25e-12 Hz. The corrected study regenerates the metrics with
exact event instants; it does not relax the original tolerances.

Save signed net and gross transfers, actual activation cost, profit, predicted
versus realized payment, interval/cumulative errors, target/delivery saturation
and wall/CPU runtimes. Decision/replay time is recorded separately. Bilinear
replay time is zero because that contract does not replay a passive trajectory.

Numerical checks halve maximum solver step and output step and tighten both
solver tolerances tenfold. They span middle and upper coefficients for both
contracts, extreme design FI availability and sustained/rapid profiles. Limits:
1e-6 Hz common-time/terminal frequency, 1e-4 Hz sampled extrema, 1e-4 Hz/s peak
RoCoF, 1e-7 Hz^2 s integrated error and 1e-5 EUR total payment/cost differences.
Decision/replay clocks and availability stay fixed. Exact actuator cost is
checked independently of the live ledger. Prediction mismatch is reported;
it is not required to vanish with solver refinement.

The maintained test files cover numerical versus analytical target optimization,
zero capability/weight, causal off-clock events, a shortened final interval,
independent responsive-FCR replay, residual response decay, interval-ledger
closure, endpoint transfer timing and numerical refinement. The analytical
no-FCR reference remains a regression test for the common physical solver.

## Commands and outputs

```powershell
.\.venv\Scripts\python.exe scripts\run_fcr.py --lyapunov-study --config configs\lyapunov.toml --output-dir results\lyapunov_l2_week10
.\.venv\Scripts\python.exe scripts\run_fcr.py --lyapunov-report results\lyapunov_l2_week10
```

Without a selection, the first command completes design curves and verification
and records `awaiting_tso_selection`. Adding a selection and rerunning the same
command preserves design results and evaluates only frozen policies independently.
Report-only regeneration performs no integration. Outputs include `ANALYSIS.md`,
frozen resolved inputs and source hashes, market/operating point, exact draw banks,
candidate statistics, numerical checks, timings and PNG figures. Representative
trajectories are explicitly design sample 0 at each tested coefficient, not
selected policies or independent deliveries.

After selection and independent assessment, the matched-performance FCR-only
benchmark varies demand and recomputes offers/clearing on the same fleet and
availability laws. Metric tolerances must be declared before design selection
of procurement. Report infeasible/unmatched targets, independent paired gaps,
and FCR block payments separately from FI event transfers. Availability-variance
reduction remains a separate sensitivity. No event-count accounting assumption
is supplied implicitly.
