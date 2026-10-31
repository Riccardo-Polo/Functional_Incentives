# AEMO-inspired FPP benchmark

## Current two-provider gain game

The maintained command selects the two renewable followers' gain policies
before delivery. The FPP-specific preset declares fixed FCR offers of 4 MW
from each conventional provider at 15, 18 and 24 EUR/MW per block. Both
renewables submit zero FCR offers. Clearing supplies 12 MW and fixes the roles
before availability is sampled. The catalogue is `[0, 0.5, 1, 1.5, 2]`.
Costs and optional monetary settlements remain separate accounting outputs.

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-benchmark --config configs/aemo_fpp_metered.toml --output-dir results/fpp_gain_game
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-report results/fpp_gain_game
```

For each candidate pair the plant starts at the same solved point and reuses
every provider availability and the entire disturbance path of the design case.
Both controllers react to the resulting common frequency. At tick k a provider
computes `clip(-theta * K * db(e_k), -A, A)`, then holds the target for 0.1 s.
The score is the sum of its two signed CF × RCR / 12 coefficients in MW h.
Ideal FM equals minus the measured frequency error at each 4 s sample. Every
finite sample enters its direction directly. Empty directions have zero scores,
RCR and coefficients. The accounting residual closes each directional ledger.

The two private mean-score tables are evaluated by columns for player 1 and
by rows for player 2. The selector chooses the first pure mutual best response
in ascending catalogue order, using a numerical tolerance of 1e-10 MW h.
When no pure profile qualifies it enumerates supports, solves linear feasibility
problems for independent mixtures, and verifies the two deviation gaps.
A mixed policy draws one gain per player per episode, before that episode's
availability, and retains it for the horizon.

The equally likely disturbance paths and samples are configurable in
`configs/fpp_followers.toml`. These paths are a synthetic prior. Controllers
observe their own capacity and current frequency. Seed children 0, 1 and 2
supply assessment, delivery and design cases respectively. Children 3 and 4
select episode gains for assessment and delivery. `design_scenarios.csv`
and `availability_draws.csv` record the full draws and path IDs.
`{design,assessment}_gain_scores.csv` record each paired score and the matching
`*_gain_payoffs.csv` give the two mean tables. `gain_equilibrium.json` stores
the frozen distributions, pure candidates, numerical gaps, and separate training
and holdout timings. `holdout_deviations.csv` retains paired gains and uncertainty
for every unilateral deviation on fresh cases. The design equilibrium applies
to the finite catalogue and design bank. The representative realization and
holdout uncertainty have separate interpretations.

The default counts give 200 design and 200 holdout profile evaluations, then 27
comparison integrations and three numerical refinements. An entirely undisturbed
design or holdout case has exactly zero score for every profile at the solved
equilibrium. These cases are counted in the averages and recorded separately
from numerical integrations. Each comparison uses
the same physical draw for FCR, FI and FPP. A prescribed reversal, if present,
overrides only the delivery path. Saved legacy overlays with the historical
source profile retain continuous fixed droop and the original FM rules.

### Paired comparison with the previous follower

Use a completed gain study as the source and a separate new directory for the
controller comparison. The command loads the frozen episode gains and the
independent delivery availability vector. Both test cases start with +10 MW at
1 s. One continues to 300 s and the other switches to -7 MW at 120.05 s. Each
300 s trajectory is evaluated for the historical continuous droop, sampled
unit gain, and selected gains. All three use the same ideal measurement profile,
constant ANDES references, FCR awards and physical bounds. The intermediate
unit-gain case separates sampling effects from gain selection. The command
runs serially, refines all six trajectories, and writes the paired metrics,
scores, figures and ANALYSIS.md.

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-compare-controllers results/fpp_gain_game --output-dir results/fpp_controller_comparison
```

## Historical fixed-policy benchmark

The following sections document the saved studies from 26-29 October. Their original experiment commands refer to the configuration at that revision. Use the command above for a new gain-game study and `--fpp-report STUDY_DIR` to read a completed historical study.

Implemented 26 October 2026. This opt-in benchmark compares FCR alone, the
existing bilinear FI contract with explicit gamma=50, and a causal fixed-droop
FPP controller. It uses the existing five-provider `fi_endogenous` configuration,
solved ANDES operating point, 60 Hz frequency state, 10 MW deficit, FCR clearing,
provider identities, inertia, limits, gains, response times and availability laws.
The comparison changes the complete policy (controller plus payment mechanism);
it does not identify a behavioural effect of payment design alone.

