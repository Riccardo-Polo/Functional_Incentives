# Mixed-fleet comparison with parameters inspired by technical sources

Protocol dated 16 October 2026, D-026; study completed and verified on
17 October 2026. Reproducible configuration: `configs/fleet_scenarios.toml`;
Git-ignored results: `results/fleet_scenarios/`. The historical preset in
`configs/fcr.toml` is unchanged. This document and all project-authored outputs
are maintained in English (D-027).

## Research question and comparison

How do frequency, the selected γ and FI finances change with the machine fleet?
All three configurations contain three synchronous thermal generators and two
PV plants. Each case uses 32 design draws, 32 independent validation draws and
one subsequent illustrative delivery event.

| Quantity | A: conventional-heavy fleet | B: higher photovoltaic share | C: higher PV share, slower responses |
|---|---:|---:|---:|
| Total synchronous rating [MVA] | 1100 | 800 | 800 |
| Total fleet rating [MVA] | 1420 | 1440 | 1440 |
| Total installed active-power capacity [MW] | 1290 | 1320 | 1320 |
| Σ H_i S_i [MW s] | 5600 | 3900 | 3900 |
| Equivalent synchronous inertia H_eq [s] | 5.091 | 4.875 | 4.875 |
| PV share of solved generation | 17.98% | 47.94% | 47.94% |
| Selected common γ [EUR/(Hz MW s)] | 33.59375 | 40.625 | 33.59375 |

The network remains PJM, with nominal frequency 60 Hz and load 1000 MW.
A→B jointly changes mix, dispatch, inertia, droop gains and PV response times;
it does not isolate renewable share alone. B→C changes only the five response
time constants, isolating provider response speed.

## Individual machine parameters

C1/C2/C3 are `conventional_1/2/3`; PV1 is `renewable_reliable`; PV2 is
`renewable_uncertain`. C1, C2, C3 and PV1 participate in FCR; PV2 participates
in FI. The role remains fixed throughout each event.

S is the machine/inverter rating, P_min and P_max are explicit active-power
limits, P_0 is the ANDES-solved operating point, H is synchronous inertia and
T is the first-order response time. P(t)=P_0+u(t). C3 is the slack machine:
its configured setpoint is an initial guess, while the solved P_0 includes
network losses. Displayed values are rounded; the configuration and
`results/fleet_scenarios/machine_parameters.csv` retain full precision.

### A — Conventional-heavy fleet (`thermal_heavy`)

| Machine | Role | S [MVA] | P_min [MW] | P_max [MW] | Configured P_0 [MW] | Solved P_0 [MW] | H [s] | T [s] | FCR K [MW/Hz] | Symmetric headroom [MW] |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C1 | FCR | 400 | 144 | 360 | 280 | 280.000 | 6 | 3.4 | 133.333 | 80.000 |
| C2 | FCR | 400 | 144 | 360 | 280 | 280.000 | 5 | 4.8 | 133.333 | 80.000 |
| C3 | FCR | 300 | 108 | 270 | 260 | 261.025 | 4 | 6.2 | 100.000 | 8.975 |
| PV1 | FCR | 160 | 0 | 150 | 90 | 90.000 | 0 | 1.6 | 53.333 | 60.000 |
| PV2 | FI | 160 | 0 | 150 | 90 | 90.000 | 0 | 2.0 | — | 60.000 |

### B — Higher photovoltaic share (`solar_heavy`)

| Machine | Role | S [MVA] | P_min [MW] | P_max [MW] | Configured P_0 [MW] | Solved P_0 [MW] | H [s] | T [s] | FCR K [MW/Hz] | Symmetric headroom [MW] |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C1 | FCR | 200 | 72 | 180 | 140 | 140.000 | 6 | 3.4 | 66.667 | 40.000 |
| C2 | FCR | 300 | 108 | 270 | 190 | 190.000 | 5 | 4.8 | 100.000 | 80.000 |
| C3 | FCR | 300 | 108 | 270 | 190 | 191.302 | 4 | 6.2 | 100.000 | 78.698 |
| PV1 | FCR | 320 | 0 | 300 | 240 | 240.000 | 0 | 0.8 | 106.667 | 60.000 |
| PV2 | FI | 320 | 0 | 300 | 240 | 240.000 | 0 | 1.0 | — | 60.000 |

### C — Higher PV share, slower responses (`solar_slow`)

| Machine | Role | S [MVA] | P_min [MW] | P_max [MW] | Configured P_0 [MW] | Solved P_0 [MW] | H [s] | T [s] | FCR K [MW/Hz] | Symmetric headroom [MW] |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C1 | FCR | 200 | 72 | 180 | 140 | 140.000 | 6 | 5.5 | 66.667 | 40.000 |
| C2 | FCR | 300 | 108 | 270 | 190 | 190.000 | 5 | 6.9 | 100.000 | 80.000 |
| C3 | FCR | 300 | 108 | 270 | 190 | 191.302 | 4 | 8.3 | 100.000 | 78.698 |
| PV1 | FCR | 320 | 0 | 300 | 240 | 240.000 | 0 | 1.6 | 106.667 | 60.000 |
| PV2 | FI | 320 | 0 | 300 | 240 | 240.000 | 0 | 2.0 | — | 60.000 |

