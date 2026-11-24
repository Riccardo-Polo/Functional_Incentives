"""Power, incentive and matched-procurement diagnostics for the meeting study."""

from concurrent.futures import ProcessPoolExecutor
import json
from math import fsum
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from .experiment import load_block_providers
from .frequency import LinearFrequencyParameters
from .grid import AndesOperatingPoint
from .lyapunov import _csv, simulate_l2_candidate, summarize_l2_candidate
from .lyapunov_comparison import _draws, _job, clear_requirement
from .providers import first_order_response_integrals
from .simulation import clear_fcr_block, realize_fcr_block


def _inputs(output):
    protocol = json.loads((output / "protocol.json").read_text())
    locations = {Path(k).name: Path(k).parent for k in protocol["source_sha256"]}
    return protocol, locations["availability_draws.csv"], locations["delivery_draw.json"]


def _curve_job(args):
    base, plant, plan, p, draw, fi_sample, other_sample, coefficient = args
    trace = simulate_l2_candidate(realize_fcr_block(base, draw), plant, plan, p, "l2", coefficient)
    return dict(fi_sample=fi_sample, other_sample=other_sample, coefficient=coefficient,
                **summarize_l2_candidate(trace))


def _budget_trial(args):
    return _job(*args)


def run_curve_checks(output):
    """Intervene on the two sets of draws separately in the historical step case."""
    protocol, source, _ = _inputs(output)
    frozen = json.loads((source / "frozen_inputs.json").read_text())
    config, plan = frozen["effective_config"], frozen["plan"]
    point = AndesOperatingPoint(**json.loads((source / "operating_point.json").read_text()))
    plant = LinearFrequencyParameters(**json.loads((source / "frequency_parameters.json").read_text()))
    base = clear_fcr_block(load_block_providers(config, point), requirement_mw=config["market"]["requirement_mw"],
        nominal_frequency_hz=plant.nominal_frequency_hz, droop_pu=config["scenario"]["droop_pu"],
        minimum_bid_mw=config["market"]["minimum_bid_mw"],
        quantity_tolerance_mw=config["decision"].get("quantity_tolerance_mw", 1e-8))
    [identity] = [p.provider_id for p, role in zip(base.providers, base.participation_modes) if role == "fi"]
    draws = _draws(base, pd.read_csv(source / "availability_draws.csv"), "design")
    profile = next(p for p in plan["profiles"] if p["name"] == "sustained_deficit")
    jobs = []
    for fi_index, fi_draw in draws:
        for other_index, other_draw in draws:
            draw = {**other_draw, identity: fi_draw[identity]}
            weights = [30000]
            if fi_index == other_index:
                weights += [500, 1000, 5000, 15000, 20000]
            jobs.extend((base, plant, plan, profile, draw, fi_index, other_index, w) for w in weights)
    with ProcessPoolExecutor(protocol["settings"]["workers"]) as pool:
        rows = list(pool.map(_curve_job, jobs))
        # Resolve the interior of each price segment in the sustained reference
        # case, where checking only the segment endpoints can miss a crossing.
        if "sustained_deficit" in protocol["settings"]["profiles"]:
            ramp = next(p for p in protocol["profiles"] if p["name"] == "sustained_deficit")
            quantities = []
            bounds = [0.]
            for offer in sorted(base.market.offers, key=lambda o: (o.price_eur_per_mw_block, o.provider_id)):
                if offer.quantity_mw > 0:
                    quantities.append(offer.quantity_mw)
                    bounds.append(min(base.market.requirement_mw, fsum(quantities)))
            extra = []
            bounds = sorted(set(bounds))
            for left, right in zip(bounds[:-1], bounds[1:]):
                for q in np.linspace(left, right, 5)[1:-1]:
                    cleared = clear_requirement(base, plant, config, float(q))
                    extra.extend((cleared, plant, plan, ramp, "l2", protocol["selection"], draw,
                                  "design", i, output) for i, draw in draws)
            _csv(output / "sustained_interior_search.csv", list(pool.map(_budget_trial, extra)))
    _csv(output / "step_curve_checks.csv", rows)


