# FCR procurement reduction on the preserved A/B/C fleets

Protocol dated 18 October 2026 (D-028). The user requests reducing FCR demand
in the existing mixed-fleet scenarios, preserving every earlier study and all
other inputs. New outputs belong only in `results/fcr_requirement/`.

**Completed and verified on 18 October 2026.** All six new optimizations and
96 full-FCR validation evaluations finished successfully. The three 12 MW
optimizer runs and all earlier studies are preserved. Full numerical tables
and figures are in `results/fcr_requirement/ANALYSIS.md`.

## Completed results and interpretation

The 10 MW cases provide a conditional cost/performance compromise relative to
12 MW plus FI. They do not preserve that reference's frequency performance.
At 8 MW, FI resource limitations become much more consequential; increasing
gamma cannot replace the missing reserve in low-availability draws.

The following are independent validation means, not the plotted delivery:

| Fleet | FCR MW | Gamma | Mean nadir [Hz] | E[E_f] [Hz² s] | Mean FI [EUR/event] | FI activation cost | FI profit |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 12 | 33.59375 | 59.91528 | 0.056408 | 37.16 | 15.04 | 22.12 |
| A | 10 | 36.71875 | 59.90603 | 0.091283 | 61.51 | 23.48 | 38.03 |
| A | 8 | 50.78125 | 59.87768 | 0.279307 | 185.26 | 49.57 | 135.69 |
| B | 12 | 40.62500 | 59.90226 | 0.073809 | 63.97 | 25.21 | 38.77 |
| B | 10 | 39.84375 | 59.89194 | 0.123015 | 94.59 | 36.21 | 58.38 |
| B | 8 | 41.40625 | 59.85379 | 0.348481 | 186.43 | 57.95 | 128.48 |
| C | 12 | 33.59375 | 59.86921 | 0.139046 | 67.17 | 23.26 | 43.90 |
| C | 10 | 34.37500 | 59.85643 | 0.219508 | 101.96 | 34.16 | 67.79 |
| C | 8 | 36.71875 | 59.81505 | 0.551154 | 205.94 | 55.99 | 149.95 |

With 10 MW, mean integrated error rises by 61.8% / 66.7% / 57.9% versus
12 MW plus FI in A/B/C. Against the original **12 MW without FI**, the mean
error instead falls by 7.3% / 12.6% / 14.8%, and mean nadirs improve by
10.8 / 20.2 / 19.8 mHz. For integrated error, the paired 95% intervals include
zero in A and B; the small mean improvements there are uncertain, not evidence
of equivalence or a guaranteed improvement. All three nadir improvements have
positive pointwise paired intervals in this sample.

With 8 MW, mean integrated error is 2.84 / 2.48 / 2.14 times the 12 MW
FCR-only value, with lower mean nadirs in all three fleets. The held FI target
is at its availability bound for 66.5% / 66.2% / 69.2% of post-disturbance time
on average. This refers to the target, not instantaneous delivered power.
The plotted delivery has unusually high FI availability (3.90441 MW); its
better nadir than the FCR-only curve must not be generalized to the full bank.

For the **explicitly hypothetical one-event-per-block accounting example**:

| Fleet | Saving: 10 MW+FI vs 12 MW+FI [EUR/block] | Saving: 8 MW+FI vs 12 MW+FI [EUR/block] | Extra cost: 10 MW+FI vs 12 MW without FI [EUR/block] |
|---|---:|---:|---:|
| A | 23.65 ± 2.72 | -20.09 ± 15.34 | 13.51 |
| B | 17.39 ± 4.11 | 5.55 ± 14.54 | 46.59 |
| C | 13.21 ± 4.41 | -10.78 ± 17.20 | 53.96 |

The ± values are pointwise 95% halfwidths for the paired mean saving; negative
saving means higher cost. Thus 10 MW reduces the FI surcharge relative to
12 MW+FI, but does not eliminate it relative to FCR alone when N=1.
The small apparent saving at 8 MW in B is uncertain and accompanies markedly
worse frequency. Event-frequency assumptions remain necessary for a commercial
comparison; this table is not a repeated-event simulation.

A useful separate follow-up would inspect requirements near the 9.592227 MW
merit-order price boundary. The present three levels do not identify an optimal
FCR requirement, and no frequency-security acceptance threshold was specified.

Verification: five focused tests passed, including input isolation, role and
price changes, resume/report without new integrations, matched-sample covariance
and CLI restrictions. The final three procurement tests were rerun after report
changes. All five figures were visually inspected. The 66 original reference
files remain byte-identical. Twelve tighter integration checks have maximum
frequency / power / payment differences of 3.99e-11 Hz / 4.80e-9 MW /
2.48e-8 EUR. All recorded response/active-power bounds and financial identities
pass. There are 3,858 new optimizer integrations plus 96 FCR-only evaluations,
with no failures. Optimization wall time was 1,399.70 s (23.33 min), cumulative
optimizer CPU 2,039.92 s (34.00 min), and baseline CPU 51.30 s. Wall timing
reflects this execution and concurrent verification work, not a machine benchmark.
Report generation took 35.14 s and added zero integrations.

## Controlled comparison

Use the saved A/B/C configurations in `results/fleet_scenarios/` as references.
Reference procurement is 12 MW; new requirements are 10 MW and 8 MW. These
are sensitivity levels chosen to span a merit-order price change, not estimates
of a security-constrained optimal reserve requirement. The physical 10 MW
disturbance is unchanged when FCR procurement changes.

