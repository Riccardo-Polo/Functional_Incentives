# Next stage: the FCR market and provider decisions

**Status: proposal for the next implementation, 2026-10-02.** No market or
economic provider model has been implemented. Source equations and additional
modelling proposals are distinguished below; none of the proposed choices is
recorded as an accepted economic decision yet.

## Why this is the next useful step

The current simulation answers: given four reserve allocations, how does the
frequency evolve? The next experiment should explain where those allocations
come from. The supervisor's suggestion adds the earlier question: why would
each provider offer that capacity at that price?

The proposed sequence is

$$
\text{capability and costs}
\longrightarrow (\bar q_i,c_i)
\longrightarrow (q_i^\star,\lambda^\star)
\longrightarrow u_i(t),\Delta f(t).
$$

The provider decides its offer; the market decides which offers to accept;
the existing physical model determines what happens during an event.

## 1. What the Week 3 slides establish

The primary source is the local `references/week3_eth.pdf`, dated
29 September 2026. Page references here count PDF pages, not slide footer labels.

| PDF pages | Relevant content |
|---|---|
| 3–4 | Symmetric FCR capacity, a common capacity price, no separate FCR energy payment |
| 7 | Offer $(\bar q_i,c_i)$, awarded capacity $q_i^\star$, clearing price $\lambda^\star$ |
| 8 | Linear capacity procurement problem and frequency/provider equations |
| 10 | Initial exclusions: indivisible bids, cross-border constraints and detailed market rules |
| 16 | Saturation at the awarded capacity connects the market to the response model |
| 18 | Procurement before delivery, with capacity available throughout a four-hour block |

This is the simplified research auction in those slides. It is not a complete
implementation of the operating FCR Cooperation market or a verification of
current regulations. The slides do not specify a provider optimization that
derives $c_i$, a complete pricing rule for edge cases, or penalties.

## 2. Keep the quantities distinct

| Symbol | Meaning | Who determines it? |
|---|---|---|
| $q_i^{\mathrm{phys}}$ | Feasible symmetric capacity around the initial power | Capability model and data |
| $\bar q_i$ | Maximum capacity the provider offers | Provider decision |
| $c_i$ | Price submitted for that offered capacity | Provider decision |
| $Q_{\mathrm{req}}$ | Total capacity the operator wants to reserve | Scenario/operator input |
| $q_i^\star$ | Capacity actually awarded | Market allocation |
| $\lambda^\star$ | Common price paid per awarded MW | Explicit market pricing rule |
| $u_i(t)$ | Power actually delivered during an event | Frequency and provider dynamics |

The minimum consistency requirement is

$$
0\le q_i^\star\le\bar q_i\le q_i^{\mathrm{phys}}.
$$

For a provider with sourced active-power limits and fixed $P_{i,0}$, symmetric
reserve is bounded by both upward and downward margins:

$$
q_i^{\mathrm{phys}}\le
\min\{P_{i,\max}-P_{i,0},\ P_{i,0}-P_{i,\min}\}.
$$

These are necessary power limits, not a complete capability test: response
speed, energy limits and availability over the delivery block can also matter.
For now physical limits are unknown. A first market demonstration may use
explicitly assumed capacities without presenting them as measured capability.

The current TOML `reserve_mw` means $q_i^\star$. It must not silently change
meaning to $\bar q_i$. Future input and output records need separate fields.

## 3. Allocation and price

**Source: Week 3, PDF page 8.** The operator minimizes total submitted bid value:

$$
\begin{aligned}
\min_{\{q_i\}}\quad &\sum_i c_iq_i\\
\text{subject to}\quad&\sum_i q_i\ge Q_{\mathrm{req}},\\
&0\le q_i\le\bar q_i.
\end{aligned}
$$

For divisible capacity and nonnegative bids, this can be solved by accepting
the cheapest offers first and taking only the required part of the last offer.
The optimal quantities are denoted $q_i^\star$. The capacity requirement is an
input chosen before the disturbance; it need not equal the example's 10 MW
deficit.

**Proposed first pricing convention:** for a feasible, positive requirement,
pay every awarded MW the highest accepted bid price,

$$
\lambda^\star=\max_{i:q_i^\star>0}c_i.
$$

