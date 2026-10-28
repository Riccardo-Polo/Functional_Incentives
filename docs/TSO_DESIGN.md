# TSO objective and optimization of gamma

Updated 2026-10-15. **Weighted common-gamma optimizer implemented (D-023).**
Use `--tso-optimize` with an explicit frequency weight to choose gamma, assess
it on independent availability draws and simulate one later delivery event.
The ordinary simulator still takes fixed gamma values; the separate
`--tso-sweep` evaluates a common grid without selecting a policy.
The zero-award FI fallback is implemented separately in D-018. The user has
accepted a common scalar gamma for the first TSO formulation (D-019).
D-020 also accepts the current 10 MW step,
availability as the only uncertainty, and TSO knowledge of provider distributions,
costs and dynamics. On 15 October the user selected the weighted criterion
`w_f * E[E_f] + E[S_FI]`, with w_f supplied by the user as an input to the TSO
(D-022). The user subsequently authorized choosing a demonstration weight and
confirmed the current five-provider FI configuration for one experiment:
w_f = 10000 EUR/(Hz² s), with all machine/market parameters unchanged.
This explicit example is not a default or calibrated valuation. The budget and
frequency-requirement formulations are alternatives, not the current objective.

The previous curve is a conditional implementation check with pedagogical
parameters. It does not establish a transferable gamma value or recommended
policy range. The optimizer must accept the configured system and user weight;
calibration and cross-scenario research claims remain separate work.

The initial optimizer uses [0, 300], seven uniform grid points and reference
points 50/200 when inside the domain. Midpoints refine intervals adjacent to
every observed local minimum, including endpoints, and the best candidate.
It stops at local spacing <= 1 or eight rounds, reporting whether resolution
was reached. Narrow minima between unexplored grid points can be missed; the
result is the best tested candidate, with no global certificate. Exact ties
choose smaller gamma. A selected upper endpoint is flagged, never extended
automatically. These are configurable computational choices.

