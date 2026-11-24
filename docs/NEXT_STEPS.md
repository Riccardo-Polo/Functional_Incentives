# Work plan after the Week 5 meeting

**24 November, supervisor feedback after Week 11:** prioritize a denser L2
coefficient sweep at the 288 EUR reference budget. Plot mean integrated squared
frequency error against lambda2. The user authorizes a fixed FCR price and
variable FCR awards to avoid endogenous clearing-price gaps. The study in
`results/l2_fixed_budget/` uses 24 EUR/MW/block and proportional scaling of the
12 MW reference awards, with one FI event per block and the same equal-weight
four-profile design mixture. Match design spending, then freeze procurement
for diagnostic validation and report its actual spending gaps. Keep 0.1 s
single-target control, endpoint-only settlement and the current zero-target
replay. See [the budget protocol](FI_BUDGET.md).

Deferred follow-ups: compare an ideal reference with immediately zero delivered
input, and extend Pareto studies to longer settlement periods with explicit
payment/activation-cost ratios. Neither change is part of the current budget
experiment. Slower disturbance ramps are acceptable but are not a priority;
retain the existing illustrative 0.5 s ramps for this comparison.

**21 November (D-049):** the separate-clock L2 experiment is complete. See
`results/l2_separate_clocks/ANALYSIS.md` and
[the causal formulation](L2_SEPARATE_CLOCKS.md). Fast 0.1 s control with
0.25/0.5 s settlement retains improved event profits without reimbursement
while reducing slow-control transients on the tested cases. Cost, boundary
effects, residual losses and fresh-policy assessment remain explicit. No
new maintained default or equal-budget comparison is adopted.

**19 November supervisor feedback:** the completed diagnostic interval sweep
is in `results/l2_interval_sensitivity/ANALYSIS.md`. Joint decision/settlement
periods 0.1/0.25/0.5 s improve original-contract event profit but increase some
transient losses and TSO payments at fixed lambda2. No new clock or contract
is adopted. Distinguish actual-power measurement from the zero-target versus
zero-delivered-power counterfactual. Separate settlement/control clocks,
coefficient scaling and equal-budget comparisons remain distinct next choices.

**19 November accepted adaptation:** implement passive-cost compensation for
L2, restoring zero profit for a zero target from any inherited response state.
Keep the same myopic control, physical dynamics, costs and participation rules.
New economic curves and budget matching require fresh compensated studies;
preserve existing runs. See [the contract definition](LYAPUNOV_STUDY.md).

**18 November meeting follow-up.** Use gradual net-deficit changes in new
comparison scenarios and inspect each provider's delivered power in MW.
For L2 show the free and bounded commands alongside the scalar reference
from Jonas Matt's Eq. (22). The fixed-budget comparison starts at 12 MW of
FCR and reduces procurement separately under each FI contract. Match expected
block-plus-event spending on design draws before evaluating delivery.
See [the study protocol](FI_BUDGET.md).

**16 November reference decision:** retain the traditional 4 MW reserve-cap
fleet for the study, with baseline demand 12 MW and the accepted FI availability
variance 1.75 MW^2. The expanded conventional-reserve scenario stays separate.
Week 10 LaTeX slides are under `results/week10/slides/`, using the Week 5 ETH
theme. Main results use the traditional fleet; the expanded study appears in
the appendix. Coefficients and the physical/economic model remain unchanged.

**16 November follow-up:** test initial FCR demands 12/12.5/13/13.5/14/16/18/20
MW with frozen gamma=50/lambda2=30000. The user authorized a separate scenario
increasing conventional reserve capacity/underlying mean from 4/3.98 to 8/7.98
MW, while preserving original inputs and recording original supply limits.
Use `--fcr-capacity-study`; see [FCR_CAPACITY_STUDY.md](FCR_CAPACITY_STUDY.md).
Report only trajectory-peak absolute RoCoF, without time-series RoCoF figures.

