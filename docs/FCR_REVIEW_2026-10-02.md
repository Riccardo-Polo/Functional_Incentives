# Review of the uncommitted FCR implementation

**Date:** 2026-10-02. **Branch:** `fcr-provider-dynamics`.
Review scope: new provider, simulator, configuration, runner, figures and tests,
plus tracked edits present in the workspace. The initial review was read-only.
**Follow-up status: R1–R3 fixed on 2026-10-02**, along with non-finite parameter
validation and explanatory comments. The changes remain uncommitted.

The default teaching experiment reproduces the documented results. The
equations, signs, units and separation of initial powers from deviations agree
with D-009/D-010 and the Week 3 model. The 14 original tests passed at review
time. The expanded suite now includes regression coverage for the failures
below, inactive providers and finite-value validation.

## Findings and resolutions

### R1 / P2 — Different timestamps: fixed

Location: `scripts/run_pjm5_fcr_teaching.py`, construction of
`analytical_regression_error_hz` and the combined CSV;
`grid/aggregate_frequency.py`, analytical sampling grid.

Originally, the numerical grid appended the final time while the analytical
helper rounded the endpoint differently. Arrays were then subtracted and
combined by position. The review reproduced these failures with the default
physical parameters and a disturbance at 1 s:

| Final time [s] | Output interval [s] | Observed result |
|---:|---:|---|
| 40 | 0.3 | Numerical array has 135 samples, analytical has 134; subtraction fails |
| 2 | 0.3 | Both have 8 samples, but end at 2.0 and 2.1 s; false error of about 0.0115372 Hz |

The second case labelled an analytical sample with the wrong timestamp in the
CSV. Both simulators now use `time_grid.make_output_times`, so their samples
have identical timestamps. The runner also checks equality before comparisons
and CSV construction. Regression tests cover both examples and verify times
as well as numerical values.

### R2 / P2 — Missing requested endpoint: fixed

Original location: `simulation/aggregate_fcr.py`, `_output_times`, specifically
the default-tolerance `np.isclose` check before appending the endpoint.

With event time 10.00001 s, final time 10.00005 s and output interval 1 s, the
last recorded time was 10.0 s. The returned deficit and state were entirely zero,
although the requested interval included the event. The closeness check treated
the earlier sample as if it were the requested endpoint.

The shared grid now keeps samples strictly before the horizon and appends the
exact endpoint once. The solver integrates from the exact event using `t_span`;
there is no approximate event-time equality or insertion/discard step. The
short event above is now recorded, with a negative final frequency deviation.

### R3 / P3 — Hard-coded final-time labels: fixed

Location: `scripts/run_pjm5_fcr_teaching.py`, final frequency and provider prints.
The script accepted another `final_time_s` but still printed `at 40 s`, `u(40 s)`
and `P(40 s)`. All those labels now use the final output timestamp. Runner
regression coverage checks labels and CSV values with a custom horizon.

## Additional repairs

- Provider, physical-model, damping and simulation settings now reject NaN and
  infinite values with a named `ValueError`. Previously an infinite provider
  time constant was accepted and silently disabled its response. Tests include
  invalid provider values read from TOML.
- The numerical convergence test now covers all $u_i$ trajectories as well as
  frequency. Boundary tests cover zero disturbance, zero gain and zero reserve.
- The provider derivative comment now correctly groups the numerator as
  `(clip(...) - u_i) / T_i`. The implemented equation was already correct.
- The `aggregate_frequency.py` module comment now correctly limits the
  constant-input assumption to the analytical `simulate_power_step` function.

## Remaining development work (outside these bug fixes)

- The runner prints analytical and convergence errors without applying
  thresholds; tests currently enforce them only for the default configuration.
  Keep that distinction clear in descriptions of a run's validation status.
- Provider/generator association is positional. Before the market can reorder
  offers or select a subset, use an explicit provider-ID-to-generator mapping.
- A saved run currently contains trajectories and figures but no copied config,
  revision or dependency metadata. Preserve these with future market experiments
  so the result remains reproducible after parameters change.

Physical headroom, economic costs and market allocation are declared future
features, not regressions in the agreed teaching scope. In particular, a reserve
bound check does not certify a generator's active-power or energy capability.

## Verification performed

The existing suite was run with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

All 14 original tests passed at initial review. After the repairs the default
runner was re-executed into a temporary output directory to preserve the
existing result files. It again reproduced 59.400035 Hz
without FCR and 59.882609 Hz with FCR at 40 s, with a lowest sampled FCR
frequency of 59.827191 Hz. Maximum numerical/analytical no-FCR difference was
$1.504\times10^{-14}$ Hz; maximum refined-solver frequency difference was
$4.893\times10^{-9}$ Hz. Sampled reserve limits were respected.

R1 and R2 are now included in automated regression tests, together with R3 and
finite-value validation. The mathematical model and teaching parameters are
unchanged. No staging, commit or publication has been performed.

## Suggested next action

Proceed to specifying the market and provider decision stages described in
[FCR_MARKET_PLAN.md](FCR_MARKET_PLAN.md). The reviewed sampling and label bugs
are resolved; the open economic and capability choices remain as documented.
