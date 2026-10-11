# Current FCR–FI model

Updated 2026-10-11. This document describes the single current implementation.
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

The physical state contains $\Delta f=f-f_N$ in Hz and one delivered-response
state per provider: $u_i$ for FCR, $z_i$ for FI, and zero for inactive providers.
Power is in MW, time in seconds and ratings in MVA. Provider power is
$P_i=P_{i,0}+u_i$. A positive deficit $d$ lowers frequency; positive $u_i$
adds power.

Renewable units are static voltage-controlled injections for power flow,
followed by the assigned FCR or FI first-order response law in the aggregate
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

Each FCR candidate chooses a price and reserve quantity maximizing

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
Extra activation costs, energy payments and clawbacks are absent from FCR by explicit
experiment convention. FI has its own activation cost below. The FCR penalty concerns available capacity, not transient
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
M_f\dot{\Delta f}=-D\Delta f-d+\sum_{i\in\mathcal F}u_i+\sum_{i\in\mathcal I}z_i,\qquad
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
simulation ends at 40 s. FCR-only runs integrate after the event. Mixed runs
start the FI decision clock at zero and restart RK45 at decisions and the event;
physical states and balances remain continuous. Output includes
the exact final time, even for a non-divisible sampling interval.
Primary control contains frequency and generally leaves a steady offset.
Exact restoration would require secondary control.

The analytical no-controlled-response reference (historical name `no_fcr`) remains part of validation:

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
| Uncertain variance reduced to 0.04 | [3.945482,3.947217,3.950723,1.699528,1.699528] | [3.945482,3.947217,0.708244,1.699528,1.699528] | 24 |

The discrete baseline was rerun before the update and saved under
`results/discrete_baseline/`. For base and lower uncertainty, its offers were
[4,4,4,1,0] and [4,4,4,1,1], and its awards were [4,4,3,1,0] and [4,4,2,1,1].
Both clearing prices remain unchanged. Positive bid prices remain
[15,18,24,20,20]; an opted-out renewable records its lower price bound of 10.

| Case | Previous nadir [Hz] | Current nadir [Hz] | Previous final [Hz] | Current final [Hz] |
|---|---:|---:|---:|---:|
| Base | 59.912123 | 59.919129 | 59.975998 | 59.974692 |
| Lower uncertainty | 59.922871 | 59.934946 | 59.975998 | 59.973452 |

The uncertainty case changes only the uncertain provider's variance. Seed
20261008 is retained. All old/new pairs have identical availability draws;
between current cases, reducing variance changes only that provider's draw.
The physical equations and gains are unchanged. The detailed old/new provider
comparison is `results/fcr/bidding_update_comparison.csv`; regenerated plots
and trajectories are under `results/fcr/`.

The preceding FCR update passed 46 tests, including continuous optimality, min/max/zero boundary
cases, safety margin validation, fixed modes, scenario precedence, provider
overrides and the existing dynamics/market/ANDES checks. Across these two retained
runs, maximum refinement differences are $2.61\times10^{-9}$ Hz and
$4.14\times10^{-7}$ MW; no-FCR analytical disagreement is below
$7.2\times10^{-15}$ Hz. These are illustrative single-event outcomes, not a
general policy result.

## 7. Mutually exclusive participation

The user fixes each provider's participation policy before the episode:

| `participation` | FCR offer | Event role |
|---|---|---|
| `fcr` (default) | Existing optimized or prescribed offer | FCR, even with a zero award |
| `fi` | Forced zero | FI, assigned exogenously |
| `fcr_or_fi` | Existing optimized offer | FI only if the chosen offer is zero; otherwise FCR |
| `inactive` | Forced zero | No controlled response |

The realized sets $\mathcal F$ and $\mathcal I$ are disjoint and fixed during
the event. Positive offers rejected by the auction remain in $\mathcal F$ with
zero response capability. Explicit FI and inactive providers cannot receive
positive fixed FCR offers or awards. `fcr_or_fi` requires optimized offers;
a prescribed zero offer is not evidence of an economic opt-out.

The endogenous rule is a **sequential FCR opt-out rule**, not a joint comparison
of expected FCR and FI profit. It preserves the existing private offer reward,
including its lack of acceptance prediction and its minimum-bid dependence.
Once a provider opts out, it becomes an FI participant without an additional
enrollment optimization. The simulator does not switch roles during an event.