def _input_audit(path, weight, mass, tau, rho):
    intervals = pd.read_csv(path / "intervals.csv")
    h = intervals.end_time_s - intervals.start_time_s
    moments = np.array([first_order_response_integrals(tau, t) for t in h])
    b = moments[:, 1] / mass
    aa, ab, bb = moments[:, 2:].T
    quadratic = rho * bb / 2 + weight * b**2
    scalar = -weight * b * intervals.predicted_passive_endpoint_hz / quadratic
    memory = -rho * ab * intervals.initial_response_mw / (2 * quadratic)
    np.testing.assert_allclose(scalar + memory, intervals.unconstrained_target_mw, atol=2e-10)
    np.testing.assert_allclose(np.clip(scalar + memory, -intervals.availability_mw, intervals.availability_mw),
                               intervals.target_mw, atol=2e-10)
    profit_zero = -rho * aa * intervals.initial_response_mw**2 / 2
    if "passive_cost_reimbursement_eur" in intervals:
        profit_zero += intervals.passive_cost_reimbursement_eur
    gain = intervals.predicted_profit_eur - profit_zero
    if gain.min() < -1e-8:
        raise ValueError("L2 predicted profit below feasible zero target")
    return intervals.assign(scalar_target_mw=scalar, inherited_cost_correction_mw=memory,
        zero_target_profit_eur=profit_zero, predicted_gain_vs_zero_eur=gain,
        realized_gain_vs_zero_eur=intervals.profit_eur-profit_zero)


def _power_plots(output, profile, policies, weight, mass, tau, rho):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1 + len(policies), 1, figsize=(9, 2 + 2.25*len(policies)), sharex=True)
    for ax, policy in zip(axes[1:], policies):
        path = output / "delivery" / profile / policy
        frame = pd.read_csv(path / "trajectory.csv")
        for field in frame.filter(regex="_response_mw$"):
            ax.plot(frame.time_s, frame[field], label=field.removesuffix("_response_mw").replace("_", " "))
        ax.set_ylabel("Response [MW]")
        ax.set_title({"fcr_base": "FCR only", "l2": "FCR + L2", "bilinear": "FCR + bilinear"}[policy], loc="left")
        ax.grid(alpha=.22)
        if policy == "l2":
            audit = _input_audit(path, weight, mass, tau, rho)
            audit.to_csv(path / "input_audit.csv", index=False)
            fig2, (top, lower) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
            for key, label in (("scalar_target_mw", "Eq. (22), mapped scalar target"),
                               ("unconstrained_target_mw", "Target with actuator memory")):
                top.stairs(audit[key], np.r_[audit.start_time_s, audit.end_time_s.iloc[-1]],
                           baseline=None, label=label)
            lower.stairs(audit.target_mw, np.r_[audit.start_time_s, audit.end_time_s.iloc[-1]],
                         baseline=None, label="Feasible held command")
            lower.plot(frame.time_s, frame[audit.provider_id.iloc[0]+"_response_mw"], label="Delivered power")
            for sign in (1, -1):
                lower.axhline(sign*audit.availability_mw.iloc[0], linestyle=":", color=".35",
                              label="Availability bounds" if sign == 1 else None)
            top.set_title(profile.replace("_", " ").capitalize() + " | L2 delivery")
            for panel in (top, lower):
                panel.set_ylabel("Power [MW]")
                panel.legend()
                panel.grid(alpha=.22)
            lower.set_xlabel("Time [s]")
            fig2.tight_layout()
            fig2.savefig(output / ("l2_input_"+profile+".png"), dpi=160)
            plt.close(fig2)
    axes[0].plot(frame.time_s, frame.power_deficit_mw, color=".2")
    axes[0].set_ylabel("Net deficit [MW]")
    axes[0].set_title(profile.replace("_", " ").capitalize() + " | common delivery draw", loc="left")
    axes[-1].set_xlabel("Time [s]")
    axes[-1].legend(loc="upper center", bbox_to_anchor=(.5,-.28), ncol=3)
    fig.tight_layout()
    fig.savefig(output / ("provider_power_"+profile+".png"), dpi=160, bbox_inches="tight")
    plt.close(fig)


