# Parameter evidence and calibration register

**FCR capacity sensitivity, 16 November 2026:** the user authorized a separate
synthetic scenario with conventional reserve capacity 8 MW (previously 4) and
underlying Gaussian mean 7.98 MW (previously 3.98). Variance, costs, dynamics,
operating point and renewable laws stay unchanged. This is not external
calibration. Each support is checked against both solved active-power margins.
Within each original/expanded fleet only FCR demand changes; no gain retuning.
See [FCR_CAPACITY_STUDY.md](FCR_CAPACITY_STUDY.md).

Updated 2026-10-16. The original numerical preset remains pedagogical.
The separate [fleet study](FLEET_STUDY.md) now adopts source-inspired ratings,
inertia ranges and explicitly reduced response times. Its register records
the NERC/NREL/CAISO and local PSDCO sources, transformations and assumptions.
This is not plant identification or market calibration; baseline values below
continue to refer to the original preset.

## Evidence rules

**L2 availability sensitivity, 15 November 2026:** explicit user request for
less uncertainty while retaining zero FCR participation. The separate manifest
`configs/lyapunov_lower_uncertainty.toml` uses variance 1.75 MW^2 instead of
4 for `renewable_uncertain`, with underlying mean 2 MW and truncated support
[0,4] MW unchanged. This is a scenario assumption, not a fitted forecast law.
Actual bounded standard deviations are 1.079120 / 0.987767 MW. Selection used
the unchanged offer heuristic: the best positive offer has reward -1.310074
EUR/block at 1.75; the zero-reward boundary at the 1 MW minimum is about
1.485591 MW^2. Keep the existing costs, 200 EUR/MW shortage penalty, price
ceiling 20 EUR/MW/block and minimum bid 1 MW. No visual confidence setting is
changed: the same n=4 and pointwise 95% Student-t formula remain. The reference
study and original preset stay unchanged. See docs/LYAPUNOV_STUDY.md.

**Current FPP economic benchmark, 6 November 2026 (D-040):** the user requested
interpretable monetary scales. Both players declare hourly quadratic activation
cost k=50 EUR/(MW² h); the loader converts rho=k/3600=1/72 EUR/(MW² s).
This means marginal 50 EUR/MWh at 1 MW, and 2.083333 EUR for a constant 1 MW
over 300 s. This is a scenario resource-cost proxy, not measured renewable
cost or a linear energy opportunity-cost model. Symmetric FPP prices are
12 EUR/(MW h), anchored illustratively at twice the preset FCR hourly scale
24/4=6. The factor-two premium is an explicit assumption, not an AEMO rule
or evidence that these products have identical prices. No claim of market
calibration is made. Sensitivity uses prices 6/12/24 and hourly costs
25/50/100. The baseline/Week-5 rho remains unchanged in configs/fcr.toml.

**FPP update, 5 November 2026 (D-039):** the ideal net-profit game uses synthetic
raise/lower prices of 120 EUR/(MW h) each, selected with user authorization
as illustrative scales, not from an observed dataset. Both players reuse the
FI cost rho=1 EUR/(MW² s). `configs/fpp_followers.toml` records these scenario
assumptions and price provenance. There is no implicit AUD/EUR conversion.

For each proposed value record: parameter/provider, current value, source title
and version/date, page/table/equation or dataset column, geography/technology,
original units and power/time base, transformation to model units, proposed
value or range, uncertainty and applicability limits. Record access date for
online data and retain the input snapshot when permitted.

Use explicit evidence labels: **source value**, **derived from source**,
**estimated/fit**, **scenario assumption**, or **missing evidence**. An
operationally plausible number is not automatically calibrated. Confirm the
relevant historical product version, or verify official current sources if
claiming present-day rules; the local PDFs alone do not establish current rules.

## Local source inventory

| Source | Verified locator / scope | Intended use and limitation |
|---|---|---|
| `references/Nordic32_MasterThesis.pdf`, Sharon Müller, *Development of Nordic 32 system model and performance analysis based on real operation statistics*, 2019 | Section 2.3.2; Table 2.7, printed p. 16 / PDF p. 27; Sections 2.4.1–2.4.2, printed pp. 17–19 / PDF pp. 28–30; Eq. (2.1) | Ratings, generation mix and reconstruction of operating scenarios from 2018 statistics. Table 2.7 aggregates multiple technologies at some generators. The hourly quasi-dynamic validation is a sequence of steady-state load flows; it does not itself validate our subsecond frequency response or FCR economics. |
| `references/PSDCO_Script_2026 (1).pdf`, Chapters 8–9 | Existing physical-model reference in MODEL.md | Frequency/inertia/droop conventions; identify technology-specific dynamic values and exact locators before adopting numerical parameters. |
| `references/D241101-AS-Products-V20-en.pdf`, Swissgrid, *Principles of ancillary services products* | Cover: version 20, product description valid from 01.11.2024 | Candidate historical product definition for FCR requirements, units and procurement rules. Audit the relevant sections and compatibility with the simplified market before adopting values. This document alone is not an observed bid-price or private-cost dataset. |
| `references/balancing-roadmap-en.pdf`, *Balancing Roadmap Switzerland 2026–2030* | Cover and stated planning horizon | Market-development context; use the applicable product rules and settlement/results data to calibrate quantities and prices. |
| Local AEMO FPP procedure and FAQ PDFs | Existing Australian frequency-performance references | Comparative incentive context. Do not silently transfer currency, products or settlement rules to the present EUR/FCR/FI experiment. |

