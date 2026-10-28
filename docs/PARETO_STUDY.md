# Controlled gamma sweep and frequency-performance Pareto curves

The 24 October 2026 user request implements the supervisor's diagnostic study:
vary a common gamma, observe expenditure and several frequency metrics, and
explain interactions of the existing FCR/FI mechanisms. RoCoF is a permanent
part of future frequency-performance assessments. No new controller, TSO
objective, security constraint or calibrated parameter is introduced.

## Commands and preservation

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --tso-pareto results/tso_tradeoff --output-dir results/tso_pareto
.\.venv\Scripts\python.exe scripts/run_fcr.py --tso-pareto-report results/tso_pareto
```

The first command requires a complete sweep with saved config, market, physical
parameters, availability banks and per-sample outcomes. It freezes the source
by SHA-256, checks the reconstructed market/roles/physical parameters, and
reuses the original draws and gamma grid. Source and destination cannot overlap.
The initial study reuses the pedagogical `fi_endogenous` sweep: 10 MW deficit
at 1 s, 40 s horizon, 64 design plus 64 independent validation draws, gamma
0/10/25/50/75/100/150/200/300, unchanged rho, response times and FI clock.

The old sweep saved scalar nadir and final frequency but no complete trajectories
or RoCoF. Those missing diagnostics require matched replays. Every historical
scientific metric is verified (rtol 1e-7, atol 1e-8) and retained exactly in the
augmented table. Original simulation timings remain historical; new replay
timings are stored in the candidate batches and study summary.

Three computational worker processes evaluate independent candidates. Completed
candidates are checkpointed and reused on restart with the same frozen plan.
Failed runs are marked incomplete and never averaged after dropping a sample.
The Python `run_pareto_study` function also accepts `workers=1` for serial runs.
Report-only mode verifies source and saved artifact hashes and performs zero
integrations. All generated outputs live in the separate ignored study folder.

## Metrics

Each metric is evaluated per realization, then summarized separately within
each bank. Candidate gamma values share the same draws within each bank.

| Metric | Definition | Direction |
|---|---|---|
| Net FI payment | Actual gross rewards minus gross charges, EUR/event | Minimize |
| Nadir | Minimum frequency on the output grid, Hz | Maximize for this positive-deficit case |
| Terminal absolute error | abs(f(H) - f_nominal), Hz | Minimize |
| Integrated squared error | Existing numerical ledger integral of delta_f², Hz² s | Minimize |
| Maximum absolute RoCoF | Maximum abs(df/dt) on the output grid plus the exact event right limit, Hz/s | Minimize |

RoCoF uses the existing swing-equation right-hand side:

`df/dt = (-D * delta_f - deficit + total_delivered_response) / M`,
where `M = 2 * H_equivalent * S_synchronous / f_nominal`.

This is instantaneous model RoCoF, without filtering or a measurement window.
It is not a finite difference of output frequencies or an instrument estimate.
Signed initial, minimum and maximum RoCoF are also retained. With zero initial
deviations/responses, the exact disturbance right limit is `-deficit/M`, even
when the disturbance is off the output grid. It is independent of gamma and
availability. A constant worst RoCoF can therefore coexist with better nadir,
terminal error and integrated error. Later extrema remain sampled estimates.

The final 5 s receive a stationarity screen: sampled frequency range <=1e-4 Hz
and maximum absolute model RoCoF <=1e-4 Hz/s, with a full window and at least
three samples. The actual sampled window duration is saved. These are declared
diagnostic thresholds, not physical/security limits or proof of asymptotic
convergence. Short/inadequately covered windows cannot pass. Plots always call
the endpoint metric **terminal error**, including when the screen passes.

Target-bound time and the fraction of draws touching a bound are distinct.
Held targets, delivered responses and available capacities remain separate in
the illustrative traces. FCR block payments are reported separately from FI
event transfers; provider activation costs are not additional TSO cash payments.

## Statistics and empirical Pareto membership

For each metric independently, a candidate is dominated when another tested
candidate has no higher estimated mean cost, no worse estimated performance,
and at least one strict improvement. Exact duplicate coordinates remain tied.
No untested continuous frontier is inferred. The joint four-performance-metric
Pareto set is not the same as these two-dimensional projections and is not
claimed by these figures.
Flags use raw sample means. Near a plateau, differences may be smaller than
the observed output/refinement discrepancies; inspect `numerical_checks.csv`
before interpreting frontier membership as a resolved performance difference.
Neither rounding nor numerical acceptance tolerances define practical equivalence.

Means use pointwise Student-t 95% intervals, not individual-event prediction
intervals or simultaneous bands. Baseline improvements are paired by sample;
positive differences always denote benefit. Statistical uncertainty is not
discarded when inspecting dominance, and empirical flags are not claims of
population dominance. No gamma is selected using either bank. Any later
data-informed policy selection requires an appropriate independent assessment.

The accepted weighted TSO objective remains `w_f * E[E_f] + E[S_FI]`.
Sweeping gamma does not require choosing w_f. RoCoF, nadir and terminal error
are reported performance metrics, not silently added optimization terms.

## Numerical checks and artifacts

For every gamma, the two design draws with extreme total FI availability are
repeated with half output spacing, half maximum integration step and tenfold
tighter rtol/atol. The provider decision clock and horizon stay unchanged.
Acceptance tolerances are 1e-4 Hz for nadir, 1e-4 Hz/s for peak absolute RoCoF,
1e-6 Hz for terminal error and matched frequency trajectory, 1e-7 Hz² s for
integrated error, and 1e-5 EUR for payment/cost. These are numerical checks,
not tolerances defining practical Pareto equivalence.

- `study_plan.json`: immutable source hashes and diagnostic conventions.
- `sample_outcomes.csv`: preserved historical observables plus new diagnostics.
- `gamma_summary.csv`: bank-specific means, intervals, paired improvements and flags.
- `pareto_frontiers.csv`: long-form membership by bank and performance metric.
- `numerical_checks.csv`, `verification.json`: refinement and source/replay checks.
- `illustrative/`: all gamma trajectories for predeclared design sample 0.
- `batches/`: candidate checkpoints and replay/refinement wall/CPU times.
- `summary.json`, `study_status.json`, `report_status.json`: counts, timing, integrity and completion.
- `ANALYSIS.md`: definitions, estimates, frontiers and interpretation limits.
- `metrics_vs_gamma`, `pareto_curves`, `diagnostics`, `illustrative_trajectories`:
  PNG previews and editable-by-code vector PDF figures.

The illustrative plots show gamma zero, nearest 50, nearest 100 and the maximum
tested gamma, always on the same predeclared draw. They are not expectations.
The A/B/C fleet extension is a subsequent controlled study; these first results
describe only the preserved pedagogical configuration.