def _halfwidth(s):
    return student_t.ppf(.975, len(s)-1)*s.std(ddof=1)/np.sqrt(len(s))


def _curve_report(output, source):
    import matplotlib.pyplot as plt
    old = pd.read_csv(source / "design_samples.csv").query("mechanism == 'l2'")
    steps = old.query("profile == 'sustained_deficit'").copy()
    checks = pd.read_csv(output / "step_curve_checks.csv")
    diagonal = checks.query("fi_sample == other_sample").rename(columns={"fi_sample": "sample"})
    keys = ["frequency_error_hz2_s", "target_bound_time_fraction", "net_payment_eur",
            "delivered_bound_time_fraction", "rocof_max_abs_hz_per_s"]
    def ordered(frame):
        return frame.sort_values("sample")[keys].values
    np.testing.assert_allclose(ordered(diagonal.query("coefficient == 30000")),
                               ordered(steps.query("coefficient == 30000")), atol=1e-8)
    data = pd.concat([steps, diagonal.query("coefficient != 30000")], ignore_index=True)
    stats = data.groupby("coefficient")[keys].mean()
    stats["error_ci95_halfwidth"] = data.groupby("coefficient").frequency_error_hz2_s.apply(_halfwidth)
    stats.to_csv(output / "sustained_curve_summary.csv")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for i, g in data.groupby("sample"):
        g = g.sort_values("coefficient")
        axes[0].plot(g.coefficient, g.frequency_error_hz2_s, alpha=.65, marker=".", label=f"Draw {i+1}")
    axes[0].plot(stats.index, stats.frequency_error_hz2_s, color="black", label="Four-draw mean")
    axes[0].set_ylabel("Integrated squared frequency error [Hz² s]")
    axes[1].plot(stats.index, stats.target_bound_time_fraction*100, marker="o", label="Target at bound")
    axes[1].plot(stats.index, stats.delivered_bound_time_fraction*100, marker=".", label="Delivery at bound")
    axes[1].set_ylabel("Share of 40 s horizon [%]")
    for ax in axes:
        ax.set_xscale("symlog", linthresh=1000)
        ax.set_xlabel("L2 weight [EUR/Hz² per interval]")
        ax.grid(alpha=.22)
        ax.legend()
    fig.tight_layout()
    fig.savefig(output / "step_saturation.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.errorbar(stats.net_payment_eur, stats.frequency_error_hz2_s, yerr=stats.error_ci95_halfwidth,
                fmt="o-", capsize=3, color="#2a5981", ms=4)
    for w, offset in ((3000, (8, 6)), (10000, (3, 9)), (30000, (8, -16)), (1000000, (-50, 10))):
        ax.annotate(f"λ₂={w:g}", (stats.net_payment_eur[w], stats.frequency_error_hz2_s[w]),
                    xytext=offset, textcoords="offset points", fontsize=9)
    ax.set_xscale("symlog", linthresh=10)
    ax.set_xlabel("Mean signed FI payment [EUR/event]")
    ax.set_ylabel("Mean integrated squared frequency error [Hz² s]")
    ax.set_title("10 MW step | four paired draws, pointwise 95% intervals")
    ax.grid(alpha=.22)
    fig.tight_layout()
    fig.savefig(output / "step_payment_performance.png", dpi=160)
    plt.close(fig)
    cross = checks.query("coefficient == 30000")
    matrix = cross.pivot(index="fi_sample", columns="other_sample", values="frequency_error_hz2_s")
    total = np.var(matrix.values)
    fi_share = np.var(matrix.mean(axis=1)) / total
    other_share = np.var(matrix.mean(axis=0)) / total
    lines = ["## Availability and the historical Pareto curves", "",
        "The historical step study used four paired joint availability draws. Its error bars are pointwise "
        "95% Student-t intervals for the mean. The design mixture averages profiles within each draw first. "
        "The table resolves the original sustained 10 MW case with five additional intermediate weights on the same draws.", "",
        "| L2 weight | Mean error [Hz² s] | 95% half-width | Target at bound [%] | Delivery at bound [%] | Mean event transfer [EUR] | Mean peak absolute RoCoF [Hz/s] |",
        "|---:|---:|---:|---:|---:|---:|---:|"]
    for w, r in stats.iterrows():
        lines.append(f"| {w:g} | {r.frequency_error_hz2_s:.6f} | {r.error_ci95_halfwidth:.6f} | "
            f"{100*r.target_bound_time_fraction:.2f} | {100*r.delivered_bound_time_fraction:.2f} | "
            f"{r.net_payment_eur:.2f} | {r.rocof_max_abs_hz_per_s:.5f} |")
    lines.extend(["", "![L2 error and target saturation](step_saturation.png)", "",
        "![L2 payment and frequency error with intermediate weights](step_payment_performance.png)", "",
        f"At weight 30000, crossing each of the four FI availabilities with each of the four other-provider "
        f"draw vectors yields 16 deterministic simulations. In this equally weighted finite grid, variation "
        f"of the conditional mean across FI availability explains {100*fi_share:.3f}% of the empirical "
        f"squared-error variance. The other-provider main effect accounts for {100*other_share:.3f}%, "
        f"and interaction accounts for {100*(1-fi_share-other_share):.3f}%. These fractions apply to this "
        "profile, coefficient and finite grid. They do not estimate population shares. The four original "
        "diagonal outcomes reproduce to numerical precision.", "",
        "Once the command is pinned to each draw's own availability throughout the disturbance, a higher "
        "weight can increase the transfer on the same response. The separation between those four "
        "trajectories persists. During recovery or reversal the bound can release, so the result depends on the profile. "
        "Target saturation uses a 1e-9 MW tolerance. Delivered saturation uses 1e-6 MW and grid quadrature.", ""])
    return lines