**15 November follow-up:** the user requested more metric-specific Pareto
curves, delegated the explicit TSO coefficient choice and requested significant
disturbance/equal-spending comparisons. The frozen illustrative choices are
lambda2=30000 and gamma=50 from the reduced-uncertainty design curves. Use
`--lyapunov-compare` with `configs/lyapunov_comparison.toml`; new outputs stay
in `results/lyapunov_comparison/`, referencing earlier studies unchanged.
Independent assessment uses 16 saved draws; additional pulses/doublets/steps
use frozen policies. The four-hour FCR block is the existing accounting
convention. N=1/4/12 event counts remain explicit conditional sensitivities,
not an adopted forecast; report supply limits and independent spending gaps.
See [LYAPUNOV_COMPARISON.md](LYAPUNOV_COMPARISON.md).

## Week 10 experiment following the Week 9 meeting (12 November 2026)

**13 November clarification:** the TSO chooses L2 lambda2 explicitly by inspecting
the design Pareto curves, analogously to the curve-based bilinear FI experiment.
Do not use the separate weighted optimizer to select it. Build and verify the
single-provider L2 case and the synthetic disturbance design curves first;
freeze an explicit curve-based choice before independent evaluation. See
[LYAPUNOV_STUDY.md](LYAPUNOV_STUDY.md) and `configs/lyapunov.toml`.

**15 November sensitivity:** the user requested less availability uncertainty
while retaining the uncertain provider's voluntary zero FCR offer. The separate
`configs/lyapunov_lower_uncertainty.toml` uses underlying variance 1.75 MW^2
instead of 4, with mean, costs and minimum bid unchanged. Recompute clearing,
pair the saved banks by common bounded-law quantiles and compare actual 95%
confidence halfwidths at the same n=4. Keep the original study by reference;
new outputs go to `results/lyapunov_l2_lower_uncertainty/`. No TSO coefficient
or independent policy evaluation is selected by this sensitivity.

The Week 9 proposal was received positively. The next experiment follows the
closing slide in `results/week9/slides/sections/main_content.tex` and adds a
matched-performance FCR-only comparison.

1. Implement the proposed L2 contract for one FI provider. Choose the bounded
   held target with the causal local frequency predictor. Settle each interval
   by replaying a zero target from the full actual initial state with the
   realized disturbance and responsive FCR. Compare predicted and realized
   payments, verify target limits and check convergence under solver refinement.
2. Sweep the L2 coefficient and compare with bilinear FI on paired availability
   draws for the usual disturbances, including sustained deficit and reversal.
   Freeze the policies before independent evaluation. Report signed net and
   gross transfers, activation cost, profit, saturation and decision/replay time.
   Measure nadir, terminal deviation, integrated squared frequency deviation and
   peak absolute RoCoF from the unfiltered model RHS over the event horizon,
   including disturbance right limits, with no averaging window.
3. Take the measured FCR+FI performance as the target. With the same fleet,
   disturbances and availability laws, disable FI and vary the FCR requirement.
   Recompute offers and clear each feasible case. Record who bids, who receives
   an award, procured MW, clearing price and whole-block payment. Define metric
   tolerances before selecting on the design bank. Report paired performance
   gaps and uncertainty on independent evaluation, including cases where no
   tested feasible procurement matches the target.
4. As a separate sensitivity, test a modest reduction in provider availability
   variance if participation limits the FCR-only comparison. Report the changed
   inputs, actual bids and awards. Pair draws across laws using common underlying
   quantiles and also evaluate FCR+FI under the changed law to isolate its effect.
5. Compare the incremental FCR-only block payment with the FI event transfer.
   For any common-horizon total, state the assumed number and mix of events per
   block. Keep provider activation costs separate from TSO payments. Record
   presentation, slide and report deadlines once confirmed.

## Earlier direction of 6 November: Lyapunov incentives

**6 November 2026 (D-041).** The user selected exploration of Lyapunov-based
functional incentives as the single next research direction. Further AEMO/FPP
best-response and Nash-equilibrium development is not a parallel workstream.
The dated plans below are historical where they conflict with this choice;
existing FPP implementations, configurations and studies remain preserved.

Start from Jonas Matt's *Functional Incentives for Control: Derivations for
Benchmark and Lyapunov-type Incentives*, dated 17 July 2026, supplied by the
user as `lyapunov_incentives (1).pdf`. The supplied local source is
`C:/Users/ricca/Downloads/lyapunov_incentives (1).pdf`; it is not a portable
repository dependency. These working notes define drift-referenced payments,
myopic follower best responses and scalar results for L1/L2 variants, with
and without effort weighting. They are a theoretical starting point, not an
already implemented or validated frequency-control contract.