The Nordic thesis is a useful starting point, not a complete parameter source.
For example, Table 2.7 gives both MVA rating and installed MW by technology;
neither alone specifies dispatched MW, symmetric reserve headroom, inertia,
governor response, private reserve costs or FI activation costs. An aggregate
containing wind must not be copied as a wholly synchronous inertial machine.
Its underlying operational spreadsheets are mentioned in the thesis; their
availability and contents still need to be established before using time series.

## Initial parameter register

Values below are the existing Week 5 scenario, not proposed replacements.
Provider order is C1/C2/C3, reliable renewable, uncertain renewable. The
resolved configuration remains the source of exact per-provider inputs.

| Parameter family | Current baseline / units | Required next evidence or decision |
|---|---|---|
| System and nominal frequency | PJM 5-bus, 1000 MW load, 60 Hz | Choose a source-informed reduced scenario and document scaling. A European/Nordic frequency base is an explicit later change, not an implicit consequence of reading the thesis. |
| Ratings and technology | [300,400,400,150,150] MVA; three conventional, two converters | Technology-consistent source ratings; separate actual machines from aggregated resources in Nordic Table 2.7. |
| Dispatch and limits | Non-slack setpoints [260,310,90,90] MW; slack solved by ANDES; explicit P_min/P_max | Source operating scenarios and active limits. Re-solve dispatch; verify both upward and downward margins. Never derive P_max silently from MVA. |
| Physical reserve capability | 4 MW per provider | Available modulation/headroom by technology and operating point; document any reduction to a comparable test-system scale. |
| Inertia | [5,4,4,0,0] s; only conventional units synchronous | Dynamic-model or technical source with the same machine bases. Do not infer synchronous inertia for wind merely because it is aggregated into a source generator. |
| Droop and load damping | 5% size-normalized droop; load sensitivity 1 | Applicable characteristic and bases; justify the reduced load-frequency coefficient. Awards and availability never retune K automatically. |
| Response time | [2,3,4,0.25,0.25] s | Technology-specific dynamics or a justified first-order fit. A product's full-activation deadline is not directly the first-order time constant. |
| Disturbance and horizon | 10 MW deficit at 1 s; 40 s event | System-relative credible disturbance and observation horizon. Event occurrence over a market block is a separate assumption. |
| Availability | Underlying means 3.98 MW for conventional, 2 MW for renewables; variances 0.0004, 0.04 and 4 MW²; truncated to [0,4] MW | Forecast-error / deliverable-reserve evidence at the intended horizon, plus mapping to modulation around dispatch. Generation variability is not automatically reserve availability; distinguish underlying and bounded moments. |
| FCR product and volume | Symmetric, 12 MW required, 1 MW minimum offer, 4-hour block | Choose geography, product and date; document system-scale translation, minimum offers versus divisible awards, and block structure. Preserve the original participation example and its 1 MW minimum. |
| Prices and shortage penalty | Configured price intervals; uniform clearing; penalty 200 EUR/missing MW/block | Official auction results and applicable shortfall rules. A different real penalty/pricing formula requires a separate mechanism decision, not an unexplained coefficient substitution. |
| Private FCR costs | a = [3,4,5,5,5] EUR/MW/block; beta = [0.5,0.5,0.5,1,1] EUR/MW²/block | Opportunity-cost/technology data or declared sensitivity ranges. Clearing prices alone do not identify private cost curves. |
| FI coefficients | rho = 1 EUR/(MW² s); gamma 50/200 EUR/(Hz MW s) in reported cases | Economic interpretation/data for delivered-power cost; otherwise a declared range. Gamma is a contract/design variable, later selected through the TSO objective, not an observed FCR price. |
| Decision interval | 0.1 s | Controller information/update assumption. Keep separate from solver and output steps; document any data-based motivation. |

## Conversion and adoption checks

- Distinguish MW from MVA, inertia seconds from a second-order machine
  parameter, Hz from per unit, and variance from standard deviation.
- Keep technology mix, dispatch, headroom, droop base and synchronous inertia
  consistent with one declared system scale. Avoid copying a large aggregate
  Nordic generator into the existing 1000 MW PJM case without a mapping.
- Convert a capacity price quoted per MW/hour to a whole-block coefficient
  exactly once. An energy price in EUR/MWh requires an explicit energy model;
  it is not interchangeable with an FCR capacity price or quadratic FI cost.
- Preserve the units in MODEL.md. FCR costs/payments are per whole block;
  FI rates are integrated over seconds of the simulated event. Changing the
  configured block duration alone does not rescale existing coefficients.
- Validate physical inputs first with economics held fixed; then change the
  economic/availability assumptions and rerun the diagnostics and saturation
  study. Keep the original scenario reproducible through the same runner.
- Record unresolved data gaps as such. Do not adjust costs, availability or
  gamma solely to recover the earlier voluntary opt-out or 2:1 observation.

## November meeting disturbance rise time

The 0.5 s rise time in `configs/fi_budget.toml` is an illustrative response
to the 18 November request for gradual disturbance onsets and reversals.
It describes the applied net power imbalance. Device-specific sensor bandwidth
and a measured disturbance trace would require separate data. Jonas Matt's
17 July 2026 notes, Section 3.1.2, Eq. (22), supply the scalar input reference.
