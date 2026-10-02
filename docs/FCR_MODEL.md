# Understanding the aggregate FCR example

Reviewed on 2026-10-02. This guide describes the implemented teaching scenario.
The [review](FCR_REVIEW_2026-10-02.md) records the sampling bugs found outside
the default configuration and their fixes; the [market plan](FCR_MARKET_PLAN.md)
describes future work.

## 1. Initial powers and changes around them

ANDES first solves a power flow: it calculates the initial power supplied by
each generator and the power transmitted through the network. Total generation
is about 1003.939 MW, serving 1000 MW of load and about 3.939 MW of losses.

The dynamic experiment starts from that balanced operating point. Its variables
describe changes around it:

$$
\Delta f(t)=f(t)-f_N,\qquad P_i(t)=P_{i,0}+u_i(t).
$$

For example, a generator initially at 210 MW delivers 211 MW when its response
$u_i$ is 1 MW. Zero response means it is still at its initial power.

The numerical simulator receives the physical parameters through its caller.
It does not run ANDES again during the event. $P_{i,0}$ is used to report total
power; it does not appear in the equations for deviations. Changes in network
losses and line flows during the event are outside this aggregate model.

## 2. One frequency, four individual responses

The approximation is that the connected generators move sufficiently together
to describe their frequency with one equivalent state. It does not track
separate rotor angles or local frequencies, or verify that this approximation
holds for every disturbance.

The state is

$$
x(t)=\begin{bmatrix}\Delta f(t)&u_1(t)&u_2(t)&u_3(t)&u_4(t)\end{bmatrix}^{\mathsf T}.
$$

The frequency equation is

$$
M_f\dot{\Delta f}=-D\Delta f-d(t)+\sum_{i=1}^4u_i,
\qquad
M_f=\frac{2H_{\mathrm{eq}}S_{\mathrm{sync}}}{f_N}.
$$

| Quantity | Meaning | Unit |
|---|---|---|
| $d>0$ | Increased load or lost generation | MW |
| $u_i>0$ | Added power from provider $i$ | MW |
| $H_{\mathrm{eq}}$ | Equivalent inertia constant | s |
| $S_{\mathrm{sync}}$ | Total synchronous machine rating | MVA |
| $M_f$ | Coefficient resisting changes in frequency | MW s/Hz |
| $D$ | Change in load per unit of frequency change | MW/Hz |

A negative $\Delta f$ gives a positive $-D\Delta f$: frequency-sensitive load
falls, which partly relieves the shortage. This term is not a provider payment
or contracted response.

The adapter obtains $H_i=M_i/2$ from original ANDES `GENCLS` input data and
forms $H_{\mathrm{eq}}=\sum_i H_iS_i/\sum_i S_i$. Here,

$$
f_N=60\ \mathrm{Hz},\quad H_{\mathrm{eq}}=2\ \mathrm{s},\quad
S_{\mathrm{sync}}=1000\ \mathrm{MVA},\quad M_f=66.6667\ \mathrm{MW\,s/Hz}.
$$

Damping is an explicit assumption: a 1% frequency change gives a 1% load change,
so $D=1000/60=16.6667$ MW/Hz. A solved power flow does not identify that sensitivity.
Week 3 also presents rotating-load inertia $W_0$ in $M=2(HS_B+W_0)/f_N$;
the implemented D-009 baseline includes synchronous machine inertia only and
does not add a separately modelled $W_0$ term.

## 3. Requested and actual provider response

For each provider define the requested response $r_i$:

$$
r_i(t)=\operatorname{clip}(-K_i\Delta f(t),-q_i^\star,q_i^\star),
\qquad
\dot u_i(t)=\frac{r_i(t)-u_i(t)}{T_i}.
$$

`clip` limits a value to the interval between its last two arguments.
$K_i$ sets the response requested per Hz of frequency error; $T_i$ determines
how quickly the delivered response follows it. For a constant request, after
one $T_i$ about 63% of the initial gap to that request has closed.

For provider 1, $K_1=10$ MW/Hz, $T_1=0.4$ s and $q_1^\star=1$ MW. If
$\Delta f=-0.1$ Hz, the request is 1 MW. If $u_1=0.2$ MW at that instant,
$\dot u_1=(1-0.2)/0.4=2$ MW/s. This is a rate of change, not an immediate
2 MW jump in power.

For the exact continuous equations, starting within $[-q_i^\star,q_i^\star]$
keeps the response within that interval: at the upper boundary its derivative
is nonpositive, and at the lower boundary its derivative is nonnegative.
The simulator does not clip the computed state after integration. The runner
checks sampled responses with a numerical tolerance of $10^{-8}$ MW.

The current $q_i^\star$ are manual assumptions. Physical feasibility would also
require, for a symmetric reserve around a fixed initial power,

$$
q_i^\star\le P_{i,\max}-P_{i,0},\qquad
q_i^\star\le P_{i,0}-P_{i,\min}.
$$

These active-power limits have not been sourced. Machine MVA ratings cannot
silently replace them; storage or uncertain providers would need further
energy and availability constraints.

