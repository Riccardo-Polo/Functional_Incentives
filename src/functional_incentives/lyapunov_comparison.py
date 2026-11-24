"""Frozen L2/bilinear policies, independent events and equal-budget diagnostics.

All plant integrations and economic decisions use the maintained modules.
Procurement matches design expected event transfers on whole-block accounting;
no future availability or validation payoff enters policy or demand selection.
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from math import fsum
from pathlib import Path
import re
from time import perf_counter, process_time
import tomllib

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from .lyapunov import (_json, _csv, _tree_hashes, _save_trace, simulate_l2_candidate,
                       summarize_l2_candidate)
from .metrics import frequency_performance_metrics, empirical_pareto_flags
from .providers import FIProviderParameters, FCRProviderParameters
from .simulation import clear_fcr_block, realize_fcr_block, power_deficit_events


METRICS = {
    "frequency_error_hz2_s": ("Integrated squared error [Hz² s]", False),
    "nadir_sampled_hz": ("Nadir [Hz]", True),
    "zenith_sampled_hz": ("Zenith [Hz]", False),
    "peak_absolute_error_hz": ("Peak absolute frequency error [Hz]", False),
    "final_absolute_error_hz": ("Terminal absolute error [Hz]", False),
    "rocof_max_abs_hz_per_s": ("Peak absolute RoCoF [Hz/s]", False),
    "rms_frequency_error_hz": ("RMS frequency error [Hz]", False),
}
FINANCIAL = ("net_payment_eur", "gross_rewards_eur", "gross_charges_eur",
             "activation_cost_eur", "profit_eur")
KEYS = ["bank", "profile", "policy", "sample"]


def validate_comparison_settings(settings, source_plan):
    if set(settings) != {"source_study", "workers", "delivery_seed", "events_per_block", "selection", "demonstrations"}:
        raise ValueError("comparison manifest has missing or unknown fields")
    for key, minimum in (("workers", 1), ("delivery_seed", 0)):
        if type(settings[key]) is not int or settings[key] < minimum:
            raise ValueError(f"invalid comparison {key}")
    counts = settings["events_per_block"]
    if not counts or len(set(counts)) != len(counts) or any(type(n) is not int or n < 1 for n in counts):
        raise ValueError("events_per_block must contain distinct positive integer accounting scenarios")
    s = settings["selection"]
    if (set(s) != {"lambda2", "gamma", "reason"} or not str(s["reason"]).strip()
            or s["lambda2"] not in source_plan["lambda2_values"] or s["gamma"] not in source_plan["gamma_values"]):
        raise ValueError("freeze tested lambda2/gamma and an explicit TSO reason")
    names = {p["name"] for p in source_plan["profiles"]}
    if not settings["demonstrations"]:
        raise ValueError("at least one new demonstration is required")
    for p in settings["demonstrations"]:
        if set(p) != {"name", "label", "purpose", "start_time_s", "power_deficit_mw", "changes", "final_time_s"}:
            raise ValueError("invalid demonstration fields")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", p["name"]) or p["name"] in names:
            raise ValueError("demonstration names must be unique and separate from design profiles")
        names.add(p["name"])
        if (not np.isfinite(p["final_time_s"]) or not 0 <= p["start_time_s"] < p["final_time_s"]
                or not np.isfinite(p["power_deficit_mw"]) or p["power_deficit_mw"] < 0):
            raise ValueError("invalid demonstration horizon or initial deficit")
        power_deficit_events(p["power_deficit_mw"], p["start_time_s"], p["final_time_s"], p["changes"])


def expand_metrics(frame, nominal, horizon):
    frame = frame.copy()
    frame["peak_absolute_error_hz"] = np.maximum(abs(frame.nadir_sampled_hz - nominal),
                                                 abs(frame.zenith_sampled_hz - nominal))
    frame["rms_frequency_error_hz"] = np.sqrt(frame.frequency_error_hz2_s / horizon)
    return frame


def summarize_groups(samples, keys):
    rows = []
    for key, group in samples.groupby(keys):
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, key, strict=True), n=len(group))
        for metric in (*METRICS, *FINANCIAL):
            if metric not in group:
                continue
            row[metric] = float(group[metric].mean())
            row[metric+"_ci95_halfwidth"] = float(student_t.ppf(.975, len(group)-1)
                * group[metric].std(ddof=1) / np.sqrt(len(group))) if len(group) > 1 else 0.
        rows.append(row)
    return pd.DataFrame(rows)


def add_design_mixture(frame, profiles, group_keys):
    fields = [f for f in (*METRICS, *FINANCIAL) if f in frame]
    mix = frame.copy()
    mix[fields] = mix[fields].mul(mix.profile.map({p["name"]: p["weight"] for p in profiles}), axis=0)
    mix = mix.groupby(group_keys, as_index=False)[fields].sum()
    mix["profile"] = "design_mixture"
    return pd.concat([frame, mix], ignore_index=True)


def clear_requirement(base, plant, config, requirement):
    """Recompute original offers and clear only a changed demand."""
    if config["decision"].get("mode", "optimize") != "optimize" or config["market"].get("mode", "clear") != "clear":
        raise ValueError("equal-budget study requires endogenous offers and ordinary clearing")
    return clear_fcr_block(base.providers, requirement_mw=requirement,
        nominal_frequency_hz=plant.nominal_frequency_hz, droop_pu=config["scenario"]["droop_pu"],
        minimum_bid_mw=config["market"]["minimum_bid_mw"],
        quantity_tolerance_mw=config["decision"].get("quantity_tolerance_mw", 1e-8))


def match_budget(base, plant, config, target_payment):
    """Invert actual uniform-price procurement, retaining jumps and supply limits.

    Awards are divisible, while bids keep the original minimum quantity.
    Candidate demands are verified by fresh offer decisions and clearing.
    """
    maximum = fsum(d.offer.quantity_mw for d in base.decisions)
    cap = clear_requirement(base, plant, config, maximum)
    minimum = base.market.requirement_mw
    result = dict(target_block_payment_eur=float(target_payment), maximum_requirement_mw=maximum,
        maximum_block_payment_eur=cap.market.total_payment_eur, requirement_mw=None,
        matched_block_payment_eur=None, design_budget_gap_eur=None)
    if target_payment < base.market.total_payment_eur - 1e-8:
        return {**result, "status": "below_reference_budget"}, None
    if target_payment > cap.market.total_payment_eur + 1e-8:
        return {**result, "status": "insufficient_offer_supply"}, None
    prices = sorted({d.offer.price_eur_per_mw_block for d in base.decisions if d.offer.quantity_mw > 0})
    candidates = {minimum, maximum}
    candidates.update(target_payment / p for p in prices if p > 0)
    for requirement in sorted(candidates):
        if minimum - 1e-10 <= requirement <= maximum + 1e-10:
            cleared = clear_requirement(base, plant, config, min(maximum, max(minimum, requirement)))
            gap = cleared.market.total_payment_eur - target_payment
            if abs(gap) <= 1e-7:
                return {**result, "status": "matched", "requirement_mw": cleared.market.requirement_mw,
                    "matched_block_payment_eur": cleared.market.total_payment_eur,
                    "design_budget_gap_eur": gap}, cleared
    return {**result, "status": "uniform_price_gap"}, None


def comparison_trace(block, plant, plan, profile, policy, selection, *, refined=False):
    options = dict(plan, final_time_s=profile["final_time_s"])
    if policy.startswith("fcr_"):
        # Original zero-award FI eligibility is disabled only as a controller.
        # Its online inertia remains unchanged, with zero FCR reserve/response.
        block = replace(block, response_parameters=tuple(
            FCRProviderParameters(p.provider_id, 0., p.time_constant_s, 0.)
            if isinstance(p, FIProviderParameters) else p for p in block.response_parameters))
        mechanism, coefficient = "bilinear", 0.
    else:
        mechanism = policy
        coefficient = selection["lambda2"] if policy == "l2" else selection["gamma"]
    return simulate_l2_candidate(block, plant, options, profile, mechanism, coefficient, refined=refined)


def comparison_summary(trace):
    if trace.fi is not None or trace.l2 is not None:
        values = summarize_l2_candidate(trace)
    else:
        for array in (trace.provider_response_mw, trace.provider_requested_response_mw):
            if not np.all(np.isfinite(array)) or np.any(abs(array) > trace.provider_reserve_mw + 1e-8):
                raise RuntimeError("FCR comparator violates finite response bounds")
        values = {**frequency_performance_metrics(trace), "zenith_sampled_hz": float(trace.frequency_hz.max()),
            "terminal_error_hz": float(trace.delta_frequency_hz[-1]),
            "frequency_error_hz2_s": float(trace.integrated_squared_frequency_error_hz2_s[-1]),
            **{f: 0. for f in FINANCIAL}}
    values["peak_absolute_error_hz"] = float(np.max(abs(trace.delta_frequency_hz)))
    values["rms_frequency_error_hz"] = float(np.sqrt(values["frequency_error_hz2_s"] / trace.time_s[-1]))
    return values


def save_comparison_trace(trace, directory):
    if trace.fi is not None or trace.l2 is not None:
        _save_trace(trace, directory)
        return
    directory.mkdir(parents=True, exist_ok=True)
    table = dict(time_s=trace.time_s, frequency_hz=trace.frequency_hz,
        delta_frequency_hz=trace.delta_frequency_hz, rocof_hz_per_s=trace.rocof_hz_per_s,
        power_deficit_mw=trace.power_deficit_mw, net_payment_eur=np.zeros(len(trace.time_s)),
        activation_cost_eur=np.zeros(len(trace.time_s)))
    for j, identity in enumerate(trace.provider_ids):
        table[identity+"_response_mw"] = trace.provider_response_mw[:, j]
        table[identity+"_target_mw"] = trace.provider_requested_response_mw[:, j]
    pd.DataFrame(table).to_csv(directory / "trajectory.csv", index=False)
    pd.DataFrame(trace.disturbance_limits).to_csv(directory / "disturbance_limits.csv", index=False)


def _job(cleared, plant, plan, profile, policy, selection, draw, bank, sample, output):
    wall, cpu = perf_counter(), process_time()
    block = realize_fcr_block(cleared, draw)
    trace = comparison_trace(block, plant, plan, profile, policy, selection)
    result = comparison_summary(trace)
    if bank == "delivery":
        save_comparison_trace(trace, output / "delivery" / profile["name"] / policy)
    return dict(bank=bank, profile=profile["name"], policy=policy, sample=sample,
        horizon_s=profile["final_time_s"], fcr_requirement_mw=cleared.market.requirement_mw,
        fcr_block_payment_eur=cleared.market.total_payment_eur, **result,
        wall_s=perf_counter()-wall, cpu_s=process_time()-cpu)


def _run_jobs(executor, jobs, records, output):
    done = {tuple(r[k] for k in KEYS) for r in records}
    if len(done) != len(records):
        raise ValueError("duplicate cached comparison outcomes")
    pending = []
    for args in jobs:
        profile, policy, bank, sample = args[3], args[4], args[7], args[8]
        if (bank, profile["name"], policy, sample) not in done:
            pending.append(executor.submit(_job, *args))
    for future in as_completed(pending):
        records.append(future.result())
        _csv(output / "outcomes.csv", records)
        if len(records) % 32 == 0:
            print(f"Frozen-policy comparison: {len(records)} completed outcomes", flush=True)


def _draws(cleared, banks, bank):
    return [(int(r["sample"]), {p.provider_id: r[p.provider_id] for p in cleared.providers})
            for r in banks[banks.bank == bank].to_dict("records")]


def run_comparison(manifest, output):
    from .experiment import prepare_experiment
    started = perf_counter()
    settings = tomllib.loads(manifest.read_text(encoding="utf-8"))
    source = (manifest.parent / settings["source_study"]).resolve()
    if source == output.resolve() or source in output.resolve().parents or output.resolve() in source.parents:
        raise ValueError("comparison must use a separate, non-nested output directory")
    frozen = json.loads((source / "frozen_inputs.json").read_text())
    source_plan, config = frozen["plan"], frozen["effective_config"]
    validate_comparison_settings(settings, source_plan)
    if not json.loads((source / "study_status.json").read_text()).get("design_complete"):
        raise ValueError("complete the source design before selecting policies")
    for name, digest in frozen["source_sha256"].items():
        if name not in ("lyapunov.py", "experiment.py") and hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"source physical/economic implementation changed: {name}")
    point, base, plant = prepare_experiment(config)
    if json.loads((source / "frequency_parameters.json").read_text()) != asdict(plant):
        raise ValueError("source operating point/physical parameters changed")
    original_market = json.loads((source / "market.json").read_text())
    if [asdict(a) for a in base.market.awards] != original_market["awards"]:
        raise ValueError("reference awards changed")
    output.mkdir(parents=True, exist_ok=True)
    protocol = dict(settings=settings, source_directory=str(source), source_files_sha256=_tree_hashes(source),
        effective_config=config, source_plan=source_plan,
        code_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")})
    path = output / "protocol.json"
    if path.exists() and json.loads(path.read_text()) != protocol:
        raise ValueError("comparison inputs, source or implementation changed; use a new directory")
    _json(path, protocol)
    _json(output / "operating_point.json", asdict(point))
    _json(output / "frequency_parameters.json", asdict(plant))
    source_samples = pd.read_csv(source / "design_samples.csv")
    expanded = expand_metrics(source_samples, plant.nominal_frequency_hz, source_plan["final_time_s"])
    expanded = add_design_mixture(expanded, source_plan["profiles"], ["mechanism", "coefficient", "sample"])
    summary = summarize_groups(expanded, ["profile", "mechanism", "coefficient"])
    for _, group in summary.groupby(["profile", "mechanism"]):
        for metric, (_, maximize) in METRICS.items():
            summary.loc[group.index, "empirical_frontier_"+metric] = empirical_pareto_flags(
                group.net_payment_eur, group[metric], maximize=maximize)
    summary.to_csv(output / "pareto_summary.csv", index=False)
    selection = dict(settings["selection"])
    mixture = summary[summary.profile == "design_mixture"]
    captures = {}
    for mechanism, coefficient in (("bilinear", selection["gamma"]), ("l2", selection["lambda2"])):
        group = mixture[mixture.mechanism == mechanism].set_index("coefficient")
        zero = group.loc[0, "frequency_error_hz2_s"]
        captures[mechanism] = float((zero - group.loc[coefficient, "frequency_error_hz2_s"])
            / (zero - group.frequency_error_hz2_s.min()))
    selected = dict(**selection, captured_design_error_reduction=captures,
        source_design_sha256=hashlib.sha256((source / "design_samples.csv").read_bytes()).hexdigest(),
        selection_method="explicit illustrative TSO choice from design Pareto curves; no weighted search")
    if (output / "selection.json").exists() and json.loads((output / "selection.json").read_text()) != selected:
        raise ValueError("policy changed after selection; validation cannot be reused for selection")
    _json(output / "selection.json", selected)
    from .plotting import plot_policy_pareto
    plot_policy_pareto(output, summary, selection, source_plan["profiles"], source_plan["final_time_s"])
    profiles = [dict(p, final_time_s=source_plan["final_time_s"]) for p in source_plan["profiles"]] + settings["demonstrations"]
    banks = pd.read_csv(source / "availability_draws.csv")
    design, validation = _draws(base, banks, "design"), _draws(base, banks, "validation")
    records = pd.read_csv(output / "outcomes.csv").to_dict("records") if (output / "outcomes.csv").exists() else []
    # Import completed design outcomes by reference, not copies of saved runs.
    existing = {tuple(r[k] for k in KEYS) for r in records}
    imported = False
    for policy, mechanism, coefficient in (("fcr_base", "bilinear", 0),
        ("bilinear", "bilinear", selection["gamma"]), ("l2", "l2", selection["lambda2"])):
        for row in source_samples[(source_samples.mechanism == mechanism) & (source_samples.coefficient == coefficient)].to_dict("records"):
            key = ("design", row["profile"], policy, row["sample"])
            if key in existing:
                continue
            row = expand_metrics(pd.DataFrame([row]), plant.nominal_frequency_hz, source_plan["final_time_s"]).iloc[0].to_dict()
            records.append(dict(row, bank="design", policy=policy, horizon_s=source_plan["final_time_s"],
                fcr_requirement_mw=base.market.requirement_mw, fcr_block_payment_eur=base.market.total_payment_eur,
                outcome_source="preserved source design", wall_s=0., cpu_s=0.))
            existing.add(key)
            imported = True
    if imported or not (output / "outcomes.csv").exists():
        _csv(output / "outcomes.csv", records)
    _json(output / "study_status.json", dict(status="running", policies_frozen=True))
    with ProcessPoolExecutor(max_workers=settings["workers"]) as executor:
        jobs = [(base, plant, source_plan, p, policy, selection, draw, "design", sample, output)
            for p in settings["demonstrations"] for policy in ("fcr_base", "bilinear", "l2") for sample, draw in design]
        _run_jobs(executor, jobs, records, output)
        design_data = pd.DataFrame(records)
        matches, cleared_policies = [], {}
        for p in profiles:
            for mechanism in ("bilinear", "l2"):
                sample = design_data[(design_data.bank == "design") & (design_data.profile == p["name"])
                                     & (design_data.policy == mechanism)]
                if len(sample) != len(design):
                    raise ValueError("budget selection requires a complete design bank")
                event_transfer = float(sample.net_payment_eur.mean())
                for count in settings["events_per_block"]:
                    matched, cleared = match_budget(base, plant, config, base.market.total_payment_eur + count*event_transfer)
                    policy = f"fcr_equal_{mechanism}_n{count}"
                    matches.append(dict(profile=p["name"], comparator=mechanism, policy=policy,
                        events_per_block=count, design_fi_event_payment_eur=event_transfer,
                        baseline_block_payment_eur=base.market.total_payment_eur,
                        maximum_events_from_design_mean=(matched["maximum_block_payment_eur"]-base.market.total_payment_eur)/event_transfer
                            if event_transfer > 0 else None, **matched))
                    if cleared is not None:
                        cleared_policies[(p["name"], policy)] = cleared
        _csv(output / "budget_matches.csv", matches)
        _json(output / "procurement_markets.json", {f"{p}/{policy}": dict(
            offers=[asdict(d) for d in b.decisions], awards=[asdict(a) for a in b.market.awards],
            clearing_price_eur_per_mw_block=b.market.clearing_price_eur_per_mw_block)
            for (p, policy), b in cleared_policies.items()})
        # Budgets/demands freeze before any independent outcome is evaluated.
        rng = np.random.default_rng(np.random.SeedSequence(settings["delivery_seed"]))
        delivery = [(0, {p.provider_id: p.availability.sample_mw(rng) for p in base.providers})]
        _json(output / "delivery_draw.json", dict(seed=settings["delivery_seed"], availability_mw=delivery[0][1]))
        jobs = [(base, plant, source_plan, p, policy, selection, draw, bank, sample, output)
            for bank, draws in (("validation", validation), ("delivery", delivery)) for p in profiles
            for policy in ("fcr_base", "bilinear", "l2") for sample, draw in draws]
        for p in profiles:
            for (name, policy), cleared in cleared_policies.items():
                if name == p["name"]:
                    jobs.extend((cleared, plant, source_plan, p, policy, selection, draw, bank, sample, output)
                        for bank, draws in (("design", design), ("validation", validation), ("delivery", delivery))
                        for sample, draw in draws)
        _run_jobs(executor, jobs, records, output)
    checks_path = output / "numerical_checks.csv"
    checks = pd.read_csv(checks_path).to_dict("records") if checks_path.exists() else []
    check_done = {(r["profile"], r["policy"]) for r in checks}
    for p in settings["demonstrations"][-2:]:
        for policy in ("fcr_base", "bilinear", "l2"):
            if (p["name"], policy) in check_done:
                continue
            block = realize_fcr_block(base, delivery[0][1])
            a = comparison_trace(block, plant, source_plan, p, policy, selection)
            b = comparison_trace(block, plant, source_plan, p, policy, selection, refined=True)
            checks.append(dict(profile=p["name"], policy=policy, **check_refinement(a, b)))
            _csv(checks_path, checks)
    for p in settings["demonstrations"][1:3]:
        policy = "fcr_equal_l2_n1"
        if (p["name"], policy) in cleared_policies and (p["name"], policy) not in check_done:
            block = realize_fcr_block(cleared_policies[(p["name"], policy)], delivery[0][1])
            a = comparison_trace(block, plant, source_plan, p, policy, selection)
            b = comparison_trace(block, plant, source_plan, p, policy, selection, refined=True)
            checks.append(dict(profile=p["name"], policy=policy, **check_refinement(a, b)))
            _csv(checks_path, checks)
    if _tree_hashes(source) != protocol["source_files_sha256"]:
        raise RuntimeError("preserved source study changed")
    _json(output / "study_status.json", dict(status="complete", policies_frozen=True,
        outcome_rows=len(records), numerical_comparisons=len(checks), last_invocation_wall_s=perf_counter()-started,
        budget_accounting="conditional events per existing FCR block; no event-rate forecast adopted"))
    run_comparison_report(output)


def check_refinement(a, b):
    x, y = comparison_summary(a), comparison_summary(b)
    limits = dict(nadir_sampled_hz=1e-4, zenith_sampled_hz=1e-4, final_absolute_error_hz=1e-6,
        rocof_max_abs_hz_per_s=1e-4, frequency_error_hz2_s=1e-7, net_payment_eur=1e-5, activation_cost_eur=1e-5)
    differences = {name+"_difference": abs(x[name]-y[name]) for name in limits}
    if any(differences[name+"_difference"] > limit for name, limit in limits.items()):
        raise RuntimeError(f"comparison refinement failed: {differences}")
    differences["common_frequency_max_difference_hz"] = float(np.max(abs(a.frequency_hz
        - np.interp(a.time_s, b.time_s, b.frequency_hz))))
    responses = np.column_stack([np.interp(a.time_s, b.time_s, b.provider_response_mw[:, j])
                                for j in range(len(a.provider_ids))])
    differences["common_response_max_difference_mw"] = float(np.max(abs(a.provider_response_mw-responses)))
    if differences["common_frequency_max_difference_hz"] > 1e-6 or differences["common_response_max_difference_mw"] > 1e-5:
        raise RuntimeError("comparison trajectory refinement failed")
    return differences


def run_comparison_report(output):
    protocol = json.loads((output / "protocol.json").read_text())
    source = Path(protocol["source_directory"])
    if _tree_hashes(source) != protocol["source_files_sha256"]:
        raise ValueError("preserved comparison source changed")
    settings, plan = protocol["settings"], protocol["source_plan"]
    data = pd.read_csv(output / "outcomes.csv")
    matches = pd.read_csv(output / "budget_matches.csv")
    all_profiles = [p["name"] for p in plan["profiles"]] + [p["name"] for p in settings["demonstrations"]]
    expected = {(bank, p, policy, sample)
        for bank, n in (("design", plan["design_samples"]), ("validation", plan["validation_samples"]), ("delivery", 1))
        for p in all_profiles for policy in ("fcr_base", "bilinear", "l2") for sample in range(n)}
    for row in matches[matches.status == "matched"].itertuples():
        expected.update((bank, row.profile, row.policy, sample)
            for bank, n in (("design", plan["design_samples"]), ("validation", plan["validation_samples"]), ("delivery", 1))
            for sample in range(n))
    actual = set(data[KEYS].itertuples(index=False, name=None))
    if data.duplicated(KEYS).any() or actual != expected:
        raise ValueError("comparison requires every planned paired outcome exactly once")
    summary = summarize_groups(data, ["bank", "profile", "policy"])
    summary.to_csv(output / "performance_summary.csv", index=False)
    # Paired differences use complete common independent availability draws.
    pairs = []
    for profile, group in data[data.bank == "validation"].groupby("profile"):
        baseline = group[group.policy == "fcr_base"].set_index("sample")
        for policy, values in group.groupby("policy"):
            values = values.set_index("sample")
            if not values.index.sort_values().equals(baseline.index.sort_values()):
                raise ValueError("incomplete independent paired comparison")
            for field in (*METRICS, *FINANCIAL):
                delta = values[field] - baseline[field]
                pairs.append(dict(profile=profile, policy=policy, metric=field, n=len(delta),
                    mean_difference=float(delta.mean()), ci95_halfwidth=float(student_t.ppf(.975, len(delta)-1)
                        * delta.std(ddof=1)/np.sqrt(len(delta)))))
    _csv(output / "validation_paired_differences.csv", pairs)
    budget_rows = []
    for match in matches[matches.status == "matched"].to_dict("records"):
        for bank in ("design", "validation", "delivery"):
            subset = data[(data.bank == bank) & (data.profile == match["profile"])]
            fi = subset[subset.policy == match["comparator"]].set_index("sample")
            fcr = subset[subset.policy == match["policy"]].set_index("sample")
            if not fi.index.sort_values().equals(fcr.index.sort_values()):
                raise ValueError("incomplete paired equal-budget comparison")
            gap = fcr.fcr_block_payment_eur - (fi.fcr_block_payment_eur + match["events_per_block"]*fi.net_payment_eur)
            row = dict(profile=match["profile"], comparator=match["comparator"], events_per_block=match["events_per_block"],
                bank=bank, requirement_mw=match["requirement_mw"], n=len(gap), mean_budget_gap_eur=float(gap.mean()),
                budget_gap_ci95_halfwidth=float(student_t.ppf(.975, len(gap)-1)*gap.std(ddof=1)/np.sqrt(len(gap)))
                    if len(gap) > 1 else 0.)
            for field in METRICS:
                delta = fcr[field] - fi[field]
                row[field+"_difference"] = float(delta.mean())
                row[field+"_difference_ci95_halfwidth"] = float(student_t.ppf(.975, len(delta)-1)*delta.std(ddof=1)
                    /np.sqrt(len(delta))) if len(delta) > 1 else 0.
            budget_rows.append(row)
    _csv(output / "equal_budget_comparison.csv", budget_rows)
    from .plotting import plot_frozen_comparison, plot_policy_pareto
    plot_policy_pareto(output, pd.read_csv(output / "pareto_summary.csv"), settings["selection"],
                       plan["profiles"], plan["final_time_s"])
    plot_frozen_comparison(output, protocol, summary, matches)
    selection = json.loads((output / "selection.json").read_text())
    roles = json.loads((source / "market.json").read_text())["roles"]
    provider = next(p for p in protocol["effective_config"]["providers"] if roles[p["provider_id"]] == "fi")
    lines = ["# Frozen-policy L2, bilinear FI and FCR comparison", "",
        "## Selection and scope", "", f"Source design preserved in place: {source.as_posix()}.",
        f"Selected lambda2={selection['lambda2']:g} EUR/Hz^2 per interval; gamma={selection['gamma']:g} EUR/(Hz MW s).",
        selection["reason"], "",
        "The selection and procurement demands freeze before independent outcomes. No weighted TSO",
        "search, frequency-security constraint or holdout-based refinement is introduced. Selection is",
        "illustrative and conditional on the four-draw design, not a population optimum or an equilibrium.",
        "The L2 provider has current RoCoF/model-mass information in addition to the bilinear information set.",
        f"Predicted and realized settlement remain distinct. Costs retain rho={provider['fi_rho_eur_per_mw2_s']:g};",
        f"underlying FI availability variance remains {provider['variance_mw2']:g} MW^2. Private event profit can",
        "be negative on unseen profiles under the myopic held-target rule.", "",
        "## Cases and independent evaluation", "",
        "The four original 40 s profiles are evaluated on 16 independent saved validation draws.",
        "Four additional unseen profiles assess different amplitudes, pulse duration, symmetric reversal",
        "and stepped deficits. These profiles do not select or refine lambda2/gamma.", ""]
    for p in settings["demonstrations"]:
        lines.append(f"- {p['label']}: {p['power_deficit_mw']:g} MW at {p['start_time_s']:g} s; "
                     f"later absolute levels {p['changes']}; horizon {p['final_time_s']:g} s. {p['purpose']}.")
    lines += ["", f"A third availability stream, seed {settings['delivery_seed']}, supplies one common delivery draw.",
        "Delivery plots are single-draw illustrations, separate from independent assessment statistics.",
        "States/availability/clocks do not reset at disturbance changes. Events are not announced to providers.", "",
        "## Frequency metrics and Pareto diagnostics", "",
        "Additional metric-specific design plots show nadir, zenith, peak absolute error, terminal absolute",
        "error, integrated squared error, RMS error and peak absolute RoCoF versus signed event transfers.",
        "Peak absolute error uses each trajectory's extrema before averaging. RMS=sqrt(E_f/horizon);",
        "for a fixed horizon it is a re-expression of the same squared-error integral, not new independent evidence.",
        "RoCoF is the unfiltered physical RHS in Hz/s, with exact one-sided event limits and no averaging",
        "window. Its statistic is the maximum absolute value over each complete 40/60 s trajectory.",
        "Intervals are pointwise Student-t 95%. The four-draw Pareto curves are exploratory; raw empirical",
        "frontier flags do not resolve plateaus or establish population dominance. Paired validation",
        "differences retain common-draw covariance; no winner is chosen on validation.", "",
        "## Equal-budget accounting", "",
        "The existing FCR block is four hours and its 12 MW baseline payment is 288 EUR/block.",
        "This duration is a saved scenario convention, not a newly asserted market rule. FI transfers",
        "cover the simulated event only. They are never prorated or extrapolated automatically.",
        "For a conditional N events of one profile per block, the design target is:", "",
        "    P_FCR(q_equal) = P_FCR(12 MW) + N * mean_design(S_FI)", "",
        f"Accounting scenarios N={settings['events_per_block']} are sensitivities, not an adopted event-rate forecast.",
        "Each profile is an alternative conditional event mix; profiles are not all charged within the same block.",
        "N changes accounting only: this does not simulate repeated market cycles, state resets or restoration between events.",
        "FCR offers are recomputed with unchanged laws, economics and the 1 MW positive bid minimum.",
        "Awards remain divisible; uniform clearing can create unattainable price gaps. Supply is not extended.",
        "A rejected FCR-only resource has zero response while keeping unchanged online inertia.",
        "Demand matches expected spending exactly on the design bank. It is frozen before availability",
        "realizes; independent and delivery FI payments vary, so their realized budget gaps are reported",
        "with uncertainty. Exact ex-post equality would require future-outcome information and is not claimed.",
        "The report distinguishes matched, insufficient-supply and uniform-price-gap cases. No provider",
        "or reserve expansion, scarcity price, altered contract or hypothetical residual subsidy is imposed.",
        "Activation costs and FCR reserve-shortfall penalties are separate from the procurement/event-transfer",
        "budget. FCR activation costs are absent in the maintained market convention; zero FI cost in an",
        "FCR-only row does not mean zero total social resource cost.", "",
        "## Independent performance summary", "",
        "| Profile | Policy | E_f [Hz^2 s] | Nadir [Hz] | Peak absolute RoCoF [Hz/s] | FI net [EUR/event] | FI profit [EUR/event] |",
        "|---|---|---:|---:|---:|---:|---:|"]
    for row in summary[(summary.bank == "validation") & summary.policy.isin(["fcr_base", "bilinear", "l2"])].itertuples():
        lines.append(f"| {row.profile} | {row.policy} | {row.frequency_error_hz2_s:.7f} | {row.nadir_sampled_hz:.6f} | "
            f"{row.rocof_max_abs_hz_per_s:.7f} | {row.net_payment_eur:.4f} | {row.profit_eur:.4f} |")
    independent = summary[summary.bank == "validation"].set_index(["profile", "policy"])
    equal = pd.DataFrame(budget_rows)
    lines += ["", "## Conditional N=1 equal-budget outcomes", "",
        "Each FI mechanism is paired with its own FCR-only procurement, not one common budget for all three controls.",
        "The budget column is exact in design expectation; the independent spending gap is FCR-only minus FCR+FI.",
        "Its 95% interval concerns the mean gap, not a prediction interval for repeated-event block dynamics.", "",
        "| Profile | FI contract | Design budget [EUR/block] | FCR-only MW | Independent budget gap [EUR/block, mean +/- 95% halfwidth] |",
        "|---|---|---:|---:|---:|"]
    for match in matches[(matches.status == "matched") & (matches.events_per_block == 1)].itertuples():
        gap = equal[(equal.bank == "validation") & (equal.profile == match.profile)
                    & (equal.comparator == match.comparator) & (equal.events_per_block == 1)].iloc[0]
        lines.append(f"| {match.profile} | {match.comparator} | {match.target_block_payment_eur:.4f} | "
            f"{match.requirement_mw:.6f} | {gap.mean_budget_gap_eur:.4f} +/- {gap.budget_gap_ci95_halfwidth:.4f} |")
    lines += ["", "| Profile | FI contract | FI E_f [Hz^2 s] | Matched FCR E_f [Hz^2 s] | FI peak |RoCoF| [Hz/s] | Matched FCR peak |RoCoF| [Hz/s] |",
              "|---|---|---:|---:|---:|---:|"]
    # Avoid Markdown column separators inside the metric name.
    lines[-2] = lines[-2].replace("peak |RoCoF|", "peak absolute RoCoF")
    for match in matches[(matches.status == "matched") & (matches.events_per_block == 1)].itertuples():
        fi = independent.loc[(match.profile, match.comparator)]
        fcr = independent.loc[(match.profile, match.policy)]
        lines.append(f"| {match.profile} | {match.comparator} | {fi.frequency_error_hz2_s:.7f} | "
            f"{fcr.frequency_error_hz2_s:.7f} | {fi.rocof_max_abs_hz_per_s:.7f} | {fcr.rocof_max_abs_hz_per_s:.7f} |")
    lines += ["", "Budget feasibility by conditional event count:", "",
              "| Events per block N | Matched profile/contract cases | Supply-limited cases |",
              "|---:|---:|---:|"]
    for count, group in matches.groupby("events_per_block"):
        lines.append(f"| {count} | {int((group.status == 'matched').sum())} | "
                     f"{int((group.status == 'insufficient_offer_supply').sum())} |")
    lines += ["", "## Findings to retain", "",
        "On the short pulse, both FI policies reduce mean squared error against their respective",
        "matched-budget FCR controls on independent draws. The RoCoF comparison is separate; FI can",
        "have higher peaks. On the bipolar doublet, more slow FCR can improve nadir while increasing",
        "integrated error through the later response. More procurement does not guarantee improvement in every metric.",
        "Selected coefficients stay frozen after these observations. L2 is not uniformly profitable on unseen cases:", "",
        "| Unseen profile with negative mean FI profit | Policy | Profit [EUR/event, mean +/- 95% halfwidth] |",
        "|---|---|---:|"]
    unseen = {p["name"] for p in settings["demonstrations"]}
    negative = summary[(summary.bank == "validation") & summary.profile.isin(unseen)
        & summary.policy.isin(["bilinear", "l2"]) & (summary.profit_eur < 0)]
    for row in negative.itertuples():
        lines.append(f"| {row.profile} | {row.policy} | {row.profit_eur:.4f} +/- {row.profit_eur_ci95_halfwidth:.4f} |")
    lines += ["", "This reveals a limitation of the myopic dynamic adaptation and assigned participation.",
        "The study retains this observation without refitting coefficients on the holdout, changing",
        "costs or asserting provider participation/equilibrium. Financial viability remains a separate research question."]
    imported = int(data.outcome_source.eq("preserved source design").sum()) if "outcome_source" in data else 0
    checks = pd.read_csv(output / "numerical_checks.csv")
    lines += ["", "## Execution counts", "",
        f"Saved outcomes: {len(data)}; {imported} imported scalar design outcomes reference the preserved source.",
        f"New episode integrations: {len(data)-imported}, plus {2*len(checks)} integrations for {len(checks)} refinement comparisons.",
        "Availability vectors are shared across policies/cases; outcome-row counts are not independent sample counts."]
    lines += ["", "Full confidence intervals are in performance_summary.csv. Budget feasibility, design",
        "target and supply ceilings are in budget_matches.csv. Equal-budget paired performance and",
        "independent spending residuals are in equal_budget_comparison.csv. These conditional budgets",
        "do not establish commercial profitability or an equally optimized comparison of all controls.", "",
        "## Verification", "",
        "Numerical checks halve solver/output steps and tighten tolerances tenfold for the two last",
        "unseen profiles under each base policy and the L2-matched FCR pulse/doublet comparators.",
        "Limits are 1e-4 Hz sampled extrema, 1e-6 Hz terminal error, 1e-4 Hz/s RoCoF, 1e-7 Hz^2 s",
        "integrated error and 1e-5 EUR transfers/cost. Frozen source data are checked by SHA-256.",
        "Report-only regeneration uses saved outcomes and performs no simulation. Existing studies",
        "remain unchanged. This finite-horizon experiment does not prove Lyapunov stability.", ""]
    (output / "ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")