The initial exploration should:

1. Understand and check the payment definitions, information assumptions,
   best responses and scope of the stability and economic results.
2. Identify the mapping to the existing frequency model: full state, held
   target versus delivered response, decision interval, passive counterfactual,
   activation cost, availability bounds and payment units. Keep unresolved
   choices explicit; scalar unconstrained results do not automatically extend
   to the coupled system with first-order provider dynamics.
3. Define a focused research question about whether the theoretical benefits
   survive actuator dynamics and saturation, using the existing bilinear FI
   mechanism as a benchmark. Selection of a particular Lyapunov variant and
   its implementation remain subsequent modelling decisions.

This decision changes research priority only. It does not change the physical
model, FCR/FI/FPP rules, economic inputs or accepted TSO objective, and does
not launch simulations. Future frequency assessments must include RoCoF with
an explicit calculation method, measurement window and summary statistic.

## Historical progress and plans

**6 November 2026 (D-040).** The maintained follower economics now use an
explicit hourly quadratic cost of 50 EUR/(MW² h), converted once to per-second
rho=1/72, and symmetric FPP prices of 12 EUR/(MW h). The latter is an
illustrative factor-two activated-service premium over the preset FCR hourly
scale 24/4=6, not a market-product identity. The catalogue extends to 4.
This supersedes the unsupported monetary scale of D-039; its equations,
causal control, physical inputs and historical results are preserved. The
same resource cost applies to FI and FPP within the follower preset. Gamma
is not recalibrated by this change. See results/fpp_economic_scale/ for
matched repricing, holdout, sensitivity and numerical checks.

**5 November 2026 (D-039).** The maintained FPP gain game now selects theta
using priced settlement minus the same quadratic delivered-power cost as FI.
Explicit illustrative raise/lower prices are 120 EUR/(MW h) each. The ideal
settlement uses raw model frequency over the full numerical trajectory with
time-weighted quadrature, without the four-second measurement cadence. The
0.1 s causal target clock remains. See [AEMO_FPP.md](AEMO_FPP.md). Earlier
equilibria below describe the historical coefficient-only, four-second game.

**31 October 2026.** The first gain game and paired comparison are complete.
The only empirical pure equilibrium is (2,2) on the configured five-gain
catalogue. Relative to continuous droop the selected gains reduce integrated
squared error by 7.58% in a sustained deficit and 6.76% after reversal. Peak
absolute RoCoF is 0.063830 and 0.108511 Hz/s respectively for all three laws.
It is the unfiltered physical RHS over 300 s including exact event limits,
with no averaging window. Results use one independent availability draw.
See `results/fpp_controller_comparison/ANALYSIS.md` for the paired cases and
`results/fpp_gain_game/ANALYSIS.md` for design and holdout. Interpretation
outside the tested gain grid would require a separate design and holdout.

**Earlier direction, 24 October 2026 (D-029):** understand the existing setup
through controlled gamma sweeps and metric-specific cost-performance curves.
Include RoCoF in all future frequency-performance assessments, alongside nadir,
terminal error and integrated error. `--tso-pareto` augments the preserved
pedagogical sweep; `--tso-pareto-report` regenerates reports without integrations.
See [PARETO_STUDY.md](PARETO_STUDY.md). Keep the current objective, all physical
and market inputs, and independent matched availability banks unchanged.
First interpret one fixed scenario, including saturation and terminal-window
diagnostics; applying the same protocol to A/B/C is a subsequent step.

**Earlier direction, 18 October 2026 (D-028):** preserve all completed studies
and compare reduced FCR procurement on the same A/B/C fleets. Only demand
changes: 12 MW reference, then 10 and 8 MW; gamma is reoptimized with the same
32+32 banks and w_f=10000. See [FCR_REQUIREMENT_STUDY.md](FCR_REQUIREMENT_STUDY.md).
Report frequency degradation alongside FCR block savings and extra FI event
spending; event-count sensitivity must remain explicitly conditional.
Completed on 18 October: six new optimizations, 96 full-FCR validation runs,
five figures, preserved references and successful numerical/accounting checks.
10 MW gives conditional savings versus 12 MW+FI with degraded frequency;
8 MW produces substantially worse expected error than the FCR-only reference.
A finer sweep near the 9.592227 MW price boundary is an optional separate step,
not an already selected policy. See the study document for uncertainty limits.

