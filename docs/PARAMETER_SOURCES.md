# Parameter evidence and calibration register

Updated 2026-10-16. The original numerical preset remains pedagogical.
The separate [fleet study](FLEET_STUDY.md) now adopts source-inspired ratings,
inertia ranges and explicitly reduced response times. Its register records
the NERC/NREL/CAISO and local PSDCO sources, transformations and assumptions.
This is not plant identification or market calibration; baseline values below
continue to refer to the original preset.

## Evidence rules

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