All providers have configured droop R=0.05, with K=P_B/(R f_nominal) and the
numerical MVA rating used as the MW per-unit base. This convention does not
set active-power limits. **PV2 uses the FI controller driven by γ and ρ, not
FCR droop K.** Its configuration-derived K (53.333 in A; 106.667 in B/C)
appears in the raw CSV for completeness but does not govern its FI response.
PV units contribute no synchronous or synthetic inertia in this model.

### Common service availability and market inputs

The symmetric service capacity is **4 MW per provider**, within each machine's
physical headroom. It is a dedicated modulation envelope, not the full plant
rating or all available headroom. Availability follows a Gaussian truncated
to [0,4] MW; μ and variance below describe the underlying, unbounded Gaussian.
These are distributions of service availability around a fixed P_0, not total
solar production. The same availability banks and delivery draw are reused
across A/B/C.

| Machine | μ [MW] | Variance [MW²] | Delivery A [MW] | FCR award [MW] | Cost a [EUR/MW/block] | Cost β [EUR/MW²/block] | Bid interval [EUR/MW/block] |
|---|---:|---:|---:|---:|---:|---:|---|
| C1 | 3.98 | 0.0004 | 3.982392 | 3.945482 | 3 | 0.5 | 10–15 |
| C2 | 3.98 | 0.0004 | 3.979929 | 3.947217 | 4 | 0.5 | 12–18 |
| C3 | 3.98 | 0.0004 | 3.969261 | 2.407773 | 5 | 0.5 | 16–24 |
| PV1 | 2.00 | 0.04 | 1.824972 | 1.699528 | 5 | 1.0 | 10–20 |
| PV2 | 2.00 | 4.00 | 3.904413 | 0.000000 | 5 | 1.0 | 10–20 |

FCR reserve cost is C(q)=a q+0.5 β q²; the shortage penalty is 200 EUR per
missing MW per block for every provider. Minimum positive offer: 1 MW;
bidding safety margin: zero; FCR requirement: 12 MW; block length: 4 hours.
Uniform clearing price: 24 EUR/MW/block; total FCR capacity payment: 288 EUR.
PV2 voluntarily offers zero in FCR and enters FI. Its a and β describe the
FCR offer calculation, not its actual FI activation cost. FI uses
ρ=1 EUR/(MW² s), cost rate ρ z²/2, and decision interval 0.1 s.
The economic coefficients and availability laws remain **pedagogical
assumptions**, held equal to make the physical comparison interpretable.
They are not inferred from auction prices or calibrated to actual plants.

## Parameter provenance and transformations

The numbers are **inspired by sources**, not a calibration of identified
plants. Unobserved quantities are explicitly labelled as assumptions.

### Thermal ratings and active-power limits

NERC, *Application Guide for Modeling Turbine-Governor and Active
Power-Frequency Controls in Interconnection-Wide Stability Studies*, June
2019, Table B.1, p. 50, uses a **200 MVA, P_max=180 MW** example. We explicitly
scale that pair to (200,180), (300,270) and (400,360). It is an illustrative
technical reference, not a measurement of a real power plant. The guide warns
against confusing the MVA base with the active-power limit; P_max therefore
remains a separate input and is never inferred automatically.

