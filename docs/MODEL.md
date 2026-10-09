# Current FCR model

Updated 2026-10-09. This document describes the single current implementation.
All scenario changes use [configs/fcr.toml](../configs/fcr.toml) and
[scripts/run_fcr.py](../scripts/run_fcr.py).

## 1. Physical conventions and operating point

ANDES solves the initial power flow on the PJM 5-bus network. The default
configuration supplies three conventional and two renewable generators.
Non-slack production setpoints are fixed; the conventional slack generator
balances load and losses. The source network file is never changed.

| Provider | Bus | $P_{i,0}$ [MW] | Power base [MVA] | $H_i$ [s] | $T_i$ [s] |
|---|---:|---:|---:|---:|---:|
| Conventional 1 | 0 | 260 | 300 | 5 | 2 |
| Conventional 2 | 2 | 310 | 400 | 4 | 3 |
| Conventional 3, slack | 3 | 250.906096 | 400 | 4 | 4 |
| Reliable renewable | 4 | 90 | 150 | 0 | 0.25 |
| Uncertain renewable | 1 | 90 | 150 | 0 | 0.25 |

The state is $[\Delta f,u_1,\ldots,u_n]$ with $\Delta f=f-f_N$ in Hz.
Power is in MW, time in seconds and ratings in MVA. Provider power is
$P_i=P_{i,0}+u_i$. A positive deficit $d$ lowers frequency; positive $u_i$
adds power.

Renewable units are static voltage-controlled injections for power flow,
followed by the same project-owned first-order droop law in the aggregate
simulation. They add no synchronous or synthetic inertia. ANDES PV denotes
a power-flow type, not photovoltaic technology. Detailed converter, wind/solar,
network-transient and energy-limit models are outside this approximation.

## 2. Capability and 5% droop

Declared physical reserve must satisfy both active-power margins:

$$
q_i^{\mathrm{phys}}\le
\min(P_{i,\max}-P_{i,0},P_{i,0}-P_{i,\min}).
$$

The default active-power bounds are $[50,280]$, $[60,380]$, $[60,380]$,
$[0,120]$, $[0,120]$ MW. All five reserve caps are 4 MW. These are explicit
pedagogical capability assumptions, independent of ratings.

`power_setpoint_mw` is the non-slack dispatch input and only an initialization
guess for the slack. Capability checks use the **solved** $P_{i,0}$ in all cases.
`reserve_capacity_mw` is the declared symmetric reserve capability, which may
be smaller than available active-power headroom. Invalid capacity is rejected.

An optional provider-specific bidding policy adds a reserve safety margin:

$$
q_i^{\max,\mathrm{offer}}=(1-h_i)q_i^{\mathrm{phys}},\qquad h_i\in[0,1].
$$

The parameter is `reserve_safety_margin_fraction`, default zero. It represents
additional reserve withheld from bidding after the physical checks, rather
than a second calculation of headroom. For 4 MW and $h_i=0.1$, the bid ceiling
is 3.6 MW. Availability remains bounded by the physical 4 MW cap; only the bid
ceiling changes. This is a configurable conservative policy assumption, not
a probabilistic reliability constraint. It can bind even when the expected
penalty would economically justify a larger offer. No gain is retuned.

With active-power normalization base $P_{B,i}$ and droop $R_i=0.05$,

$$
\frac{r_i^{\mathrm{unlim}}}{P_{B,i}}
=-\frac{1}{R_i}\frac{\Delta f}{f_N},
\qquad K_i=\frac{P_{B,i}}{R_i f_N},
\qquad \frac{K_if_N}{P_{B,i}}=20.
$$