This is an explicit convention for the simplified auction, not a claim that
the slides fully specify real-market price formation. For a partly accepted
marginal offer it agrees with the usual marginal value of additional reserve.
At an exact boundary between offers the optimization can have more than one
valid dual price. An LP solver's reported dual should therefore be checked
against the chosen pricing convention rather than treated as a unique price.

Proposed edge-case rules to settle before coding: deterministic ordering by
provider ID for equal bids; no excess allocation when zero-price offers make
several solutions optimal; zero awards and price zero for $Q_{\mathrm{req}}=0$;
and an explicit infeasible result with no claimed clearing price when supply
is insufficient. Negative bids, indivisible blocks, scarcity pricing and
penalties are outside the proposed first implementation.

## 4. Price units and payments

**Proposed convention:** express $c_i$ and $\lambda^\star$ in EUR per MW for
one named delivery block. The slide's EUR/MW capacity quote is then interpreted
over the specified four-hour product. Payment for the block is

$$
\Pi_i^{\mathrm{cap}}=\lambda^\star q_i^\star.
$$

Do not multiply this by four hours again. If an alternative model uses prices
in EUR/(MW h), the correct formula becomes $\lambda^\star q_i^\star h_{\mathrm{block}}$.
The units must be chosen before implementing prices, costs or profits.

The 40 s disturbance experiment is a physical test within a delivery block.
It does not by itself measure the cost of keeping reserve available for the
whole block. The slides' absence of a separate FCR energy payment does not
imply that producing or reducing energy has no cost for a provider.

## 5. A small auction that can be checked by hand

**Invented test values, not observed market prices.** Assume offered capacities
$[1,2,3,4]$ MW, bids $[10,15,20,30]$ EUR/MW per block, and a requirement of 5 MW.

| Provider | Offered MW | Bid per MW | Awarded MW | Capacity payment [EUR] |
|---|---:|---:|---:|---:|
| 1 | 1 | 10 | 1 | 20 |
| 2 | 2 | 15 | 2 | 40 |
| 3 | 3 | 20 | 2 | 40 |
| 4 | 4 | 30 | 0 | 0 |

The awards are $[1,2,2,0]$ MW and the proposed clearing price is 20 EUR/MW per
block. Accepted bid value is 80 EUR, while uniform-price payments total 100 EUR.
These are different quantities. Actual profit also depends on true costs.

If the requirement were 10 MW, equal to all the offered capacity, the awards
would have to be $[1,2,3,4]$ MW. Changing the bid prices would not change that
allocation. Use excess offered capacity in experiments intended to show how
economic choices change the provider mix.

## 6. How a provider could determine its bid

The slides call $c_i$ a capacity bid. A submitted price and a true cost are
different objects. Define $C_i(q)$ as the provider's expected incremental cost,
in EUR for the complete block, of committing $q$ MW relative to not reserving it.

**Proposed cost accounting**, to be parameterized for an explicit technology:

$$
C_i(q)=C_i^{\mathrm{availability}}(q)
      +C_i^{\mathrm{opportunity}}(q)
      +\mathbb{E}_{\omega}[C_i^{\mathrm{activation}}(q,\omega)].
$$

These components must be defined without double counting. They can represent
the cost of staying ready, lost operating profit from holding capacity back,
and the net cost of later response. $\omega$ denotes possible future operating
conditions. Their probabilities and costs must be explicit assumptions or
sourced data; the provider cannot know the realized disturbance in advance
unless that is deliberately assumed for a deterministic teaching test.

### First behavioural model: a provider that takes the expected price as given

For a first transparent model, assume the provider treats a forecast price
$\hat\lambda_i$ as independent of its own offer. Also assume, for this first
quantity decision, that the offered capacity will be fully accepted and paid
at that forecast price. Price-taking alone does not imply full acceptance;
the subsequent auction can still award less. Under this additional teaching
assumption the planned quantity is

$$
\bar q_i\in\arg\max_{0\le q\le q_i^{\mathrm{phys}}}
\left[\hat\lambda_iq-C_i(q)\right].
$$

Then explicitly assume cost-based offers. If costs are linear,

$$
C_i(q)=a_iq,\qquad c_i=a_i.
$$

