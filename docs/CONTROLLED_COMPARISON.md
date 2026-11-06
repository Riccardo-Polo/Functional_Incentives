# Controlled three-mechanism reversal experiment

**Current D-040 economics:** new runs inherit hourly quadratic costs of 50
EUR/(MW² h), converted to rho=1/72, and FPP prices of 12 EUR/(MW h).
The old gamma=50 curve percentages are invalid for this changed FI cost.
The maintained configuration therefore stops after the new design curve;
select gamma with a new reason before continuing the paired comparison.
The completed historical experiment and its gamma selection remain saved in
place. No gamma recalibration is implied by the FPP economic benchmark.

**5 November 2026 (D-039):** new runs inherit the cost-aware
`ideal_frequency_net_profit` preset: explicit synthetic raise/lower prices of
120 EUR/(MW h), FI-style delivered-response internal cost, and full-trajectory
time-weighted performance without four-second measurements. Historical results
under `results/controlled_comparison/` used the coefficient-only objective and
retain their saved interpretation. Their payoff cache is not reused for the
new game; use a new output directory. The (4,4) result below is historical.

Run from the repository root.

```powershell
.\.venv\Scripts\python.exe scripts/run_fcr.py --controlled-study --config configs/controlled_comparison.toml --output-dir results/controlled_comparison_replay
```

The experiment uses the fixed-offer two-renewable preset from
fpp_followers.toml. Conventional awards are 4 MW each and renewable awards are
zero. The physical plant, availability laws and causal follower implementations
come from the existing experiment and simulation modules. The prescribed deficit
is +10 MW from 1 s and -7 MW from 120.05 s to 300 s.

The FI design grid uses common availability draws and the existing FI response
optimizer. The TSO knows the full disturbance trajectory but not the realized
availability. selected_gamma and gamma_reason explicitly document an illustrative
choice by inspecting this curve. An initial configuration without those fields
stops after saving the curve. Adding them permits a restart to run the subsequent
stages. The accepted weighted TSO optimizer remains a separate experiment.

The FPP catalogue is [0,0.5,1,1.5,2,3,4]. The original four-path uniform prior,
design and independent assessment banks are retained. New runs use the net
monetary objective and ideal measurements described above. A completed score
subset from the same objective and measurement profile can be reused after verifying the physical
configuration, numerical tolerances, prior, seed and complete case vectors.
Source hashes are saved. New pairs are integrated using the same coupled model.

Independent streams of the new experiment seed provide the FI design bank,
common final assessment bank, representative delivery, and FPP mixed-gain
randomization. Final paths are the prescribed reversal. Neither the evaluation
bank nor representative availability selects gamma or FPP policies. Symmetric
gains 2 and 3 are assessed on the final bank only as physical diagnostics.

The four main figures cover frequency/RoCoF, individual delivered renewable
responses, FI performance-cost curves and FPP payoff matrices. summary_table.csv
contains representative physical and per-provider financial metrics.
assessment_summary.csv and paired_differences.csv describe independent common
draws. fpp_accounts.csv separates signed raise and lower coefficients including
the residual. fcr_block_financial.csv preserves whole-block accounting.
numerical_checks.csv records tighter integration of the representative policies.
ANALYSIS.md states assumptions and limitations.

FPP coefficients, activation costs and FI monetary transfers retain their own
units. New FPP runs use the explicit synthetic EUR prices above; historical
results were unpriced. Its accounting residual has no assigned real
funding participant. Frequency dynamics are aggregate after ANDES initialization.
All runs use serial numerical evaluation.