A 150 MVA converter/machine base numerically gives 150 MW for 1 pu active
power; it does not establish $P_{\max}$. The default gains are
$[100,133.3333,133.3333,50,50]$ MW/Hz at 60 Hz.
This uses the percentage-droop convention described in
[NERC's Balancing and Frequency Control reference](https://www.nerc.com/comm/OC/ReferenceDocumentsDL/Reference_Document_NERC_Balancing_and_Frequency_Control.pdf).
Five percent is dimensionless, not a 0.05 Hz activation band.

Awards and availability never retune $K_i$. Saturation begins at
$q_i^{\mathrm{eff}}/K_i$; equal normalized droop gives equal thresholds only
when effective reserve scales in the same proportion as the power base.
An explicit per-provider gain override is available for parameter experiments.

## 3. Availability and provider decisions

An underlying scalar Gaussian describes reserve:

$$
X_i\sim\mathcal N(\mu_i,\sigma_i^2).
$$

Its scalar covariance is the variance in MW². The example conditions the
Gaussian on $[0,q_i^{\mathrm{phys}}]$, giving continuous truncated availability
$A_i$. The alternative `clipped` mode censors draws at those bounds and creates
boundary point masses. Configured moments describe the underlying Gaussian;
the physical distribution's moments generally change after bounding.
Zero variance gives deterministic availability.

Draw once after clearing, independently across providers, and hold the draw
fixed during the event. Provider decisions know the same law used for sampling;
they do not see the realized reserve before bidding.

The whole-block commitment cost and expected shortage are

$$
C_i(q)=a_iq+\tfrac12\beta_iq^2,\qquad
\ell_i(q)=\mathbb E[(q-A_i)_+]=\int_0^q F_{A_i}(x)\,dx.
$$

The implementation evaluates the CDF integral deterministically with stable
truncated-normal functions. It does not replace availability by its mean.
For a truncated Gaussian, with $a=-\mu/\sigma$,
$b=(q^{\mathrm{phys}}-\mu)/\sigma$ and $z=(q-\mu)/\sigma$,

$$
\ell(q)=
\frac{(q-\mu)[\Phi(z)-\Phi(a)]+\sigma[\phi(z)-\phi(a)]}
{\Phi(b)-\Phi(a)}.
$$

Each provider chooses a price and reserve quantity maximizing

$$
R_i(c,q)=cq-C_i(q)-\kappa_{\mathrm{res},i}\ell_i(q).
$$

The feasible set is

$$
c_i\in[c_i^{\min},c_i^{\max}],\qquad
\bar q_i\in\{0\}\cup[q_{\min},q_i^{\max,\mathrm{offer}}].
$$

Prices have configurable provider-specific nonnegative bounds. Since
$\partial R/\partial c=q>0$, the exact price optimum for positive quantity is
$c_i^{\max}$. For nonnegative $\beta_i$ and $\kappa_i$, the quantity reward is
concave because expected shortage is convex. SciPy bounded scalar minimization
of $-R$ searches the positive interval, with explicit endpoint comparisons and
the deterministic availability kink when variance is zero. Its absolute
quantity tolerance is configurable (default $10^{-8}$ MW).

Zero has reward zero; participation requires strictly positive optimized
reward. An upper bound below the minimum forces opt-out; equal bounds permit
one positive quantity. Exact ties prefer smaller quantity, then smaller price
(the minimum price is recorded for a zero bid). The market-wide default
$q_{\min}=1$ MW applies to offers; partial awards may be smaller.

This objective still does not predict acceptance, partial awards or the actual
clearing price. Its value is a private offer reward, not expected auction profit
or an auction equilibrium. The price ceiling result is a limitation of this
deliberately retained assumption.

## 4. Capacity market and settlement

For divisible nonnegative-price offers,

$$
\min_{\{q_i\}}\sum_i c_iq_i,\qquad
\sum_i q_i\ge Q_{\mathrm{req}},\quad 0\le q_i\le\bar q_i.
$$

The algorithm accepts cheapest bids first, with provider ID resolving ties,
and buys exactly the requirement. The marginal offer may be partial.
The uniform price is the highest positively accepted bid:
$\lambda^\star=\max_{q_i^\star>0}c_i$.

Zero requirement gives zero awards and price. Insufficient supply returns an
infeasible result with no price and stops the episode before sampling.
Negative bids, indivisible blocks and scarcity pricing are not included.

Prices are EUR/MW **for the whole block**, initially four hours.
Capacity payment is $\lambda^\star q_i^\star$ without another hours multiplier.
Realized accounting uses

$$
\Pi_i=\lambda^\star q_i^\star-C_i(q_i^\star)
-\kappa_{\mathrm{res},i}(q_i^\star-A_i)_+.
$$

Cost applies to the award; rejected offers have zero commitment cost.
Extra activation costs, energy payments and clawbacks are absent by explicit
experiment convention. The penalty concerns available capacity, not transient
tracking lag or reserve left unactivated by a small frequency error.

Offered capacity, award, availability and effective reserve stay separate:

$$
q_i^{\mathrm{eff}}=\min(q_i^\star,A_i).
$$

Fixed offers and fixed awards are optional explicit inputs to this same flow;
all capability, identity, payment and reserve-limit checks remain active.

## 5. Frequency dynamics and numerical checks

The coupled model is

$$
M_f\dot{\Delta f}=-D\Delta f-d+\sum_i u_i,\qquad
T_i\dot u_i=-u_i+\operatorname{clip}(-K_i\Delta f,-q_i^{\mathrm{eff}},q_i^{\mathrm{eff}}).
$$

Only online synchronous machines enter inertia:

$$
H_{\mathrm{eq}}=\frac{\sum H_iS_i}{\sum S_i},\qquad
M_f=\frac{2\sum H_iS_i}{f_N}.
$$

The default has $S_{\mathrm{sync}}=1100$ MVA, $H_{\mathrm{eq}}=4.272727$ s
and $M_f=156.666667$ MW s/Hz. Rating-to-generator joins use identities.
Load damping is an explicit assumption
$D=\alpha P_{\mathrm{load},0}/f_N=16.666667$ MW/Hz with $\alpha=1$.
A power-flow solution cannot identify this load sensitivity.

The state starts at zero. A permanent 10 MW deficit begins at 1 s and the
simulation ends at 40 s. RK45 integrates only after the event; output includes
the exact final time, even for a non-divisible sampling interval.
Primary control contains frequency and generally leaves a steady offset.
Exact restoration would require secondary control.

The analytical no-FCR reference remains part of validation:

$$
\Delta f(t)=-\frac{d}{D}\left(1-e^{-D(t-t_0)/M_f}\right),\quad t\ge t_0,
$$

or $-d(t-t_0)/M_f$ for $D=0$. It is not a separate experiment implementation.
Each run checks solver success, finite states, identical sample grids,
reserve bounds and refinement with the same awards/draws.
Limits are $10^{-8}$ MW for sampled bounds, $10^{-8}$ Hz for analytical
agreement, $10^{-6}$ Hz and $10^{-5}$ MW for refinement.

## 6. Default participation result

Both renewables have mean 2 MW, cap 4 MW, $a=5$, $\beta=1$ and penalty
200 EUR per missing MW per block. Their underlying variances are 0.04 and
4 MW². Because the truncation interval is symmetric around the mean, both
physical distributions retain mean 2 MW. The uncertain one's resulting
standard deviation is 1.079120 MW.

For its minimum positive 1 MW offer at price 20:

$$
R(20,1)=20-5.5-200(0.1029852085)=-6.097042\ \mathrm{EUR}.
$$

The reward is decreasing throughout [1,4] MW in this case, so all feasible
positive offers are unprofitable. It chooses zero before the auction. This
differs from having a positive offer rejected.

The feasible quantities are $\{0\}\cup[1,4]$ MW. This minimum matters:
$R'(0^+)=c-a>0$ for the continuous bounded law, so tiny offers can still be
profitable if the minimum is relaxed. Uncertainty alone does not universally
force nonparticipation.

Current results, with provider order conventional 1–3, reliable renewable,
uncertain renewable, rounded to six decimals:

| Case | Offers [MW] | Awards [MW] | Price [EUR/MW/block] |
|---|---|---|---:|
| Base | [3.945482,3.947217,3.950723,1.699528,0] | [3.945482,3.947217,2.407773,1.699528,0] | 24 |
| No penalty, all providers | [4,4,4,4,4] | [4,4,0,4,0] | 20 |
| Uncertain variance reduced to 0.04 | [3.945482,3.947217,3.950723,1.699528,1.699528] | [3.945482,3.947217,0.708244,1.699528,1.699528] | 24 |

The discrete baseline was rerun before the update and saved under
`results/discrete_baseline/`. Its offers were [4,4,4,1,0], [4,4,4,1,4] and
[4,4,4,1,1], and its awards were [4,4,3,1,0], [4,4,0,1,3] and [4,4,2,1,1].
All three clearing prices remain unchanged. Positive bid prices remain
[15,18,24,20,20]; an opted-out renewable records its lower price bound of 10.

| Case | Previous nadir [Hz] | Current nadir [Hz] | Previous final [Hz] | Current final [Hz] |
|---|---:|---:|---:|---:|
| Base | 59.912123 | 59.919129 | 59.975998 | 59.974692 |
| No penalty | 59.934913 | 59.906329 | 59.969988 | 59.963988 |
| Lower uncertainty | 59.922871 | 59.934946 | 59.975998 | 59.973452 |

The previous `no_penalty` removed only the uncertain provider's penalty;
the current case removes it for **every** provider. Thus this row combines the
optimizer update and the requested policy correction. Both renewables now bid
4 MW at 20, so the existing provider-ID tie-break awards the reliable renewable
all remaining 4 MW. The uncertain renewable participates but is rejected.
The reliable renewable delivers only its sampled 1.9476 MW of available reserve,
which helps explain the worse frequency response in this realization.

The penalty case changes only penalties relative to the current base;
the uncertainty case changes only the uncertain provider's variance. Seed
20261008 is retained. All old/new pairs have identical availability draws;
between current cases, reducing variance changes only that provider's draw.
The physical equations and gains are unchanged. The detailed old/new provider
comparison is `results/fcr/bidding_update_comparison.csv`; regenerated plots
and trajectories are under `results/fcr/`.

All 46 tests pass, including continuous optimality, min/max/zero boundary
cases, safety margin validation, fixed modes, scenario precedence, all-provider
penalties and the existing dynamics/market/ANDES checks. Across these three
runs, maximum refinement differences are $2.61\times10^{-9}$ Hz and
$4.14\times10^{-7}$ MW; no-FCR analytical disagreement is below
$7.2\times10^{-15}$ Hz. These are illustrative single-event outcomes, not a
general policy result.

## Sources and scope

Local primary references are `references/PSDCO_Script_2026 (1).pdf`,
Chapters 8–9, and `references/week3_eth.pdf`, PDF pages 8 and 16–17 for the
provider/frequency equations and pages 12–14 for aggregation/load response.
The implemented inertia omits a separate rotating-load inertia term.
Local accepted decisions and current context are in `local_context/`.

Functional incentives, secondary control and detailed network validation are
future work. The exact performance/Lyapunov incentive formula remains open.