Here $a_i$ is the incremental cost per reserved MW for the block, assembled
from the stated cost assumptions. The provider offers its available capacity
if $\hat\lambda_i>a_i$, offers zero if $\hat\lambda_i<a_i$, and is indifferent
at equality (a tie convention is needed). This is a decision model under a
price-taking and cost-based bidding assumption; truthful bidding is not a
general result of a uniform-price auction.

If $C_i(q)$ is curved, marginal cost $C_i'(q)$ varies with quantity. A single
constant $c_i$ cannot describe the whole cost curve faithfully. A later version
can use multiple bid segments or a documented approximation. With fixed costs,
average cost $C_i(q)/q$ and marginal cost also differ; a participation rule is
needed to avoid claiming that marginal-cost bids always recover total costs.

### Later extension: a provider anticipates its influence on the auction

If the supervisor intends strategic bidding, the provider chooses its offer
anticipating how the market responds. A possible research formulation is

$$
\max_{(\bar q_i,c_i)\in\mathcal B_i}
\mathbb E\!\left[
\lambda^\star(b_i,b_{-i})q_i^\star(b_i,b_{-i})
-C_i\!\left(q_i^\star(b_i,b_{-i})\right)
\right],
\quad b_i=(\bar q_i,c_i).
$$

$b_{-i}$ denotes other providers' offers; $\mathcal B_i$ is the allowed set of
offers. This requires assumptions about competitors, price/quantity bounds,
information, uncertainty and participation. It is a proposed later model,
not an equation supplied by Week 3. With only four providers, price-taking
should be presented as a benchmark assumption rather than an established fact.

$K_i$ and $T_i$ alone cannot determine a price in EUR. They may influence the
cost of response once an energy/cost model exists. Also, the quadratic cost
coefficient $R_j$ on Week 3 PDF page 9 belongs to the functional-incentive
follower model; it is not automatically the capacity bid $c_i$.

## 7. Proposed implementation order and checks

1. **Numerical prerequisite completed (2026-10-02).** The simulators now share
   an exact-endpoint output grid, non-finite parameters are rejected and final
   time labels follow the configuration. Regression tests cover the reviewed
   failures. See the [resolution record](FCR_REVIEW_2026-10-02.md).
2. **Specify provider capability, cost and price units.** Keep physical capacity,
   offered capacity, awarded capacity and delivered power distinct. Choose
   price-taking or strategic behaviour explicitly. Teaching costs are acceptable
   for a first example if clearly labelled.
3. **Implement the auction with fixed test bids.** Put allocation, pricing and
   capacity payments in a market module. Check the worked example, partial
   awards, ties, zero requirement, exact total capacity and insufficient supply.
4. **Implement provider decisions.** Put cost functions and offer construction
   in provider modules. Test nonparticipation when expected revenue cannot cover
   cost, the selected cost-to-bid rule, and sensitivity to opportunity costs.
5. **Connect awards to dynamics.** Set each provider's `reserve_mw` from its award
   by provider ID. Hold the award fixed during the event. Compare manual awards,
   fixed-bid awards and cost-derived awards under the same physical disturbance.
6. **Report physical and economic outcomes.** Show frequency, each response,
   offered/awarded reserve, clearing price, payments, true costs and profit.
   Use separate capacity and response records so that no capacity is paid twice.

Keep the accepted $K_i,T_i$ fixed when first connecting the auction: the award
changes the saturation limit. A common full-activation frequency would instead
imply a rule such as $K_i=q_i^\star/\Delta f_{\mathrm{full}}$; adopting that would
be a new control-policy decision and must not happen silently. A zero award
gives zero response from a provider initialized at $u_i=0$.

The proposed software separation is `providers` for capability, costs and
offers; `markets` for clearing and payment; the existing `grid` and provider
response models for physical derivatives; `simulation` for coupling them;
`metrics` and `plotting` for reporting. Only create new modules as they are used.

## Choices still open

The next discussion with the supervisor should settle the interpretation of
the provider decision (cost-based or strategic), the first cost/capability
model, price units, price forecast/information assumptions, reserve requirement,
and tie/shortage pricing rules. The four-hour product and the 40 s event must
remain separate time scales. The existing PJM preset can test the market
interface, but technology claims and realistic profits require additional data.