**Earlier direction, 16 October 2026 (D-026):** sampling stability is completed;
the user now prioritizes multiple mixed fleets with source-inspired machine
parameters. The separate [fleet study](FLEET_STUDY.md) compares three physical
configurations with common economics, w_f=10000, and 32+32 availability draws.
Completed and verified on 17 October: five plots, individual accounting,
six numerical checks and seven targeted tests; results in `results/fleet_scenarios/`.
This takes precedence over the earlier ordering below. Source-inspired inputs
are not yet a calibration of private costs, availability or market prices.

Updated 2026-10-15. The user has chosen **the weighted TSO objective with w_f
supplied by the user** (D-022); see [TSO_DESIGN.md](TSO_DESIGN.md). The numbered
sequence below is the earlier recommendation, not a prerequisite for that
discussion. The requested zero-award FI fallback is now implemented (D-018):
eligible rejected FCR bidders can enter FI. The first availability-only,
common-gamma trade-off sweep is implemented via `--tso-sweep`. The weighted
optimizer is now implemented via `--tso-optimize` (D-023), with focused tests,
independent validation and a single subsequent delivery event. The user confirmed
the current five-provider FI parameters and authorized a demonstration w_f of
10000; it remains an explicit runtime input, not a calibrated default.
Parameter calibration and repeated cycles remain separate work. The preliminary
curve does not justify a general gamma range or a restriction to 25–75.
The latest follow-up selects **three user weights before availability scenarios**:
5000, 10000 and 20000, with identical machine/market parameters and draws.
Week-5-style reporting is available via `--tso-report`; the controlled study
is under `results/tso_weights/`, referencing the existing central run.
Do not mistake the single-delivery figures for expectations over availability.

After this comparison, proceed in separate increments:

1. Check optimizer sampling stability using larger design/validation banks and
   a small number of independent seeds, holding the system and weight fixed.
   Compare objective loss as well as gamma: a flat minimum can make the latter
   variable even when performance is stable. Predeclare any held-out evaluation.
2. Locate the FI saturation boundary and S versus 2C behavior on a fixed
   configuration/draw, then vary availability in a separate controlled study.
   Changing uncertainty can change FCR offers/roles under endogenous participation;
   decide whether to study that market effect or hold FI assignment fixed.
3. Introduce sourced physical parameters, then economic parameters, as separate
   changes. A better Monte Carlo estimate does not make pedagogical inputs real.
   Repeated contracts still require explicit state/forecast transition rules.

The user authorized increment 1 at w_f=10000; it is completed and assessed on
16 October 2026. The implemented
`--tso-stability` protocol uses N=8/32/128 and three independent design seeds,
followed by a frozen-policy comparison on 256 common independent draws against
the previous gamma=52.34375. See D-025 and the configuration/design notes.
Results are under `results/tso_stability/`. At N=128 the three selected gammas
are 46.875/46.875/45.3125, versus 52.34375/49.21875/42.1875 at N=8. Held-out
mean J for 46.875 improves by 0.7167 EUR/event (0.2066%) against the previous
52.34375; the two distinct N=128 candidates differ by only 0.1060 EUR/event.
This supports examining practical objective changes rather than pursuing many
gamma decimals. It does not establish equivalence, convergence or a universal
policy. Saturation and calibration remain separate subsequent increments.

The supervisor's positive feedback and agreement with the Week 5 next steps
were reported by the user after the 13 October meeting.

The accepted research directions are:

- test more seeds and disturbance scenarios;
- define a TSO objective trading frequency performance against FI expenditure,
  then optimize gamma;
- introduce repeated market cycles with updated availability forecasts,
  renewed FCR clearing, FI contracts and participation;
- investigate the observed FI net-payment/activation-cost ratio and locate
  the onset of saturation for a specified configuration;
- replace pedagogical physical and market inputs with traceable,
  source-informed values where evidence permits.

