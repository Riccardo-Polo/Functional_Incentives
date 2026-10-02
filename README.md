# Functional Incentives for Frequency Control

Semester Project — ETH Zürich, Automatic Control Laboratory.

The project studies how payments can encourage flexible resources to help keep
electricity supply and demand balanced. The long-term comparison is between
contracted frequency reserves and additional response encouraged by functional
incentives: payments that depend on the system state and the provider's action.

## Current implementation

The teaching example uses the ANDES PJM 5-bus case to establish initial powers
and model parameters. It then represents the system with one shared frequency
and four individual provider responses. A permanent 10 MW shortage starts at
1 s, and the experiment runs to 40 s on the case's native 60 Hz base.

The numerical FCR example is implemented. The reserve market
and providers' economic decisions are not implemented yet.

## What the equations mean

Write frequency relative to its nominal value as $\Delta f=f-f_N$. A positive
deficit $d$ lowers frequency; a positive provider response $u_i$ adds power:

$$
M_f\frac{d\Delta f}{dt}=-D\Delta f-d+\sum_i u_i,
\qquad
M_f=\frac{2H_{\mathrm{eq}}S_{\mathrm{sync}}}{f_N}.
$$

$M_f$ describes resistance to a rapid frequency change. $D$ describes how load
changes with frequency. Power is in MW, frequency in Hz, and time in seconds;
$S_{\mathrm{sync}}$ is the synchronous machine rating in MVA.

Each provider requests a response proportional to the frequency error, with a
reserve limit, and gradually moves towards that request:

$$
r_i=\text{clip}(-K_i\Delta f,-q_i^\star,q_i^\star),
\qquad
T_i\frac{du_i}{dt}=r_i-u_i.
$$

- $K_i$ [MW/Hz] controls how strongly the provider responds to a frequency error.
- $T_i$ [s] controls how quickly its actual response approaches the request.
- $q_i^\star$ [MW] limits the request in either direction.

The response is a change around the initial power: $P_i(t)=P_{i,0}+u_i(t)$.
The current reserve limits are manually assigned teaching values. They have
not been checked against sourced generator minimum and maximum power limits.
See the [model and code guide](docs/FCR_MODEL.md) for a worked explanation.

## Run the example

Python 3.11 or newer is required; the current local environment uses 3.12.
From the repository root, on Windows PowerShell:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe scripts\run_pjm5_fcr_teaching.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

The experiment reads [pjm5_fcr_teaching.toml](configs/experiments/pjm5_fcr_teaching.toml).
Its four provider parameter sets are scenario assumptions:

| Provider | $K_i$ [MW/Hz] | $T_i$ [s] | $q_i^\star$ [MW] |
|---|---:|---:|---:|
| 1 | 10 | 0.4 | 1 |
| 2 | 15 | 0.8 | 2 |
| 3 | 20 | 1.5 | 3 |
| 4 | 25 | 3.0 | 4 |

The script saves `time_series.csv`, `frequency_comparison.png`, and
`provider_responses.png` in `results/pjm5_fcr_teaching/`. The CSV includes
requested response, actual response, and total power for every provider.
Use `--output-dir` to preserve separate experiment results; the default paths
are reused on the next run. `--config` selects another TOML configuration.

## Verified default results

| Quantity | Result |
|---|---:|
| Frequency without FCR at 40 s | 59.400035 Hz |
| Lowest sampled frequency with FCR | 59.827191 Hz |
| Frequency with FCR at 40 s | 59.882609 Hz |
| Largest sampled numerical/analytical no-FCR difference | $1.50\times10^{-14}$ Hz |
| Largest frequency difference after refining the solver | $4.89\times10^{-9}$ Hz |

All four sampled responses respect their reserve limits within numerical
tolerance. Frequency settles below 60 Hz because this proportional primary
response needs a remaining frequency error to sustain its added power.
Restoring exactly 60 Hz would require a later secondary-control model.

## Reading the code

| Read | File | Role |
|---|---|---|
| 1 | [providers/fcr.py](src/functional_incentives/providers/fcr.py) | One provider's parameters, request and rate of response |
| 2 | [grid/aggregate_frequency.py](src/functional_incentives/grid/aggregate_frequency.py) | Frequency equation and analytical constant-step reference |
| 3 | [grid/andes_adapter.py](src/functional_incentives/grid/andes_adapter.py) | Solved initial powers and case data |
| 4 | [simulation/aggregate_fcr.py](src/functional_incentives/simulation/aggregate_fcr.py) | Numerical evolution of frequency and provider states together |
| 5 | [run_pjm5_fcr_teaching.py](scripts/run_pjm5_fcr_teaching.py) | Experiment, comparisons, checks and saved outputs |
| 6 | [plotting/fcr.py](src/functional_incentives/plotting/fcr.py) | Frequency and individual-response figures |
| 7 | [tests](tests) | Analytical comparisons, signs, bounds and numerical convergence |

## Next development stage

The proposed next stage is the simplified Week 3 FCR capacity market:

$$
\min_{\{q_i\}}\sum_i c_iq_i
\quad\text{subject to}\quad
\sum_i q_i\ge Q_{\mathrm{req}},\qquad 0\le q_i\le\bar q_i.
$$

A provider offers capacity $\bar q_i$ at bid price $c_i$. The auction chooses
awards $q_i^\star$; an explicit pricing rule determines $\lambda^\star$.
Awards then become the reserve limits used by the existing dynamic model.

The supervisor's suggested extension is to explain how each provider chooses
its bid. This requires a cost and decision model: the slides specify $c_i$ as
a bid, but do not derive it. The [market and provider-decision plan](docs/FCR_MARKET_PLAN.md)
sets out the equations, proposed implementation order, and choices still open.

## Research context and sources

The main local sources are `references/week3_eth.pdf` (29 September 2026) and
`references/PSDCO_Script_2026 (1).pdf`. PDF page references are listed in the
guides. These research files and `local_context/` are intentionally excluded
from Git. `local_context/NOW.md`, `DECISIONS.md`, and `PROJECT_HANDOFF.md`, when
present, provide current status, accepted decisions, and historical context.
ANDES IEEE 14-bus remains the planned later validation case for more detailed
network dynamics.