**Current reference revision (29 October 2026, D-034):** every generator uses
its solved ANDES P_i,0 as a constant settlement reference for the whole horizon.
Meters retain actual model output P_i,0 plus numerically delivered response,
so settlement deviation equals delivered response. All maintained FPP presets
use this reference for assessment and delivery; the previous C1/C2 endpoint
reallocation is removed. New outputs use `results/aemo_fpp_constant/`.
Historical studies, including `results/aemo_fpp_metered/`, remain unchanged.
The 27 October actual-output metering correction remains in force.

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-benchmark --config configs/aemo_fpp_metered.toml --output-dir results/aemo_fpp_constant
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-report results/aemo_fpp_constant
```

## Commands and outputs

The separate, user-requested reversal experiment uses
`configs/aemo_fpp_reversal.toml` and `results/aemo_fpp_reversal/`:

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-benchmark --config configs/aemo_fpp_reversal.toml --output-dir results/aemo_fpp_reversal
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-report results/aemo_fpp_reversal
```

It retains the original 10 MW deficit at 1 s, then prescribes a **net 7 MW
surplus at 120.05 s** through 300 s. The change is -17 MW, not an additional
7 MW deficit. These times and amounts are scenario assumptions chosen before
simulation: 120 s allows initial responses and FM to settle, unequal magnitudes
avoid a mirrored event, and 120.05 s is off both the 0.1 s FI decision clock and
4 s FPP measurement clock. Both directional settlements and transient FM memory
can be inspected within the same complete 300 s interval. No policy is selected
on the new results. Prices remain absent, as in the original comparison.

Optional `[[disturbance_changes]]` entries contain `time_s` and
`net_power_deficit_mw`. Each value replaces the previous absolute net deficit;
negative means surplus. Times must be finite, strictly increasing, after the
initial event and before the horizon. The same aggregate integrator restarts at
each discontinuity while preserving all states and ledgers. FI decisions remain
on their original clock. Availability is drawn once per provider for the entire
event, and the original banks are reused. Both exact one-sided model-RHS RoCoF
limits are saved in every run's `disturbance_limits.csv` and included in extrema.
The old single-step API and original study remain reproducible.

The four figures include reversal zooms, the net-disturbance profile, excluded
performance samples, zenith and FI charges. PNG and vector PDF versions are
saved. `baseline_replay_checks.csv` and `preservation_audit.json` record matched
inputs, agreement before reversal, and hashes of the preserved original outputs.

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-benchmark --config configs/aemo_fpp.toml --output-dir results/aemo_fpp
.\.venv\Scripts\python.exe scripts/run_fcr.py --fpp-report results/aemo_fpp
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

The first command refuses an existing study status file. To reproduce a completed
experiment, use a new directory such as `results/aemo_fpp_replay`; do not overwrite
historical studies. Report-only mode reads saved data and performs no integration.
On restricted Windows hosts, set `TEMP` and `TMP` to an existing writable
`results/test_tmp` directory for the file-writing tests.

The overlay selects a base scenario and changes horizon/numerical settings.
Its optional prescribed disturbance changes and prices are separate inputs;
source constants are not knobs. Omitting `schedules` and `assessment_schedule`
resolves to the constant solved-power reference. Explicit historical schedule
overrides remain supported for reproduction, and report-only mode reads saved
overlays rather than applying the new default to old results.
Policies and schedules are frozen before sampling. Eight assessment draws use
NumPy SeedSequence(20261026), child 0; an independent representative delivery uses
child 1. All policies reuse each draw. No optimization, sweep, assessment-based
selection is performed. Each trajectory lasts 300 seconds;
historical 40-second payments are not used in the comparison.

New outputs include resolved inputs, offers/awards, solved powers, original and
replacement assignments, exact draws and provenance, all meter/response
trajectories, per-provider saturation, block accounting, per-run timings,
refinement checks, paired statistics, four figures and `ANALYSIS.md`.
`reference_checks.csv` verifies constant P_i,0 and deviation=response on every
saved constant-reference trajectory. The standard study uses 30 integrations:
24 assessment, three independent delivery and three numerical refinements.
For FPP runs, `fpp_samples.csv`, `fpp_filter_history.csv`, `fpp_directions.csv`
and `fpp_accounts.csv` preserve the settlement intermediate quantities.

## Verified sources and parameter classification