Only FCR awards satisfy $Q_{\mathrm{req}}$. FI cannot rescue an infeasible
capacity auction. Availability is drawn once after feasible clearing for every
provider in stable ID order, including FI/inactive providers. Each FI provider
then observes its own $A_i$, knows its own dynamics and activation cost, and
measures the common aggregate frequency without noise or delay.

The physical dispatch, online synchronous inertia and solved headroom do not
depend on the participation label. FI uses $|v_i|\le A_i\le q_i^{\rm phys}$;
it does not use the zero FCR award as a capability limit. The bidding safety
margin limits FCR offers only. No shared reserve or double payment is allowed.

## 8. Fixed incentive and provider optimization

The TSO/user announces constant $\gamma_i\ge0$ before the run. There is **no
TSO outer optimization or automatic tuning**. FI decisions share a configurable
clock $t_k=k\Delta$ starting at zero, with $t_{K}=H$ and a shortened last
interval if necessary. Let $h_k=t_{k+1}-t_k$ and $e_k=\Delta f(t_k)$.

The held target $v_{i,k}$ drives actual delivered power:

$$
T_i\dot z_i=-z_i+v_{i,k},\qquad z_i(0)=0,\qquad
v_{i,k}\in[-A_i,A_i],\qquad P_i=P_{i,0}+z_i.
$$

This invariant interval keeps actual power within both declared margins.
The payment rate and activation-cost rate are

$$
s_i(t)=-\gamma_i e_k z_i(t),\qquad
c_i(t)=\tfrac12\rho_i z_i(t)^2,\qquad \rho_i>0.
$$

Units are EUR/s for both rates, EUR/(Hz MW s) for $\gamma_i$ and
EUR/(MW² s) for $\rho_i$. These coefficients are separate from whole-block
FCR costs and capacity-shortage penalties. Gamma is nonnegative; the **payment**
can have either sign. With a sampled negative frequency error, positive power
earns money and negative power incurs a charge; signs reverse for positive error.

At $t_k$, all FI providers simultaneously maximize their next-interval predicted
net income, using the same pre-decision physical state:

$$
v_{i,k}^*=\arg\max_{|v|\le A_i}\int_0^{h_k}
\left[-\gamma_i e_k\widehat z_i(\tau;v)-\tfrac12\rho_i\widehat z_i(\tau;v)^2\right]d\tau,
\quad
\widehat z_i(\tau;v)=a_i(\tau)z_i(t_k)+b_i(\tau)v,
$$

where $a_i(\tau)=e^{-\tau/T_i}$ and $b_i=1-a_i$. There is no frequency
forecast or strategic prediction of other providers. Freezing $e_k$ is also
the actual contract rule, rather than an approximation of continuous-frequency
settlement. Define the moments over $[0,h_k]$:

$$
I_a=T_i(1-e^{-h_k/T_i}),\quad I_b=h_k-I_a,\quad
I_{aa}=\tfrac{T_i}{2}(1-e^{-2h_k/T_i}),\quad
I_{ab}=\tfrac{T_i}{2}(1-e^{-h_k/T_i})^2,\quad
I_{bb}=h_k-2I_a+I_{aa}.
$$

The exact constrained solution is

$$
v_{i,k}^*=\operatorname{clip}\left(
\frac{(-\gamma_i e_k/\rho_i)I_b-z_i(t_k)I_{ab}}{I_{bb}},-A_i,A_i\right).
$$

$I_{bb}>0$ for a positive interval. Small-interval series avoid cancellation.
The command may deliberately overshoot the desired delivered level to overcome
lag, while staying within availability. In the instantaneous-response limit,
the rule reduces to $\operatorname{clip}(-\gamma_i e_k/\rho_i,-A_i,A_i)$.

### Relationship to the paper

The primary local paper `references/2604.27770v2.pdf`, equations (3)–(6), supplies
the bilinear state/action incentive, quadratic private cost and myopic best
response. With the scalar convention $\Theta=-\gamma_i$ and $R_i=\rho_i/2$,
its unconstrained instantaneous response has the same normalization.
Our multiple providers, explicit FCR exclusion, first-order delivered response,
finite-interval objective, physical saturation and cash ledgers are extensions
agreed for this experiment. The paper's outer incentive-design problem is
deliberately omitted; its stability results are not asserted for this extension.