The weighted TSO objective is now decided. The reference market and inter-cycle
transition rules remain open; the numbered stages below retain the earlier plan.
[MODEL.md](MODEL.md) describes the implemented model;
[CONFIGURATION.md](CONFIGURATION.md) describes the existing controls;
[PARAMETER_SOURCES.md](PARAMETER_SOURCES.md) records the calibration work.

## 1. Evidence to preserve

The local Week 5 deck is
`results/week5_slides/week5_eth/week5_eth.pdf`, dated 13 October 2026.
Slide labels 18/22, 21/22 and 22/22 are PDF pages 19, 22 and 23 respectively
(the title page and appendices explain the different numbering).
The resolved configurations, CSVs and original figure-generation script are
preserved in `results/archive/week5_reproduction.zip` (with the original
`data/` paths inside the archive). These local artifacts are ignored by Git.

The saved FI outcomes give the following **single-realization** observations
for `renewable_uncertain`, seed 20261008, horizon 0–40 s, rho = 1,
response time 0.25 s, decision interval 0.1 s and availability
2.9291100053 MW. Here S denotes actual net settlement and C activation cost.

| Case | S [EUR] | C [EUR] | S/C | S - 2C [EUR] | Maximum held target [MW] |
|---|---:|---:|---:|---:|---:|
| `fi_endogenous`, gamma 50 | 61.4103435462 | 30.7031585228 | 2.0001311429 | 0.0040265006 | 2.6145815622 |
| `fi_strong`, gamma 200 | 414.5456002633 | 164.2859767052 | 2.5233170145 | 85.9736468528 | 2.9291100053 |

The first ratio is approximately two, with relative residual
`(S - 2C)/(2C)` about 0.006557%. The second case reaches the target bound.
This supports investigating saturation, but does not establish that every
unsaturated dynamic trajectory has exactly S = 2C or that saturation explains
every deviation. The supervisor's theoretical result is reported context;
its precise assumptions and proof have not been supplied to this project.

The instantaneous, interior quadratic best response does give payment = twice
cost. The implemented controller instead optimizes a held target through a
first-order response with an inherited state. See the research note in
[MODEL.md](MODEL.md). Distinguish net settlement S from profit S - C,
gross rewards, and the instantaneous-target payment benchmark.

## 2. Recommended order: one reviewable change at a time

### Step 1 — Add diagnostics and establish the theoretical comparison

Preserve the five Week 5 cases and their inputs. Reproduce the two FI ledger
totals above before extending reporting. Use the existing modules and runner.

Add per-provider, per-decision diagnostics for the unconstrained target,
clipped target, availability, signed/absolute distance to the bound, initial
and final response, interval duration, S, C and S - 2C. Existing decision
records already contain most of these quantities. Report cumulative residuals
as well: cancellations must not hide interval-level differences.

Use S/C only when C exceeds a declared small monetary threshold; otherwise
report it as undefined, including zero action/gamma. Predeclare absolute and
relative tolerances instead of choosing them to make the ratio pass.
Distinguish three quantities:

1. target bound active or unconstrained target clipped;
2. delivered response close to availability, using a separate MW tolerance;
3. payment/cost residual outside a stated tolerance.

First-order delivery need not reach its bound at the instant the target clips.
Separate inherited-response/transient effects, decision sampling, terminal
horizon effects and numerical error. Derive/check the interval identity from
the existing objective before claiming an exact dynamic 2:1 law. Do not modify
the payment rule or optimizer to enforce that ratio.

**Completion check:** the extra reporting leaves trajectories and settlements
unchanged; tests cover an interior case, a clipped target, nonzero initial
response and zero cost. Solver refinement holds the decision clock and draws
fixed. The residual has a theoretical interpretation, not only a plot.

### Step 2 — Find the saturation boundary in one fixed configuration

Start with the Week 5 `fi_exogenous` configuration so membership is fixed.
Its gamma-50 physical/FI results match `fi_endogenous`. Keep the operating
point, FCR offers/awards, all availabilities, rho, response time, disturbance,
horizon and decision interval fixed. Vary only this FI provider's gamma.