## 4. How the simulator joins the equations

`grid/aggregate_frequency.py` supplies the frequency derivative.
`providers/fcr.py` supplies each provider derivative. `simulation/aggregate_fcr.py`
assembles them into $\dot x=g(x)$ and passes that function to SciPy `solve_ivp`.
The models remain separately usable.

At the event at 1 s, $x=0$ but the deficit immediately becomes 10 MW. Initially
$\dot{\Delta f}=-10/M_f=-0.15$ Hz/s and $\dot u_i=0$. Frequency then falls,
positive requests develop, and the providers gradually add power.

RK45 evaluates the derivatives repeatedly at intermediate states and chooses
internal steps using error estimates. The output interval is 0.05 s; by
default it also caps the internal step, but internal steps can be smaller.
The solver tolerances are `rtol=1e-9` and `atol=1e-11`.

The pre-event equilibrium is recorded directly as zeros. Integration starts
at the event and continues with a constant deficit. The present API supports
one permanent nonnegative step, not an arbitrary disturbance function.

Both simulators use `time_grid.make_output_times`. The exact final time is
always included; if the requested interval does not divide the horizon, the
last output interval is shorter. `t_span` starts integration at the event even
when that instant is absent from the output grid; `t_eval` only chooses which
post-event times to return. The runner verifies identical timestamps before
combining trajectories. Non-finite parameters are rejected before simulation.

SciPy returns an array with states in rows and times in columns. Transposing
it gives the project's convention: one row per instant. `AggregateFCRTrajectory`
stores that result as separate frequency and provider arrays, along with
requested responses, reserve limits, and optional total generator powers.

## 5. What the experiment compares

`scripts/run_pjm5_fcr_teaching.py` runs four calculations:

| Calculation | Purpose |
|---|---|
| Numerical frequency with four providers | Main FCR experiment |
| Numerical frequency with an empty provider list | Same solver without FCR |
| Analytical frequency without FCR | Independent regression reference |
| Numerical FCR with a 0.025 s maximum step and tighter tolerances | Numerical convergence check |

For $D>0$, the analytical no-FCR trajectory after the event at $t_0$ is

$$
\Delta f(t)=-\frac{d}{D}\left(1-e^{-(D/M_f)(t-t_0)}\right),\quad t\ge t_0.
$$

Before $t_0$ it is zero; with $D=0$, it is the ramp $-d(t-t_0)/M_f$.
The old `simulate_power_step` also permits a constant response starting at
$t_0$; it defaults to zero. The standalone frequency derivative can accept
changing response values, which is how the numerical FCR simulator reuses it.

The runner fails on solver failure, non-finite solver states, and sampled
reserve violations. It prints the analytical and refined-solver differences;
their tolerance thresholds are asserted by tests for the default preset.
The convergence test compares both frequency and all provider trajectories.

## 6. Understanding the observed result

Re-running the default configuration on 2026-10-02 reproduced:

| Provider | Maximum sampled $\lvert u_i\rvert$ [MW] | $q_i^\star$ [MW] | $u_i(40)$ [MW] |
|---|---:|---:|---:|
| 1 | 1.000000 | 1 | 1.000000 |
| 2 | 1.996375 | 2 | 1.760870 |
| 3 | 2.827116 | 3 | 2.347826 |
| 4 | 3.106957 | 4 | 2.934783 |

Without FCR the final frequency is 59.400035 Hz. With FCR, the lowest sampled
frequency is 59.827191 Hz and the final value is 59.882609 Hz. These are samples
of the trajectories, not a continuous-time search for the exact minimum.

At equilibrium provider 1 is saturated at 1 MW while providers 2–4 remain
proportional to the frequency error. The balance gives

$$
0=-D\Delta f_\infty-10+1-(15+20+25)\Delta f_\infty,
\qquad
\Delta f_\infty=-\frac{9}{D+60}=-0.117391\ \mathrm{Hz}.
$$

Thus the providers deliver about 8.043478 MW, while the reduction in
frequency-sensitive load contributes about 1.956522 MW. Together these offset
the 10 MW deficit. Merely having 10 MW of reserve available does not mean all
10 MW must be delivered at equilibrium. Primary FCR contains the deviation;
exact restoration would require a later secondary controller.

## 7. Sources and boundaries

Local sources, intentionally omitted from Git:

- `references/week3_eth.pdf`, PDF pages 8 and 16–17: coupled model and provider
  law; pages 12–14: aggregation and load response; page 10: prototype exclusions.
- `references/PSDCO_Script_2026 (1).pdf`, Chapters 8–9, as cited in the code and
  Week 3 derivation.
- Accepted local decisions D-009 and D-010: PJM baseline, units and provider states.

Page numbers above count PDF pages, since several appendix pages repeat the
same slide footer number. The model has no dynamic line constraints, market
allocation, economic payments, technology-specific capability, secondary
restoration or functional-incentive provider response yet. ANDES `PV` denotes
a power-flow bus/generator type; it does not identify photovoltaic technology.