def run_budget_report(output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    protocol, source, comparison = _inputs(output)
    frozen = json.loads((source / "frozen_inputs.json").read_text())
    [fi_id] = [name for name, role in json.loads((source / "market.json").read_text())["roles"].items()
               if role == "fi"]
    provider = next(p for p in frozen["effective_config"]["providers"] if p["provider_id"] == fi_id)
    tau, rho = provider["time_constant_s"], provider["fi_rho_eur_per_mw2_s"]
    plant = LinearFrequencyParameters(**json.loads((source / "frequency_parameters.json").read_text()))
    weight = protocol["selection"]["lambda2"]
    args = (weight, plant.frequency_mass_mw_s_per_hz, tau, rho)
    matches = json.loads((output / "frozen_demands.json").read_text())
    data = pd.read_csv(output / "outcomes.csv")
    data["block_plus_event_eur"] = data.fcr_block_payment_eur + data.net_payment_eur
    budget = protocol["reference_budget_eur"]
    records, paired = [], []
    metrics = ["frequency_error_hz2_s", "nadir_sampled_hz", "zenith_sampled_hz",
               "final_absolute_error_hz", "rocof_max_abs_hz_per_s", "net_payment_eur",
               "activation_cost_eur", "profit_eur", "block_plus_event_eur"]
    for (profile, policy), group in data.query("bank == 'validation'").groupby(["profile", "policy"]):
        group = group.set_index("sample").sort_index()
        ref = data.query("bank == 'validation' and profile == @profile and policy == 'fcr_base'").set_index("sample").sort_index()
        if not group.index.equals(ref.index) or not group.index.is_unique or len(group) != frozen["plan"]["validation_samples"]:
            raise ValueError("incomplete or unpaired validation bank")
        row = dict(profile=profile, policy=policy, n=len(group), requirement_mw=group.fcr_requirement_mw.iloc[0],
                   fcr_eur=group.fcr_block_payment_eur.iloc[0])
        for key in metrics:
            row[key] = group[key].mean()
            row[key+"_ci95_halfwidth"] = _halfwidth(group[key])
            delta = group[key] - ref[key]
            paired.append(dict(profile=profile, policy=policy, metric=key, mean_difference=delta.mean(),
                               ci95_halfwidth=_halfwidth(delta)))
        records.append(row)
    summary = pd.DataFrame(records)
    _csv(output / "validation_summary.csv", records)
    _csv(output / "paired_validation.csv", paired)
    hist = pd.read_csv(comparison / "outcomes.csv").query(
        "bank == 'validation' and policy == 'l2' and profile == 'moderate_sustained'")
    audit = _input_audit(comparison / "delivery" / "moderate_sustained" / "l2", *args)
    audit.to_csv(output / "historical_moderate_profit_audit.csv", index=False)
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for key, label in (("net_settlement_eur", "Actual transfers"), ("activation_cost_eur", "Actual costs"),
                       ("profit_eur", "Actual profit")):
        top.plot(audit.end_time_s, audit[key].cumsum(), label=label)
    top.axhline(0, color=".4", ls=":", label="Zero policy from rest")
    top.set_ylabel("Cumulative amount [EUR]")
    top.set_title("5 MW step | historical delivery, 12 MW FCR")
    bottom.plot(audit.end_time_s, audit.profit_eur, label="Profit at chosen target")
    bottom.plot(audit.end_time_s, audit.zero_target_profit_eur, ls="--", label="Zero target from same state")
    bottom.set_ylabel("Profit per interval [EUR]")
    bottom.set_xlabel("Time [s]")
    for panel in (top, bottom):
        panel.grid(alpha=.22)
        panel.legend()
    fig.tight_layout()
    fig.savefig(output / "historical_profit.png", dpi=160)
    plt.close(fig)
    last = audit.iloc[-1]
    lines = ["# Provider power, L2 profit and replacement of FCR at a fixed budget", "",
        "## Why a profit-maximizing decision can lose money", "",
        f"In the original 5 MW step (60 s, 12 MW FCR, weight {weight:g}), the {len(hist)} independent "
        f"validation draws give mean signed payment {hist.net_payment_eur.mean():.2f} EUR, "
        f"activation cost {hist.activation_cost_eur.mean():.2f} EUR and provider profit "
        f"{hist.profit_eur.mean():.2f} EUR. Losses occur in {(hist.profit_eur < 0).sum()} draws. "
        f"Mean charges are {hist.gross_charges_eur.mean():.4f} EUR. The forecast payment is "
        f"{hist.predicted_payment_eur.mean():.2f} EUR. Replacing forecast with replay changes mean "
        f"profit by {hist.payment_prediction_bias_eur.mean():.2f} EUR. The existing controller already "
        "predicts a loss when its interval forecasts are added along the delivery.", "",
        "At rest, choosing zero for the entire event is feasible and gives zero transfer and zero cost. "
        "The implemented best response chooses a held target for the next 0.1 s, conditional on its current "
        "delivered power z. After activation, setting the target to zero gives zero replay transfer and "
        "z(t) = z_k exp(-t/T). That interval's passive profit is −ρ z_k² T (1−exp(−2h/T))/4. The provider "
        "can prefer an action to this passive interval even if both lose money. Each decision also leaves "
        "a response state whose future costs lie outside the decision horizon. Summing these local "
        "comparisons compares different inherited states at each tick. It gives no event-level participation guarantee.", "",
        f"In the separate historical delivery, the interval {last.start_time_s:g}–{last.end_time_s:g} s "
        f"starts at {last.initial_response_mw:.3f} MW. The selected target is {last.target_mw:.3f} MW "
        f"with {last.availability_mw:.3f} MW available. Its predicted profit is {last.predicted_profit_eur:.4f} EUR, "
        f"its settled profit is {last.profit_eur:.4f} EUR, and the zero-target profit from that same state is "
        f"{last.zero_target_profit_eur:.4f} EUR. Predicted gain over zero was nonnegative on every tick "
        f"(minimum {audit.predicted_gain_vs_zero_eur.min():.3g} EUR). This loss occurs below the availability limit.", "",
        "![Event finances and interval zero-target comparison](historical_profit.png)", "",
        "## Comparing the provider input with Jonas Matt's scalar solution", "",
        "Section 3.1.2, Proposition 1, equation (22) of *Functional Incentives for Control* gives "
        "u* = −λ b δ/(R+λ b²) with scalar performance weight m=1, δ=ax−x_ref and stage cost Ru². "
        "For an interval-by-interval comparison in MW, evaluate that expression along the simulated "
        "trajectory with δ=α (predicted passive error), b=I_b/M_f and R=ρ I_bb/2 (target-cost curvature). "
        "These are local comparison coefficients. The scalar curve is a reference evaluated on the same "
        "observations, not a separately simulated scalar power system.", "",
        "The delivered-power cost adds a cross term ρ I_ab z_k v. Consequently the adapted free target "
        "equals u* − ρ I_ab z_k/[2(R+λ b²)]. The implementation clips that target to [−A,A], then the "
        "actuator delivers T dz/dt = v−z. Before clipping the target is linear in the observed state "
        "at a fixed interval length. Clipping, FCR limits and held decisions affect the closed loop. "
        "The input audits reconstruct both targets independently and compare them at every recorded tick.", ""]
    lines.extend(_curve_report(output, source))
    lines.extend(["## Reference-budget comparison with ramped events", "",
        f"The 12 MW FCR-only reference clears at {budget/12:g} EUR/MW for the entire four-hour block "
        f"and pays {budget:g} EUR. Each profile below is a separate conditional, one-event-per-block "
        "experiment. Each commanded change is spread over "
        f"{protocol['settings']['disturbance_rise_s']:g} s by a causal rectangular moving average of the net deficit. "
        "This is an illustrative physical input shared by plant and replay. Frequency and RoCoF are observed "
        "as before. A separate sensor filter would answer a different question.", "",
        "At every candidate demand, re-clear the original offers, freeze awards and roles, simulate both "
        "the event payment and frequency on the same four design draws, and solve "
        "P_FCR(q) + mean S_FI(q) = P_FCR(12). Gamma=50 and lambda2=30000 are frozen. Search downward "
        "within merit-order segments, checking both sides of supply boundaries. Payment can be nonmonotone "
        "within a segment. An unbracketed target is reported as unresolved. Root uniqueness is not assumed.", "",
        "| Scenario | Policy | FCR [MW] | FCR [EUR/block] | Design FI [EUR/event] | Design total [EUR] |",
        "|---|---|---:|---:|---:|---:|"])
    search = []
    for f in (output / "search").glob("*/*/*.csv"):
        r = pd.read_csv(f)
        search.append(dict(profile=r.profile.iloc[0], policy=r.policy.iloc[0],
            q=float(r.fcr_requirement_mw.iloc[0]), fcr=float(r.fcr_block_payment_eur.iloc[0]),
            fi=r.net_payment_eur.mean(), total=r.fcr_block_payment_eur.iloc[0]+r.net_payment_eur.mean()))
    trials = pd.DataFrame(search).sort_values(["profile", "policy", "q"])
    trials.to_csv(output / "budget_search.csv", index=False)
    for m in matches:
        name, policy = m["profile"], m["policy"]
        g = trials.query("profile == @name and policy == @policy")
        if m["status"] == "matched":
            r = g.loc[(g.q - m["requirement_mw"]).abs().idxmin()]
            if abs(r.total-budget) > protocol["settings"]["budget_tolerance_eur"]:
                raise ValueError("design match exceeds tolerance")
            lines.append(f"| {name} | {policy} | {r.q:.5f} | {r.fcr:.2f} | {r.fi:.2f} | {r.total:.2f} |")
        else:
            if name == "sustained_deficit" and policy == "l2":
                samples = pd.read_csv(output / "sustained_interior_search.csv")
                totals = (samples.fcr_block_payment_eur + samples.net_payment_eur).groupby(samples.fcr_requirement_mw).mean()
                tested = pd.concat([g.set_index("q").total, totals])
                closest = tested.loc[(tested-budget).abs().idxmin()]
                lines.append(f"| {name} | {policy} | No verified match | — | — | closest tested {closest:.2f} |")
                continue
            lines.append(f"| {name} | {policy} | No verified match | — | — | closest tested {budget+m['closest_gap_eur']:.2f} |")
    lines.extend(["", "## Independent validation", "",
        "Means on the 16 paired draws after procurement freezes. Financial half-widths are pointwise "
        "95% Student-t intervals. Peak absolute RoCoF uses each trajectory's maximum absolute unfiltered "
        "model RHS on the output grid including ramp corners and event limits, with no measurement window. "
        "Report the mean of these individual peaks over the full 40 s (10 MW sustained and short pulse) "
        "or 60 s (5 MW sustained). Integrated squared error is a live ledger. The CSV tables also give "
        "zenith, terminal error and paired differences with half-widths against FCR only.", "",
        "| Scenario | Policy | Total [EUR/block with one event] ± 95% HW | FI profit [EUR/event] | Error [Hz² s] | Nadir [Hz] | Mean peak absolute RoCoF [Hz/s] |",
        "|---|---|---:|---:|---:|---:|---:|"])
    for r in summary.itertuples():
        lines.append(f"| {r.profile} | {r.policy} | {r.block_plus_event_eur:.2f} ± "
            f"{r.block_plus_event_eur_ci95_halfwidth:.2f} | {r.profit_eur:.2f} | "
            f"{r.frequency_error_hz2_s:.6f} | {r.nadir_sampled_hz:.5f} | {r.rocof_max_abs_hz_per_s:.5f} |")
    lines.extend(["", "## Delivery examples", "",
        "Each panel shows the common third-stream draw. Provider inputs are delivered active-power "
        "deviations from the solved operating point in MW. The L2 panels separate the mapped theoretical "
        "target, the free target with actuator memory, the held target and physical delivery.", ""])
    for name, group in summary.groupby("profile"):
        policies = [policy for policy in ("fcr_base", "bilinear", "l2") if policy in set(group.policy)]
        _power_plots(output, name, policies, *args)
        lines.append(f"![Provider power, {name}](provider_power_{name}.png)")
        if "l2" in policies:
            lines.append(f"![Theoretical and delivered L2 power, {name}](l2_input_{name}.png)")
        lines.append("")
    checks = pd.read_csv(output / "refinement.csv")
    lines.extend(["## Numerical checks", "",
        f"All {len(checks)} delivery comparisons passed with half the solver and output steps and tenfold "
        f"tighter solver tolerances. Largest common-time frequency difference "
        f"{checks.common_frequency_max_difference_hz.max():.2e} Hz, peak absolute RoCoF difference "
        f"{checks.rocof_max_abs_hz_per_s_difference.max():.2e} Hz/s, net-payment difference "
        f"{checks.net_payment_eur_difference.max():.2e} EUR."])
    (output / "ANALYSIS.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
