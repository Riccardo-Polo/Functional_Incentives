# L2 control and settlement on separate clocks

## Current single-target controller, 22 November 2026

The user selected `l2_control_policy="single_target"`, now the simulator default.
At each 0.1 s tick choose one constant target over the remaining time h to the
next settlement. Apply it until the next control tick and recalculate. Reuse
`choose_l2_target`, which includes the exact inherited-response cost cross
term. With z(s) = a(s) z_k + b(s) v, the remaining cost is

    C(v) = rho/2 * [I_aa*z_k² + 2*I_ab*z_k*v + I_bb*v²]
    alpha = e + h*r - I_b*z_k/M
    beta = I_b/M
    v_free = -(lambda2*beta*alpha + rho*I_ab*z_k/2)
             /(lambda2*beta² + rho*I_bb/2)

Clip v_free to availability. The pure passive cost term does not depend on v;
the cross term does. Its inclusion accounts for future delivered-power cost
and adds no passive reimbursement. Settlement remains the realized endpoint
comparison using one full-state zero-target replay per window. Decisions
never use a future disturbance. Off-control settlement does not update a
command. The objective includes cost only until the next closure, including
when a held command straddles it.

Use `l2_control_policy="sequence"` explicitly for the historical formulation
described below. Earlier separate-clock studies, Pareto curves, budget matches
and input audits used that formulation and retain their saved results.

D-050 adopts this formulation as the reference for future FI L2 studies. Run
both settlement periods with 0.1 s control and endpoint-only payment, without
passive-cost compensation. Supply these settings explicitly to the solver.
Study scripts, hashes, episode records and numerical checks
are under `results/l2_separate_clocks/`; findings are in its `ANALYSIS.md`.

## Contract and physical evolution

Keep the global control clock at delta=0.1 s, starting at zero. Let settlement
windows be [t_j,t_j+H], with H=0.25 or 0.5 s, shortened only at the event horizon.
Start a full-state passive replay at the actual state at t_j. In this replay,
the FI target is zero throughout the entire window; inherited own response
decays, FCR responds to replay frequency, and realized disturbances match.
Carry the replay continuously across control ticks. At closure post only

    S_j = lambda2*(e_passive(t_j+H)^2 - e_actual(t_j+H)^2)
    C_j = integral_[t_j,t_j+H] rho*z_actual(t)^2/2 dt
    profit_j = S_j - C_j

Lambda2 remains EUR/Hz^2 per settlement. Settlement uses actual frequency
endpoints and actual delivered-power costs; no passive reimbursement or profit
floor is added. A zero target still has negative profit from inherited power.

## Causal provider problem

At each control time t, optimize a sequence of bounded held targets through
the next settlement closure. The target changes only at future control ticks.
The last planned piece may be shorter than 0.1 s. The predicted endpoint is

    e_end(v) = e(t) + (end-t)*(r(t)-z(t)/M_f)
               + integral_t^end z_predicted(s;v) ds / M_f

Other current balance contributions are frozen, as in the original predictor.
Actuator dynamics T*dz/dt=-z+v and their cost moments are propagated exactly.
Maximizing predicted remaining profit is equivalent to

    minimize_v lambda2*e_end(v)^2 + integral_t^end rho*z_predicted(s;v)^2/2 ds
    subject to |v_i| <= A

The passive endpoint and costs already incurred are constants in this decision.
The result is a convex quadratic problem, solved using bounded least squares.
Apply the first target, observe the system at the next tick, and replan. No
future disturbances, profile identity or future replay trajectory enter control.

The planning horizon ends at settlement; no terminal continuation value is
included. At H=0.25 s, the command chosen at 0.2 s persists across the 0.25 s
boundary until 0.3 s, although its plan considered only the first 0.05 s.
This is an explicit boundary limitation. Settlement alone never triggers a
control update. H=0.5 s aligns with the control ticks. Replanning does not imply
that the opening forecast is a forecast of the eventual realized sequence.

## Implementation and checks

The existing `simulate_fcr_power_step` accepts opt-in
`l2_settlement_interval_s=H` with `fi_decision_interval_s=0.1` and
`l2_contract="endpoint_only_v1"`. It shares the existing plant RHS, solver,
live cost and frequency-error ledgers; there is no separate physical model.
`choose_l2_window_targets` implements the bounded sequence optimization.
Episode settlement records contain all actual financial amounts and nested
causal decision records. Predicted-settlement trajectories are unavailable
(NaN), rather than summing overlapping remaining-window predictions.

Tests compare the single-action optimizer to the original formula, the sequence
optimizer to independent numerical quadrature/optimization, H=0.1 against the
original simulation, and full-window replay against independent FCR integration.
They check global ticks, off-control settlement, causality, target limits,
short final windows, ledger closure and numerical refinement. The experiment
adds matched-source validation/delivery comparisons and source preservation.

## Interpretation limits

This changes both the provider decision horizon and settlement definition;
it is more than postponing a sum of old transfers. Fixed lambda2 does not mean
fixed expenditure when H changes. Evaluation uses historical draws again for
a diagnostic comparison, not fresh validation of a policy selected afterward.
No new default, weight, budget policy, external cost calibration or participation
rule is selected. The accepted compensated contract remains separately available.