Use a coarse scan including 0, 50 and 200, then refine the first bracket in
which the target constraint becomes active. Re-run the coupled frequency and
provider dynamics for each gamma: frequency itself depends on the incentive.
For positive availability, the mathematical contact condition is
`max_k(abs(v_free[k; gamma]) - A) = 0`. Declare the numerical MW tolerance and
gamma bracket width, and distinguish contact from strict clipping. Handle
A = 0 separately as unavailable capacity, not a meaningful interior regime.

Do not assume global monotonicity of the active set or frequency improvement.
Refine a locally identified crossing and report any additional crossings;
use bisection only on a bracket for which the local behavior is justified.
The instantaneous rule `gamma*abs(e)/rho = A` is a reference only: it is not
the threshold formula for the current lag-aware closed loop.

**Completion check:** save a nonsaturated/saturated bracket, first contact
time, provider ID, fraction of decision time at the bound, free/actual target
and delivery, S/C and S - 2C, nadir and terminal frequency. Verify cases on
both sides with refined integration using the same draws and decision clock.
The boundary is conditional on the full configuration and realization, not a
universal gamma. A detectable ratio change need not coincide exactly with it.

### Step 3 — Test robustness without changing the mechanism

Extend the same experiment harness in small groups: gamma versus disturbance
magnitude; then rho and response time; then availability realizations and
multiple seeds. Compare several decision intervals as a **controller change**,
separately from solver convergence at a fixed decision interval.

Hold other inputs fixed within each comparison. Reuse identical availability
draws for paired gamma runs and numerical refinement. A changed availability
law produces different draws even with the same seed; document any common
random-number coupling. For a deterministic-availability sensitivity, declare
the changed law explicitly. Keep the initial FI group fixed; test endogenous
membership separately and record every change in offer/award/participation.

Report per-provider and aggregate residuals, frequency metrics, FI expenditure,
bound activation frequency, threshold ranges, sample count, seed list and
quantiles. Include failed/infeasible cases in the report, never silently drop
them or invent partial FCR procurement. Use separate evaluation seeds if
these runs later support TSO parameter selection.

**Completion check:** a reproducible scenario matrix identifies where the
approximate relation holds, where it fails and whether saturation, transient
dynamics or numerical accuracy explains the observed differences.

### Step 4 — Introduce evidence-based inputs in two separate changes

Source collection can begin during Steps 1–3, but change numerical inputs
only after the baseline diagnostics work. First complete the provenance
register in [PARAMETER_SOURCES.md](PARAMETER_SOURCES.md), including missing
evidence. Choose the target system/market and historical reference period
explicitly; a Nordic-inspired physical example does not automatically choose
a Nordic FCR product or replace the PJM network.

**4a. Physical configuration:** ratings, technology, dispatch and active-power
limits/headroom, synchronous inertia, droop, response times, damping and
disturbance scale. Keep the pedagogical case reproducible. Solve ANDES again,
check both active-power margins and verify the frequency/droop power bases.
Do not infer inertia from aggregated wind capacity or P_max from MVA alone.

**4b. Economic and uncertainty configuration:** choose a documented FCR
product/date, then source bid sizes, block duration, procurement scale, prices,
shortfall rules and availability assumptions. Identify what is measured,
converted, estimated or still pedagogical. Private costs a, beta and rho
are not identified by a clearing price; FI gamma remains a design variable.
Keep missing parameters as declared sensitivity ranges rather than fabricate
calibration. Re-run the threshold and robustness checks after each change.

**Completion check:** every adopted value has a source locator, original
units/time base, conversion, rationale and applicability limit. Changing a
market rule or the 60 Hz model requires an explicit model decision, separate
from entering new numbers. No full Nordic32 network migration is needed for
the first source-informed parameter study.

### Step 5 — Define the TSO objective, then optimize gamma

Implemented D-022/D-023: minimize w_f times expected integrated squared
frequency error plus expected actual net FI payment. Weight is explicit;
gamma is common and fixed before delivery. A configurable bounded grid plus
local refinement reuses design draws, includes saturation and 0/50/200 when
within range, then evaluates the selected candidate on independent validation.
Provider interval optimization is unchanged. No risk/budget constraint is added.
Keep FI event costs and FCR whole-block costs separate unless an event-frequency
model and common financial horizon have been explicitly defined.