The small initial run uses 8 design and 8 validation samples, fresh root seed
20261015 and independent children 0/1/2 for design/validation/later delivery.
Only design outcomes select gamma. The two extreme design FI-capacity draws
receive tighter integration at the selected candidate. Validation evaluates
that candidate alone; the final delivery draw happens afterwards. J is composed
per sample before computing its standard error. Single-delivery uncertainty is
undefined; design intervals do not correct selection bias. Wall and process CPU
times are saved separately. See [CONFIGURATION.md](CONFIGURATION.md#weighted-tso-optimization)
for exact inputs, replay files and timing scopes. Generated results stay under
`results/tso_optimization/`; no new plot batch is produced.

### Implemented first study

Use `--scenario fi_endogenous --tso-sweep --output-dir results/tso_tradeoff`.
The current exploratory grid is 0, 10, 25, 50, 75, 100, 150, 200, 300 with
64 design and 64 independent validation draws (root seed 20261014). These are
explicit computational choices for the first curve. The horizon remains 40 s,
the disturbance 10 MW at 1 s, and the underlying distributions/costs remain
pedagogical. There is currently one FI provider in this preset.

Clearing/role resolution and realization construction are reusable stages.
The frequency-error integral is an additional numerical ledger; payment still
uses the accepted sampled-frequency contract. The held-target optimization is
unchanged. Known FI response zero crossings split numerical integration to
resolve gross reward/charge kinks without creating provider decisions.

The whole predeclared grid is evaluated on both banks. Paired differences use
common samples within each bank. Pointwise Monte Carlo intervals quantify
uncertainty in means; empirical frontier membership is not a population theorem.
There is no selected gamma to validate yet. At least the two extreme design
FI-availability draws receive tighter-solver checks at zero, near 50 and the
upper grid endpoint (duplicate cases are checked once).

See [CONFIGURATION.md](CONFIGURATION.md#tso-trade-off-curve) for saved artifacts,
timing definitions and reproduction. Numerical findings are kept with the
generated run under `results/tso_tradeoff/`, outside version control.

The first run completed 1,152 candidate/draw simulations plus six tighter-solver
checks. On the independent bank, gamma 25/50/75/100/200/300 reduces mean E_f
relative to zero by approximately 34.1/44.5/49.1/51.6/55.0/55.3%, with respective
mean FI payments of EUR 18.50/53.08/93.20/135.63/310.75/471.18 per 40 s event.
Gamma 50 therefore obtains about 80% of the tested maximum improvement for
about 11% of its expenditure. These are conditional numerical observations,
not a recommendation to restrict optimization to gamma 25–75. The appropriate
choice depends on the user weight and all configured physical/economic inputs.

Saturation is realization-dependent: gamma 50 reaches a target bound at least
once in 70.3% of design and 60.9% of validation draws. Across the 247 tested
positive-gamma/draw pairs with no target saturation, S/C ranges from 2.00008456
to 2.00014073. This supports the approximate observation, not an exact dynamic
identity. With availability support extending down to zero, a positive common
gamma cannot generally ensure no saturation across all possible draws.

The independent-bank phase took 218.56 s for 576 simulations (median 0.374 s
per run). Total recorded wall time was 4,689.74 s, including two anomalous
2,093.34/1,891.98 s evaluations during tool waits; their cause is unverified.
Preserve those raw timings and do not interpret the total as pure computation.
The local `results/tso_tradeoff/ANALYSIS.md` contains full findings and uncertainty.

## 1. Implemented decision and information timing

Use one nonnegative scalar gamma shared by the eligible FI providers in the
first TSO formulation, as explicitly selected by the user (D-019). The implementation
keeps it fixed over the simulated event. Provider-specific rho_i, response times
and availability remain distinct even with a common gamma: equal incentives
do not imply equal delivered powers. Provider-specific gamma_i optimization
is deferred; existing manual per-provider configuration remains available.

Sequence for one market block/event:

1. Providers submit FCR offers; the FCR market clears.
2. Resolve roles: assigned FI plus `fcr_or_fi` providers with zero FCR awards
   enter FI. Any positive award remains exclusively FCR.
3. TSO selects gamma using the operating point, awards, roles and a probability
   model for availability/disturbances, before the actual availability draw.
4. Draw actual availability; each provider observes its own realization.
5. Providers run the existing sampled-frequency interval optimizer with the
   announced, fixed gamma. Simulate delivered response and actual ledgers.

The user accepted TSO knowledge of rho_i, T_i and the availability laws for the
initial model (D-020). This is a full-information assumption about model
parameters, not observation of the future actual availability draw or a claim
about real markets. The current 10 MW step remains fixed; only availability is
uncertain. In the current offer heuristic, gamma does not affect
FCR bids, so awards and FI membership remain fixed within this outer search.
Strategic bidding in anticipation of FI revenue is outside this proposal.

### Horizon and later re-optimization

Distinguish the duration for which gamma is held from the horizon over which
the TSO evaluates performance and payments. In the initial event experiment,
the proposal makes both equal to H (initially 40 s), while the provider decision
interval remains 0.1 s and the FCR financial block remains four hours. These
three clocks are not interchangeable.

A later repeated-contract extension could select gamma_m at t_m and hold it
on [t_m, t_m + H), then optimize again using updated forecasts, model parameters
and measured physical state. Its evaluation horizon could also differ from
the contract-update interval; that would be an explicit later design choice.
Continuation must preserve frequency and individual responses at the boundary,
including any persistent deficit, and apply the specified ledger/availability
transition rules. It must not restart nominal frequency or introduce a fresh
10 MW disturbance at every renewal by default. A new gamma contract does not
itself require a new FCR auction; align the market and FI clocks explicitly if
both are to be updated. No repeated-cycle semantics are implemented yet.

## 2. Accepted objective: frequency quality plus actual expenditure

Let omega denote an availability/disturbance scenario conditional on the
already-cleared block, H the fixed simulated event horizon, and
e_gamma(t; omega) = f_gamma(t; omega) - f_N. Define

$$
E_f(\gamma;\omega)=\int_0^H e_\gamma(t;\omega)^2\,dt,
$$

$$
S_{\rm FI}(\gamma;\omega)
=\sum_{i\in\mathcal I}\sum_k\int_{t_k}^{t_{k+1}}
-\gamma\,e_\gamma(t_k;\omega)\,z_{i,\gamma}(t;\omega)\,dt.
$$

The accepted objective is the following; the finite search bound is still
an explicit implementation/domain choice:

$$
\boxed{\min_{0\le\gamma\le\gamma_{\max}}
J(\gamma)=\mathbb E_{\omega}\!\left[
w_f E_f(\gamma;\omega)+S_{\rm FI}(\gamma;\omega)\right].}
$$

- E_f measures both the depth and duration of frequency deviations, including
  any overshoot. Its units are Hz² s; w_f is positive in EUR/(Hz² s), making
  both objective terms EUR over the **same event horizon**.
- S_FI is the actual net TSO transfer, gross rewards minus gross charges.
  It uses the existing held frequency sample and delivered response, not a
  live-frequency replacement, target benchmark or predicted instantaneous power.
- Activation costs rho_i*z_i²/2 already enter the providers' optimization.
  Do not add them to the TSO's payment expense as if they were a second cash
  payment, or replace payments by provider profits. A social-cost objective
  would be a separate formulation in which transfers cancel.
- w_f is an explicit user input to the TSO, not an optimized variable or a
  known market price. A reduction Delta E_f is worth up to w_f*Delta E_f EUR.
  Require it in optimization mode rather than choosing an implicit default.
  A numerical value is needed to run a policy, not to implement the method.
  Do not normalize E_f by H or a baseline without redefining the weight.
- gamma_max is an explicit finite search bound to justify. If the selected
  gamma is at that boundary, report it and reassess the range; do not claim an
  unconstrained optimum. Saturation is part of the provider model, not a
  reason to exclude those candidate gamma values automatically.

Use the current 40 s horizon initially as a declared experiment choice. A
permanent deficit leaves a primary-control frequency offset; the objective
does not require exact restoration. Horizon sensitivity matters, since a
longer horizon increases the weight of the persistent offset and its payments.
No terminal penalty or secondary controller is added implicitly.

The FCR capacity payment is constant with respect to gamma for this fixed
cleared block. Keep it in a separate report; it need not enter the argmin and
must not be combined with event FI money without an event/block model.

## 3. Relation to the primary paper and alternative budget formulation

Local source: `references/2604.27770v2.pdf`, Matt, Bolognani and Dörfler,
*Optimal Functional Incentives for Control: The Linear-Quadratic Case with
Bilinear Incentives*, Section II, Eq. (2), and Section III, Eqs. (4)–(5),
PDF p. 3. The paper's leader cost includes state performance plus incentive
payment; its social cost instead includes follower effort cost after transfers
cancel. The scalar bilinear normalization is Theta = -gamma, R = rho/2.

The objective above is the **accepted project adaptation** to continuous physical time,
sampled settlement, multiple providers, first-order lag, saturation, FCR and
random availability/disturbances. The paper's closed-form gradients, optimal
parameters and stability results are not asserted for this extension.

The budget formulation below was discussed before D-022 and is not selected
for the first implementation. It remains a possible later alternative:

$$
\min_{0\le\gamma\le\gamma_{\max}}\mathbb E[E_f(\gamma;\omega)]
\quad\text{subject to}\quad
\mathbb E[S_{\rm FI}(\gamma;\omega)]\le B_{\rm event}.
$$

This substitutes an explicit expected net budget in EUR/event for w_f; it is
not automatically equivalent to every weighted problem in a nonconvex model.
Net expenditure can include negative transfers caused by harmful residual
delivery. Always report gross outlay and charges too. Choosing gross outlay
as the budget/cost, imposing a per-scenario cap, or using a risk measure would
be an explicit alternative decision, not an unnoticed accounting change.

Nadir/maximum absolute deviation and RoCoF can initially be reported as
additional physical metrics. If an operational frequency limit is imposed,
specify its numerical value, applicability and whether it must hold per tested
scenario or probabilistically. Expected performance alone is no safety guarantee.

## 4. Numerical method

For a frozen design scenario bank omega_j with weights pi_j summing to one,
evaluate

$$
\widehat J(\gamma)=\sum_j\pi_j
\left[w_f E_f(\gamma;\omega_j)+S_{\rm FI}(\gamma;\omega_j)\right].
$$

For independent samples from the chosen law, pi_j = 1/N is the sample average.
For hand-picked disturbances, weights must be supplied/justified; they are
not automatically event probabilities. The accepted first study conditions on
the current 10 MW step and varies availability only (D-020), limiting the scope
of any resulting optimum to that disturbance experiment.

1. Solve dispatch, offers and clearing once. Freeze FI roles and prepare a
   design bank of all providers' availability draws, respecting the configured
   bounded laws. Reuse exactly the same bank for every candidate gamma.
2. Scan a bounded gamma grid including zero and the applicable 50/200 reference
   values when within the chosen range. Simulate the full coupled response for
   each candidate: do not reuse a frequency trajectory computed at another gamma.
3. Inspect the frequency/expenditure trade-off and saturation. Refine promising
   intervals locally, comparing all retained candidates and endpoints. Do not
   assume convexity, monotonic improvement, or a single minimum. State the
   search resolution and whether the result is only the best tested candidate.
4. Evaluate the selected gamma on a separate availability sample bank. Reusing
   design samples for comparisons is useful; using them as the only evidence
   of out-of-sample performance is not. The actual delivery draw comes later.
5. Check numerical accuracy near the selected solution with fixed awards,
   samples and decision interval. Integrate e² consistently over the full
   horizon; do not introduce dependence on the CSV output step. Actual payment
   already has an integrated ledger. No plots are needed inside the search.

Report selected gamma, objective components, net/gross FI money, frequency
metrics, saturation, sample count/seeds, sampling uncertainty, search bounds
and failures. A feasible FCR market remains a prerequisite; FI does not fill
missing procured capacity. Failed trajectories cannot be accepted as an optimum.

Implementation now reuses `clear_fcr_block` and `realize_fcr_block` as separate
stages; `prepare_fcr_block` retains its ordinary one-draw interface. The evaluator
runs the existing physical/provider simulator and compact metrics, without
per-candidate trajectory plots. The original fixed-gamma mode remains available.

### Trade-off curve before choosing a weight or budget

For each candidate gamma, estimate two separate quantities on the same sample
bank: mean integrated squared frequency error and mean actual net FI payment.
Plot expected expenditure on the horizontal axis and expected frequency error
on the vertical axis, with gamma attached to each point. Lower and leftward
are preferable. There is no time axis here, and joining points is only a guide;
the actual candidate set need not form a monotone or convex curve.

Discard dominated candidates for selection: a candidate is dominated if
another has no larger expected error or expenditure and is strictly better in
at least one. The remaining set is the trade-off frontier among tested choices.
It displays what extra frequency performance costs without choosing w_f first.

With a weighted objective, select the candidate minimizing w_f*mean_error +
mean_payment. With an expected budget B, select the lowest-error candidate
whose mean_payment is at most B. The weight is a marginal valuation; the budget
is a spending constraint and need not be fully used. An expected budget is not
a cap on every realization. Weighted and budget formulations can select
different frontier points when the candidate set is nonconvex or discrete.
Once the candidate outcomes are saved, changing w_f or B only recombines or
filters them; new simulations are needed only to refine coverage/accuracy.

### Execution-time measurements requested for implementation

Instrument elapsed wall time with a monotonic high-resolution timer. Report
operating-point/market preparation, scenario-bank construction, one candidate
evaluation (all its samples), outer search, independent validation and total
runtime separately. Separate plotting/file export and cold-start overhead from
the design computation. Save simulation/evaluation counts, scenario count,
horizon, numerical settings, hardware and any parallelism; distinguish wall
time from CPU time if both are reported. Do not claim timings before measuring.

The first search happens before the event, with no hard real-time deadline in
this experiment. A future periodic update must finish before the next contract
starts or use an explicitly designed update schedule. Record performance now
without introducing premature parallel execution or changing the physical model.

## 5. First optimizer: implemented conventions

The objective family and first-version conventions are implemented. A numerical
w_f is required to run the optimizer; calibration and a larger statistical
study remain separate. The following conventions do not calibrate policy values:

1. Require a finite positive user weight, with units EUR/(Hz² s). Preserve
   the integrated squared error in Hz and the actual net FI settlement above.
2. Use a configurable domain [0, gamma_max], always testing zero. The previous
   upper endpoint 300 is only an exploratory starting value, not a theoretical
   bound. Report when the best tested value lies on that endpoint; a user cap
   must not be silently enlarged or confused with an unconstrained optimum.
3. Keep the configured event horizon initially at 40 s and gamma fixed within
   it. Use the existing post-clearing/pre-delivery timing. Repeated contracts,
   state continuation and renewed availability remain a separate extension.
4. Minimize the sample-average objective on a fixed design bank, with an
   exploratory grid and local refinement of promising intervals. Do not assume
   convexity or unimodality. Make sample counts, search resolution and stopping
   tolerances explicit; do not launch a large study merely to verify the code.
5. Compute each sample's J_j = w_f*E_f_j + S_j before estimating uncertainty
   in J: the two terms are correlated. Use a fresh independent bank to assess
   the selected gamma, never to select/refine it. The actual delivery draw is
   separate again. Refine numerical integration with fixed draws and clock.
6. Preserve physical/provider constraints. The first version adds no budget,
   hard frequency requirement or risk constraint to the selected expected-cost
   objective. Such additions would require separate modelling decisions.

Output gamma, w_f, estimated E_f, S and J, search domain/resolution, boundary
status, independent evaluation uncertainty, failures and timings. Report the
best tested/refined candidate without asserting a certified global optimum.
The algorithm should work with the configured parameters; any numerical optimum
remains conditional on those inputs. Tests cover multiple observed minima,
endpoints, numerical limits, covariance, independent validation, one shared FI
gamma and exact delivery replay, using the existing five test files.

## 6. Sampling-stability assessment

The user requested a larger-sample, few-seed check at w_f=10000, comparing the
objective as well as gamma. `--tso-stability` implements this separate study,
with the same search and model. The initial protocol is N=8/32/128, three
design seeds, and 256 shared independent held-out draws; computational settings
and exact streams are in [CONFIGURATION.md](CONFIGURATION.md#tso-sampling-stability).

Use nested prefixes within a seed, independent seeds across replicas, and cache
only identical gamma/draw simulations. Every search sees design draws alone.
Freeze the selected policy set before assessing it on the independent bank.
Use the previously selected gamma=52.34375 as the predeclared reference, not
whichever policy happens to win on the holdout. The holdout comparison measures
out-of-sample performance differences, not regret against a known optimum.

Estimate uncertainty from paired per-draw J differences, retaining both the
frequency/payment covariance and correlation between policies on common draws.
Report pointwise mean intervals, absolute EUR/event differences and percentages
relative to the reference mean. An interval containing zero does not prove
equivalence. A practical equivalence tolerance would need a separate explicit
choice; this initial assessment reports magnitudes without inventing one.

Gamma can move with sample size even when the objective is nearly flat. Three
seeds and a larger finite bank are a diagnostic, not a convergence certificate.
The domain and local search accuracy remain unchanged, and the conclusions are
conditional on the configured pedagogical parameters. This study introduces
neither calibrated inputs nor a changed objective, new constraints or repeated
contracts. Parallel workers distribute independent simulations only.

The initial study completed with final analysis on 16 October 2026. Selected
gammas across three seeds span 42.1875–52.34375 at N=8, 43.75–49.21875 at N=32,
and 45.3125–46.875 at N=128. The held-out mean objective at gamma=46.875 is
346.1788 EUR/event versus 346.8955 at the predeclared reference 52.34375.
The paired improvement is 0.7167 EUR/event (0.2066%), with pointwise 95% CI
[0.3551,1.0784]. These conditional results support a relatively insensitive
objective near the selected policies; they do not prove practical equivalence
or justify restricting the search domain. Full data and interpretation are
under `results/tso_stability/`, with seven focused tests and numerical checks.