## 9. Benchmarks, actual settlement and profit

For each interval, record three distinct payment amounts:

$$
B_{i,k}=(-\gamma_i e_k)v_{i,k}h_k,
\qquad
\widehat S_{i,k}=(-\gamma_i e_k)(I_a z_i(t_k)+I_b v_{i,k}),
\qquad
S_{i,k}=\int_{t_k}^{t_{k+1}}s_i(t)\,dt.
$$

$B$ assumes instantaneous target delivery and is only a benchmark.
$\widehat S$ uses analytical predicted delivery. $S$ integrates **actual numerical
delivered power**, including residual response from previous targets. Predicted
activation cost is

$$
\widehat C_{i,k}=\tfrac12\rho_i\left(I_{aa}z_i(t_k)^2+
2I_{ab}z_i(t_k)v_{i,k}+I_{bb}v_{i,k}^2\right).
$$

The actual cumulative balances, initialized at zero, obey

$$
\dot R_i=\max(s_i,0),\qquad \dot L_i=\max(-s_i,0),\qquad
\dot C_i=\tfrac12\rho_i z_i^2,\qquad
S_i=R_i-L_i,\qquad \Pi_i=S_i-C_i.
$$

Gross penalties $L_i$ are reported as positive magnitudes. Splitting the sign
**before** integration preserves rewards and charges within the same interval.
Netting an interval first would lose that information. The solver integrates
these rates in seconds alongside the physical states, rather than summing
sampled rates. Financial values are continuous at decisions even though rates
can jump. Outputs at interior decision boundaries use the new command/sample;
the final endpoint reports the last interval's command and rate.

Aggregate FI transfers are $R=\sum_iR_i$, $L=\sum_iL_i$, $S=R-L$; positive
$S$ is net TSO expenditure. Aggregate provider profit is $S-\sum_i C_i$.
FCR capacity payments and profits retain their whole-block basis. FI figures
cover only the simulated horizon $[0,H]$: there is no multiplication by the
four-hour block duration and no settlement after $H$. Comparing profitability
across the two products over a common commercial horizon needs additional
event-frequency and participation assumptions.

### Timing, checks and interpretation

At each decision: observe $e_k,z_i(t_k),A_i$; optimize a held target; integrate
actual response and rates until the next decision; carry state and balances
forward. The deficit may begin between decisions and does not trigger a new
FI measurement. The last shortened interval is optimized for its actual duration.
`fi.decision_interval_s`, `scenario.maximum_solver_step_s`, and
`scenario.output_time_step_s` have distinct roles. Refinement keeps the decision
clock, parameters, awards and availability fixed.

Prediction and realized net settlement agree up to numerical tolerance under
the assumed perfect knowledge of first-order dynamics and fixed availability.
This is expected; the target benchmark can differ substantially. Each run
checks cumulative-ledger finiteness/monotonicity and prediction/refinement
agreement, with monetary tolerance $10^{-6}\max(1,\max_{i,t}(R_i+L_i+C_i))$ EUR.
Tests independently integrate rewards, charges and costs, including a
wrong-sign residual response and a sign crossing within an interval.

The contract classifies helpful/harmful delivery using **sampled** frequency.
If the live frequency crosses nominal between samples, the frozen signal can
reward a response that is no longer helpful to the instantaneous frequency.
There is no guaranteed nonnegative realized profit, stability, budget balance,
or monotonic improvement with gamma. The initial model has no measurement
noise, energy/SoC limit, separate ramp constraint, future-income objective,
time-varying availability, secondary restoration, or exit during an interval.

## Sources and scope

Local primary references are `references/PSDCO_Script_2026 (1).pdf`,
Chapters 8–9, and `references/week3_eth.pdf`, PDF pages 8 and 16–17 for the
provider/frequency equations and pages 12–14 for aggregation/load response.
The implemented inertia omits a separate rotating-load inertia term.
Local accepted decisions and current context are in `local_context/`.

The implemented FI is the fixed-gamma bilinear contract above. A separate
performance/Lyapunov formula, optimized TSO design, secondary control and detailed
network validation remain future work.