Sources were inspected on 26 October 2026. The profile is named
`aemo_fcf_v1.2_2025-06-08`; this is a declared historical profile, not a claim
that every current Australian implementation detail is reproduced.

| Source / version | Locator used | Implemented role / limits |
|---|---|---|
| Local [Frequency Contribution Factors Procedure](../references/final-frequency-contribution-factors-procedure.pdf), v1.2, effective 8 June 2025, approved 12 May 2025 | Sections 4.1-4.3, pp.9-10; 5.1/5.3, pp.11-12; 6.2.1-6.2.4, pp.14-16; 7.1-7.4, pp.20-21 | FM, exclusions/reliability, scheduled reference, residual, signed performance/CF, deviation-based directional RCR. Historical defaults, multiple areas and missing-data recovery are excluded. |
| Local [FPP FAQ](../references/fpp-frequently-asked-questions-faqs-version-6-may-2025.pdf) | Version history p.2 lists v6 dated **15 April 2025** despite the filename saying May and footer saying December 2024; questions on FPP amounts p.18 and binding constraints p.24 | Context for constraint-specific payment, separate Regulation FCAS recovery and residual. Not used instead of the procedure equations. |
| [Frequency Contribution Factor Tuning Parameters and Input Sources](https://www.aemo.com.au/-/media/files/stakeholder_consultation/consultations/nem-consultations/2022/frequency-contribution-factors-procedure/final-documents/turning-parameters-and-input-sources.pdf?rev=f0bea162118e4c089001ed67865966da&sc_lang=en), one-page table, effective 8 June 2025 | FM smoothing-factor row, section 4.1 | Alpha=2/9. Other listed telemetry/HPP thresholds are outside this ideal-metering scope. |
| [FPP Factor Calculation Guide](https://www.aemo.com.au/-/media/files/initiatives/frequency-performance-payments-project/aemo-nem-fpp-factor-calculation-guide.pdf?la=en), cover **9 December 2024** | Section 5.1.1, pp.18-20; alignment discussion p.21; Appendix A1.1/Table 15, pp.44-46; section 4.4, pp.15-16 | Reset with preceding 120 seconds, first history FM=0; source 0.015 Hz alignment band. Official Table 15 values independently check indexing and sign-memory. Price discussion distinguishes adjusted constraint values. Website listing dates/footer years are not substituted for cover date. |
| [Primary Frequency Response Requirements](https://www.aemo.com.au/-/media/files/initiatives/primary-frequency-response/2024/primary-frequency-response-requirements-clean.pdf?rev=d490aab52bcf47a9822e7b4184564df7&sc_lang=en), v3.0, effective 3 June 2024 | Section 1.2.1, PFCB definition, p.4; section 3.2, deadband, p.7 | Australian band 49.985-50.015 Hz supplies the absolute half-width. Keeping 0.015 Hz around 60 Hz for this fixed controller is an explicit adaptation, not regulatory compliance. |
| [Settlements Guide to Ancillary Services and Frequency Performance Payments](https://www.aemo.com.au/-/media/files/electricity/nem/data/ancillary_services/2025/settlements-guide-to-ancillary-services-and-frequency-performance-payments.pdf?rev=e7c4fb3fc63347fca83cd07fa283f9c0&sc_lang=en), **v7.2, effective 2 December 2025** | Section 4, p.14; section 1.2, p.5 | Signed transfer CF * P_regulation * RCR / 12; P_regulation is the adjusted price of the relevant constraint. Participant-level residual allocation is not implemented. |

| Classification | Values / choices |
|---|---|
| Fixed source profile | 4 s samples, 300 s intervals, 75 counted samples; alpha=2/9; preceding 30 history samples; at least seven directional FM values and a strict 0.01 Hz amplitude crossing; performance misalignment outside +/-0.015 Hz |
| Existing physical/economic inputs | Solved powers and identities, MVA/droop power bases, online synchronous inertia, active-power limits, K, T, bounded Gaussian laws, FCR offers/awards/costs; gamma=50 and rho=1 for the switched provider in this scenario |
| External scenario data | Optional signed net-deficit changes and separate raise/lower hourly regulation prices with source/date/currency/scope/evidence metadata; prescribed dispatch targets only for historical replay |
| Explicit adaptations | Single area; constant solved ANDES P_i,0 reference for every unit; exact synchronized metering of model output; nominal initial prehistory; retained FCR market; fixed-droop FPP follower with 0.015 Hz deadband at 60 Hz; NULL factors retained without historical substitution; valid zero performance maps to CF=0 |
| Numerical settings | 8 matched draws plus independent delivery; seed 20261026; output step 0.05 s; maximum RK45 step 0.1 s; rtol=1e-11, atol=1e-13; refinement halves both steps and divides tolerances by ten |

## Physical control and dispatch

ANDES solves the initial network operating point. The existing aggregate solver
integrates frequency and individual delivered responses; ANDES does not perform
this aggregate dynamic integration. Original FCR providers keep exactly one
response state with bound min(award, availability), the same unmodified controller
and the same whole-block accounts. Gains remain resolved by the existing
`clear_fcr_block` identity-based path, including explicit overrides. FPP neither
recomputes K from P_max nor retunes it from awards, availability or prices.

Only providers already assigned to FI after clearing switch control in the FPP
case. Their law is

$$
\operatorname{db}(e)=\operatorname{sign}(e)\max(|e|-0.015,0),\quad
v_i=\operatorname{clip}(-K_i\operatorname{db}(\Delta f),-A_i,A_i),\quad
T_i\dot z_i=-z_i+v_i.
$$

This is a continuous, causal fixed controller using instantaneous unfiltered
frequency. FM is used only for settlement. The FPP provider receives no bilinear
FI payment; its existing quadratic activation cost is integrated as a live ledger.
This controller does not maximize FPP profit. Price changes do not change its
trajectory, and the bilinear analytical best response does not apply to FPP.
FPP settlement eligibility is independent of physical role: all metered units,
including FCR providers, enter its accounting without receiving extra controls.
FCR offers do not anticipate FPP income. The accepted TSO objective and optimizer
remain specific to bilinear FI.

The current reference is fixed before the event at the solved operating point:

$$
\operatorname{ref}_i(t)=P_{i,0}\quad\text{for all }t\in[0,H].
$$

This uses each provider's solved active power, including the ANDES slack
solution, matched by provider identity. The meter reads physical model output:

$$P_i^{\mathrm{meter}}(t)=P_{i,0}+u_i(t),\qquad
d_i(t)=P_i^{\mathrm{meter}}(t)-\operatorname{ref}_i(t)=u_i(t).$$

Here u_i is delivered response, including the state named z_i for FI/FPP, not
the requested target. No dispatch tracking controller, noise or artificial
tracking-error signal is added. The reference simplification affects settlement
accounting; frequency, RoCoF and actual delivered power retain the same dynamics.

The current first interval is (0,300] s, with these constant references:

| Provider | Reference at 0 s [MW] | Reference at 300 s [MW] | Physical role in FPP case | FPP settlement |
|---|---:|---:|---|---|
| Conventional 1 | 260 | 260 | Original FCR controller | Included |
| Conventional 2 | 310 | 310 | Original FCR controller | Included |
| Conventional 3 | 250.906095586 | 250.906095586 | Original FCR controller | Included |
| Reliable renewable | 90 | 90 | Original FCR controller | Included |
| Uncertain renewable | 90 | 90 | Fixed-droop FPP follower replacing FI | Included |

No endpoint dispatch choice is required for the current model. The residual
is an additional accounting entry, not a sixth generator. Settlement participation
is exported separately from controller assignments in `fpp_participants.csv`.

Historical studies used linear interpolation between prescribed DT knots,
including C1 260 -> 261 MW and C2 310 -> 309 MW. The generic `DispatchSchedule`
and explicit overlay fields remain available for those historical reproductions.
Their saved metering provenance and reference choice govern report regeneration;
they are not silently reinterpreted as constant-reference results.

Every boundary target must preserve both margins for the **entire unchanged
availability support**, not just the realized draw. Every segment is balanced:
sum(reference-P0)=0. Checking linear-segment endpoints establishes these
properties throughout the segment. Unbalanced, infeasible or unsolved-initial
schedules are rejected; capacity/distributions are never reduced to make them fit.
Delivered total power is also checked against explicit active-power limits.

## Measurement, FM and settlement

Intervals own their right endpoint: (0,300], (300,600], etc. The counted times
are 4,8,...,300 and 304,...,600. The solver evaluates exact settlement times
through its existing dense output, independently of plotting steps. The settlement
function requires these synchronized samples and rejects interpolation of missing
measurements or incomplete intervals.

For interval starting at t_j, the filter window has 105 rows:
t_j-116,t_j-112,...,t_j (30 preceding rows), then t_j+4,...,t_j+300.
Assign FM=0 to the **first history row**, then recurse from the second row:

$$FM_k=\frac79 FM_{k-1}+\frac29(-\Delta f_k).$$

Keep only the final 75 FM values for settlement. Initially the 30 preceding
measurements are nominal, including t=0. Later windows use actual preceding
measurements and restart by the same rule, rather than carrying a previous
interval's stored filter state. This follows Table 15's 12:03:04 zero start
before the counted interval beginning after 12:05:00. It does not reset FM to
zero at the first counted sample. Full history is exported for audit.

Exclude a sample from **performance only** when FM*delta_f>0 and
abs(delta_f)>0.015 Hz; equality at the band boundary is included. Reliability
uses all raw FM samples: raise needs at least seven FM>0 and at least one
FM>0.01; lower analogously FM<0 and FM<-0.01. The amplitude check concerns FM,
not instantaneous frequency. An unreliable direction has NULL performance,
undefined CF and RCR=0. Its transfer coefficient is zero by the RCR-zero rule;
this does not relabel NULL as valid zero performance.

For each unit and the residual d_res=-sum(d_i), performance is

$$P_i^+=\sum_{k\notin X}\max(FM_k,0)d_{i,k},\qquad
P_i^-=\sum_{k\notin X}\min(FM_k,0)d_{i,k}.$$

There is no four-second multiplier: performance has MW Hz units, not energy.
Normalize each nonzero performance by the absolute sum of performances with
the same sign, including residual. Valid zero performance has CF=0; all-zero
populations yield zero factors. No adjustable denominator is introduced. Partial
NULL populations require unsupported historical defaults and raise an explicit
error. Missing frequency or power values also raise errors instead of becoming
zero measurements; telemetry recovery is outside the first version.

For reliable directions, use the original, unmasked FM sign:

$$RCR^+=\max_{k:FM_k>0}\sum_{i\text{ including residual}}\max(d_{i,k},0),$$
$$RCR^-=-\min_{k:FM_k<0}\sum_{i\text{ including residual}}\min(d_{i,k},0).$$

The residual is an accounting entity and never enters the physical power balance.
RCR is not disturbance size, FCR requirement, mean response or a prescribed budget.
The single-area assumption removes cross-area weighting/alignment requirements.
For each direction, export coefficient B_i=CF_i*RCR/12 [MW h], and when priced
compute payment=B_i*price with price in currency/(MW h). Gross rewards and
positive charge magnitudes are formed from each signed directional interval
payment before aggregation. Keep the residual when checking balance.

## Economic inputs and interpretation

The initial experiment deliberately supplies no prices. This avoids selecting
prices to obtain a preferred FI/FPP ranking or treating a unit-price normalization
as calibrated revenue. Missing monetary fields mean unpriced, not zero income.
The saved MW h coefficients permit repricing a fixed policy without integration.

Optional `[prices]` fields are `raise_per_mw_h`, `lower_per_mw_h`, `currency`
(AUD or EUR), `source`, `date` (ISO), `scope`, `evidence` (observed, illustrative,
proxy), `price_basis` (adjusted_constraint, regional_market_proxy, synthetic),
and `units="currency/(MW h)"`. Both directions use the declared common currency;
no exchange conversion is implicit. Regional market prices must be labelled
proxies. The relevant Australian P_regulation is the adjusted constraint price,
which can differ from its marginal value after price adjustments and is not
automatically a regional regulation market price. It is unrelated to the current
FCR price in EUR/MW per whole block.

Tests use explicitly synthetic 120/60 AUD/(MW h) prices. Independent hand
calculations check raise deviations (2,-1), residual -1, CF=(1,-1/2,-1/2),
RCR=2 MW, producing (20,-10,-10) AUD; lower deviations (1,-3), residual +2,
CF=(-1/3,1,-2/3), RCR=3 MW, producing (-5,15,-10) AUD. These values are
test fixtures only. The official FM example independently checks source indexing.

No activation costs are inferred for other providers. FCR payments/costs retain
their whole-block units; FI/FPP settlement covers the simulated horizon. AUD FPP
payments cannot be netted against EUR activation costs without a sourced
conversion. A balanced transfer ledger does not eliminate resource costs.
FPP transfers and Regulation FCAS procurement/recovery remain separate.

Excluded: NEMDE/energy optimization, AGC, Regulation FCAS procurement, multiple
regions/interconnectors, dispatch tracking dynamics, historical default factors,
detailed telemetry recovery, participant allocation of residual, optimized droop,
strategic equilibrium, energy limits and secondary restoration. No frequency
security threshold or new TSO objective is introduced.

## Historical supervisor design discussion (29 October)

Supervisor discussion, 29 October 2026 (D-035): focus the next design on
redistribution from harmful to helpful contributions and a tractable provider
optimization. The present FI provider optimizes its response while the FPP
follower uses prescribed droop. This asymmetry prevents interpreting their
comparison as a fair comparison of equally optimized incentive mechanisms.

The meeting suggested simplifying operational measurement heuristics in the
ideal, noise-free model, potentially including FM filtering. This is a research
direction, not a change to the implemented source profile. Filtering also
changes temporal weighting and signal memory even without noise, so removing
it requires an explicit settlement decision and checks of the retained
redistribution property. Only the constant reference is implemented now.

Questions to resolve before selecting a simplified game or optimization:

- If every physical provider is helpful, who funds the rewards? Distinguish
  budget balance over the physical providers from balance including the current
  accounting residual; residual allocation is not implemented.
- If only one or two providers are harmful, how concentrated are the charges,
  and how do their magnitudes depend on normalization, RCR and prices? Do not
  assume a large charge follows solely from having few harmful providers.
- How can a provider act with causal, limited information about others? Unknown
  realized actions need not be known in advance to define a best response under
  declared beliefs, forecasts or a strategy model. None is adopted yet.
- Is a tractable Nash equilibrium available after explicitly justified
  simplifications? Specify players, strategies, information and payoffs first;
  existence, uniqueness and computability are open, not assumed.

The subsequent task was to optimize an FPP provider's behavior. The current
`--fpp-benchmark` is a fixed-policy comparison: the uncertain renewable uses
the prescribed deadband droop follower, while all five generators participate
in FPP settlement. It supplies no FPP price in the main study and therefore
does not estimate the provider's monetary revenue or optimize its response.
The existing bilinear FI provider optimization and common-gamma TSO optimizer
solve different problems.

Before implementing the provider problem, decide and document:

- the action to optimize (for example, a response target or a controller
  parameter), when it may change, and its physical bounds;
- the provider's causal observations and forecasts, including treatment of
  availability, future frequency, other providers and the system-wide
  contribution factor and RCR;
- the objective, horizon and accounting units, including directional FPP price
  provenance, currency, activation cost and any retained FCR or energy cash flow;
- how the transfer ledger is closed, including all-helpful/all-zero cases and
  concentrated charges, while retaining the constant P_i,0 reference;
- a frozen-policy evaluation on matched draws and an independent holdout,
  with physical frequency metrics including RoCoF and auditable settlement.

These are open modelling choices, not newly adopted controller equations or
market rules. Preserve the metered baseline and its saved draws for comparison.
The [work plan](NEXT_STEPS.md#3-current-next-increment-fpp-provider-optimization)
sets out the proposed implementation sequence.

## Verification

The 29 October constant-reference run completed 30 integrations, including
three numerical refinements; all 108 tests passed. All 27 matched physical
trajectories (frequency, RoCoF and actual power) reproduce the previous metered
study exactly. Reference error is zero; exported deviation=response error is
at most 2.89e-14 MW. The original constant-reference settlement coefficients
also reproduce. `results/aemo_fpp_constant/change_audit.json` records these
checks, 530 unchanged historical files, and report-only regeneration with zero
integrations and preserved scientific data. Four figures were visually checked.

Tests stay within the five existing test files. They cover source interpolation,
endpoint ownership, balanced ramps and headroom, synthetic meters, helpful/harmful
directions, FM sign memory and preceding-history resets, strict reliability and
misalignment boundaries, NULL/zero distinctions, zero denominators, residual
accounting, RCR and monetary units, price provenance, fixed-gain mapping,
instantaneous causal control, independent clocks, cost integration and repricing.
Existing dynamics, market, offer, grid and experiment regressions are retained.

The comparison checks solver success, finite states, response/total-power limits,
schedule feasibility and ledger balance. Tightened checks reuse the same draws.
Limits: 1e-6 Hz trajectory difference, 1e-5 MW response difference, 1e-7 Hz² s
integral difference, 1e-5 Hz sampled-nadir difference, 1e-4 EUR known-cost/FI-payment
difference, 1e-6 MW h coefficient difference and 1e-5 MW RCR difference.
RoCoF is the unfiltered physical RHS with the exact event right limit; maximum
absolute value is reported over the declared horizon with **no smoothing window**.
Assessment statistics remain separate from representative delivery plots.