The only changed configuration field within a fleet is
`market.requirement_mw`. Reclear the same offers, then independently optimize
the common gamma with the existing objective
`w_f * E[E_f] + E[S_FI]`, at w_f=10000 EUR/(Hz² s). The same 32 design draws,
32 independent validation draws and third-stream delivery draw are reused
across all policies, with seed 20261019. Validation does not select gamma.
The original gamma search bounds, resolution and numerical tolerances remain.

All individual machine parameters, dispatches, inertia, droop gains, response
times, physical limits, costs, availability distributions and FI eligibility
remain as tabulated in [FLEET_STUDY.md](FLEET_STUDY.md). No new physical or
economic calibration is introduced. A/B jointly change several physical
features; B/C differ only in response times, exactly as in the earlier study.

FCR spending is constant with respect to gamma after clearing. Including it
as an additive term would not change the inner gamma minimizer for a given
requirement. This study does not optimize FCR requirement jointly with gamma.

## Clearing and provider roles

The current offer heuristic depends on prices, costs and availability, but not
on expected acceptance or procurement demand. It therefore produces the same
offers at all three requirements. The merit order is C1, C2, PV1, C3.
PV2 voluntarily offers zero and enters FI through its existing eligibility.

| FCR requirement [MW] | C1 award | C2 award | PV1 award | C3 award | Uniform price [EUR/MW/block] | TSO FCR [EUR/block] |
|---:|---:|---:|---:|---:|---:|---:|
| 12 | 3.945482 | 3.947217 | 1.699528 | 2.407773 | 24 | 288 |
| 10 | 3.945482 | 3.947217 | 1.699528 | 0.407773 | 24 | 240 |
| 8 | 3.945482 | 3.947217 | 0.107301 | 0 | 20 | 160 |

The cheaper offers total 9.592227 MW before C3 enters. Crossing below this
quantity lowers the uniform price from 24 to 20 EUR/MW/block; the 8 MW case
therefore saves both quantity and price. The minimum 1 MW positive *offer*
does not prevent a divisible award below 1 MW.

C3 is FCR-only: a zero award at 8 MW means zero reserve response, not automatic
FI entry. C3 remains online and contributes its unchanged synchronous inertia.
PV2 remains the sole FI provider. Changing the eligibility of other providers
would be a separate experiment. Awards do not retune droop gains.

## Economic comparison with explicit horizons

FCR uses gross capacity payments per full four-hour block, following the
original study's reporting convention; FI is settled over the simulated
40-second event. Provider activation cost is deducted from FI income when
computing provider profit; it is not an additional TSO expenditure.

Report these two spending measures separately. To make the economic question
concrete without assuming an event-occurrence law, also report an accounting
sensitivity with hypothetical N comparable, non-overlapping events per block:

`saving versus 12 MW + FI = FCR_saving - N * (E[S_reduced] - E[S_12])`

`saving versus 12 MW without FI = FCR_saving - N * E[S_reduced]`

The tables include N=1 as an explicitly conditional example and the break-even
N when the denominator is positive. Break-even counts are ratios of estimated
means without confidence intervals. These are not repeated-event simulations:
state recovery, energy feasibility and the number/distribution of events remain
outside the current model. Economic savings alone do not establish acceptable
frequency performance or compliance with a reserve-sizing rule.

## Outputs and reproduction

From the repository root, using the existing runner:

```powershell
.venv/Scripts/python.exe scripts/run_fcr.py --tso-procurement --config configs/fcr_requirement.toml --output-dir results/fcr_requirement
.venv/Scripts/python.exe scripts/run_fcr.py --tso-procurement-report results/fcr_requirement
```

The manifest permits only a saved baseline study, new reduced requirements and
a worker count. Inputs are frozen before simulations. Three baseline cases are
referenced in place; six new policies are optimized. SHA-256 hashes verify the
baseline tree remains unchanged. Completed matching runs can be resumed without
repeating them; incomplete or conflicting output directories are rejected.
Report regeneration performs no dynamic integrations.

For a direct frequency comparison with the original service alone, also
evaluate the predeclared 12 MW, gamma=0 policy on the same 32 validation draws
per fleet (96 extra integrations). These new baseline outcomes are cached only
in the new study. They never select or refine gamma. Existing gamma-zero
delivery trajectories are reused by reference in the frequency figure.

- `ANALYSIS.md`: results and interpretation, including event-count sensitivity.
- `comparison_summary.csv`: validation means/intervals and separate delivery.
- `paired_validation.csv`: candidate minus 12 MW differences with pointwise
  95% Student-t intervals on matched availability samples. Compose J and FI
  profit per sample before calculating uncertainty, preserving covariance.
- `paired_sample_differences.csv`: auditable per-sample differences.
- `economic_sensitivity.csv`: FCR savings, incremental FI and break-even counts.
- `fcr_only_validation_summary.csv`, `paired_vs_fcr_only.csv`: original full-FCR
  reference without FI and paired differences against it.
- `provider_delivery.csv`, `fi_delivery.csv`: every provider's response and
  settlement, FI rewards/charges, activation cost and profit.
- Five figures: frequency, every provider, FI target/response/availability,
  FI finances and separate-unit expected spending/error.
- Per-case directories: complete replay inputs, draws, selected policy,
  trajectories, samples, refined numerical checks and wall/CPU timings.

The common plotted delivery is illustrative, not an expected trajectory.
The frequency plot also references the saved 12 MW gamma-zero delivery.
Validation has only 32 draws: intervals describe mean-estimation uncertainty,
not event guarantees. Its empirical lower nadir percentile is exploratory.
Selected gamma is the best tested candidate, not a certified global optimum.