**Completion check:** the objective and information timing are documented;
the selected gamma is compared with the reference grid and validated on held-out
cases. This step does not introduce adaptive within-event gamma.

### Step 6 — Add repeated market cycles after the single-cycle checks

Specify what changes and what carries over before coding: block length,
forecast updates, sampling/dependence between blocks, event occurrence, dispatch,
physical states, decision-clock alignment, gamma update rule and cash ledgers.
Decide explicitly whether episodes are independent resets or a continuous
trajectory. Current primary control does not restore nominal frequency, so
resetting a physical trajectory is not automatic.

Start with two cycles: forecast information -> offers -> clearing -> realization
-> FI decisions/delivery -> settlement, with new contracts and membership only
at the declared boundary. Preserve the distinction between FCR opt-out and a
rejected positive bid. Both now allow FI for `fcr_or_fi` providers after a zero
award (D-018). A joint expected-profit comparison across products remains a
separate participation-policy change.

**Completion check:** one-cycle mode reproduces the baseline, the second cycle
uses no future information, and carried/reset states and money reconcile.
Do not add an intracycle stochastic availability process implicitly.

## 3. Historical next increment: FPP provider optimization

Superseded as the active research direction by D-041 above. This section
preserves the earlier design brief, not the current implementation status.

The fixed FPP benchmark and metering revision are complete. The next increment
is to specify an FPP provider problem, then implement and test its solution.
The existing fixed-droop follower is the reference policy, not the result of
an optimization. Its settlement is directional and based on system-wide
frequency and metered deviations; its private activation cost is separate.
The baseline has no external raise/lower prices, so monetary net income cannot
yet be computed from its coefficients.

1. Specify the transfer budget and participants, including the accounting
   residual, all-helpful/all-zero outcomes and charge concentration. Identify
   which measurement heuristics could be simplified without losing the chosen
   redistribution property. Keep the reference fixed at solved P_i,0.
2. State the provider's controllable action and feasible set, including power,
   ramping or energy limits only if explicitly adopted.
3. Specify the information set and timing: what the provider observes before
   acting, what it can forecast, and whether the policy is chosen before the
   event or updated during it. Future availability and frequency draws must not
   enter a causal decision as known values.
   Treat others' behavior through explicit beliefs, forecasts or strategies;
   assess whether a tractable Nash equilibrium exists, without assuming one.
4. Define the objective and economic inputs. If optimizing money, identify
   directional FPP prices, currency and conversion basis, the accounting horizon,
   activation costs and any other payments to include. Keep the AEMO-inspired
   settlement formula and the provider's own objective conceptually distinct.
5. Compare the optimized policy with fixed droop on matched assessment draws
   and independent validation/delivery draws. Report profit components and
   frequency nadir, terminal error, integrated error and RoCoF. State the RoCoF
   calculation method, measurement window and summary statistic. Check solver
   refinement, physical limits, settlement balance and sensitivity to prices.

Keep the first implementation inside the existing modules and runner, with a
separate opt-in mode and saved resolved inputs. No FPP optimum, price scenario
or controller law has been selected by the current benchmark. Earlier FI
saturation, calibration and repeated-cycle work remain separate increments.

## 4. Open decisions to resolve at the relevant step

- Step 1: exact scope of the supervisor's theoretical identity; acceptable
  residual tolerances and distinction between instantaneous and dynamic models.
- Step 4: reference geography/product/date; system scaling and whether to
  retain 60 Hz or introduce a separate, explicit frequency-base change;
  sources or declared ranges for dynamics, private costs and forecast errors.
- Step 5 follow-up: justified application-specific valuation, horizon/search
  sensitivity and statistical precision. The weighted objective is implemented;
  additional risk or operational constraints would be separate decisions.
- Step 6: inter-cycle forecasting, events, state continuity/reset, gamma and
  participation updates. No new rule is assumed in advance.
- FPP provider optimization: controllable action, information timing, objective,
  directional price evidence and currency, private costs, transfer-budget closure,
  all-helpful cases, charge concentration, measurement simplifications, possible
  Nash equilibrium and out-of-sample comparison protocol. The constant P_i,0
  reference is decided and is not an open dispatch-optimization variable.