[NERC source](https://www.nerc.com/globalassets/who-we-are/standing-committees/rstc/ppmvtf/reliability_guideline-application_guide_for_turbine-governor_modeling.pdf),
accessed 16 October 2026.

P_min=40% of P_max and the dispatches are **scenario assumptions**, not NERC
data. ANDES solves each operating point; min(P_max−P_0, P_0−P_min)≥4 MW is
checked individually. This checks active-power headroom in the aggregate
model; it does not certify plant P-Q limits, voltages or multimachine stability.

### Inertia

NREL, *Inertia and the Power Grid: A Guide Without the Spin*, May 2020,
NREL/TP-6A20-73856, box on p. 11, gives a typical range of **2–7 s**.
H=[6,5,4] s are **choices within that range**, not identified values for these
machines. Only thermal machines contribute to ΣHS.

[NREL report](https://www.nrel.gov/docs/fy20osti/73856.pdf), consulted through
an indexed excerpt from the primary source on 16 October 2026.

### Thermal response times: explicit reduction

The local `references/PSDCO_Script_2026 (1).pdf`, printed pp. 88–89
(PDF pp. 96–97), describes a single-reheat steam turbine:

G(s) = [F_HP + F_IP/(1+s T_RH) + F_LP/((1+s T_RH)(1+s T_CO))] / (1+s T_CH).

Reported ranges: T_CH=0.1–0.4 s, T_RH=4–11 s, T_CO=0.3–0.6 s.
We set T_CH=0.2 and T_CO=0.5 s. F_HP/F_IP/F_LP=0.3/0.3/0.4 and governor
constant T_g=0.2 s are **explicit assumptions**. The low-frequency,
first-order approximation gives:

T_eq = T_g + T_CH + (1−F_HP) T_RH + F_LP T_CO = 0.6 + 0.7 T_RH.

T_RH=[4,6,8] gives T=[3.4,4.8,6.2] s in A/B;
T_RH=[7,9,11] gives T=[5.5,6.9,8.3] s in C. The implemented provider equation
is unchanged; only its T is selected. This reduction does not reproduce the
fast HP component or all real turbine transients; model nadirs are not
certified plant predictions.

### Photovoltaics: real rating, approximate response

CAISO/NREL/First Solar, *Using Renewables to Operate a Low-Carbon Grid*, 2017,
reports tests on a **300 MW** plant with **80 inverters rated 4 MVA** each
(p. 24), totalling 320 MVA. We use that pair in B/C and half its size in A.
The droop tests use a ramp limit of **600 MW/min = 10 MW/s** (p. 33).
These are historical references for that plant, not universal requirements
or current market rules.

[CAISO report](https://www.caiso.com/Documents/UsingRenewablesToOperateLow-CarbonGrid.pdf),
accessed 16 October 2026.

For the first-order model, |v|,|z|≤A_max implies |dz/dt|≤2A_max/T.
With A_max=4 MW, choose T≥8/r, assuming r scales with active-power rating:
5 MW/s in A and 10 MW/s in B. This gives T_PV1=1.6/0.8 s; PV2 uses a
conservative factor of 1.25, giving 2/1 s. C doubles both B time constants.
This is a **control derivation/assumption**, not identification of T from data.
No ramp limiter is added: the bound follows analytically for the chosen T
and service envelope. The existing 5% droop on the numerical MVA base differs
from droop on the 300 MW active-power base used in the CAISO tests.

### Role of the Nordic32 thesis

`references/Nordic32_MasterThesis.pdf`, Sharon Müller, 2019, Table 2.7,
printed p. 16 / PDF p. 27, distinguishes capacity by technology; some
generators aggregate hydro, CHP, nuclear and wind. We do not copy these as
fully synchronous machines. The thesis motivates coherent mixes and dispatch,
but its hourly power-flow validation does not identify our subsecond/second
response constants. This study neither reconstructs Nordic32 nor implicitly
switches to 50 Hz. The US PV tests are consistent with our 60 Hz setting.

## Computation and reproducibility

All cases use a 10 MW deficit at t=1 s, a 40 s horizon and no secondary control.
The objective is min_γ 10000 E[E_f]+E[S_FI], with search domain [0,300],
7 initial candidates and local resolution 1. Each configuration uses 32 draws
to select γ, 32 independent draws to assess it, and a subsequent event for the
figures. Seed 20261019, streams 0/1/2. Draws are identical across configurations;
there is no retrospective seed selection. Validation does not select γ.
Each case has two checks with finer integration and a γ=0 reference for the
same delivery draw. Mean statistics and single-event figures remain distinct.
Solver/output steps are 0.05 s, rtol=1e-11 and atol=1e-13.

```powershell
.venv/Scripts/python.exe scripts/run_fcr.py --tso-fleet --config configs/fleet_scenarios.toml --output-dir results/fleet_scenarios
.venv/Scripts/python.exe scripts/run_fcr.py --tso-fleet-report results/fleet_scenarios
```

The second command regenerates only the report from saved files. The first also
reuses completed cases when the resolved plan matches; it rejects incompatible
output directories or incomplete cases without overwriting them. Three worker
processes use the ordinary runner's solver and model. Timings and counts are saved.

FCR expenditure per block and FI expenditure per event are reported separately.
An event-occurrence model is needed to estimate FI spending over four hours.
FI profit equals net payment minus activation cost; provider costs must not be
added again to TSO spending. Results and statistical limits are reported in
`results/fleet_scenarios/ANALYSIS.md`.

## Results

On 32 independent validation draws, selected γ in A/B/C is
33.59375 / 40.625 / 33.59375. Mean nadirs are
59.91528 / 59.90226 / 59.86921 Hz; mean FI spending is
37.16 / 63.97 / 67.17 EUR/event. B→C mainly worsens frequency, while mean FI
spending increases by about 3.19 EUR in this sample. The same γ in A and C
does not imply the same payment or response.

In the illustrated event, the FI provider has 3.90441 MW available; delivered
power peaks are 2.394 / 3.101 / 3.376 MW. Its target reaches the bound in
1.8% / 0.5% / 10.3% of post-disturbance intervals. The held target and delivered
power are distinct, with a lag governed by T.

FCR spending is 288 EUR/block in every case. Illustrative-event FI spending is
44.56 / 77.14 / 84.21 EUR; FI profit is 22.29 / 38.57 / 44.16 EUR. Full provider
data and validation means are in the report and CSV files; single-delivery
values must not substitute for expectations.

Verification: seven targeted tests, six finer-integration comparisons, checks
of all provider limits and financial identities. No failures in 1932
integrations. Optimization took 13.58 minutes wall time with three processes
and 21.17 minutes cumulative CPU time. The report can be regenerated without
repeating optimization or previously saved reference simulations.
