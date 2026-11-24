"""FCR-demand sensitivity with frozen FI policies and paired availability banks.

This module orchestrates the maintained market and plant, without introducing
new physics. Expanded reserve availability is an explicit scenario assumption.
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from math import fsum
from pathlib import Path
from time import perf_counter
import tomllib

import numpy as np
import pandas as pd
from scipy.stats import truncnorm, t as student_t

from .lyapunov import _csv, _json, _tree_hashes
from .lyapunov_comparison import (METRICS, FINANCIAL, clear_requirement,
    match_budget, comparison_trace, comparison_summary, save_comparison_trace,
    check_refinement, summarize_groups)
from .simulation import realize_fcr_block


KEYS = ["reference_requirement_mw", "bank", "profile", "policy", "sample"]
BASE_POLICIES = ("fcr_base", "bilinear", "l2")


def validate_capacity_settings(settings, config):
    expected = {"source_comparison", "fleet_label", "requirements_mw", "workers",
                "events_per_block", "provider_overrides"}
    if set(settings) != expected or not str(settings["fleet_label"]).strip():
        raise ValueError("capacity manifest has missing or unknown fields")
    values = settings["requirements_mw"]
    if (not values or values[0] != config["market"]["requirement_mw"] or values != sorted(set(values))
            or any(not np.isfinite(q) or q < config["market"]["requirement_mw"] for q in values)):
        raise ValueError("requirements must be distinct ascending finite values at or above the reference")
    if type(settings["workers"]) is not int or settings["workers"] < 1:
        raise ValueError("workers must be a positive integer")
    if type(settings["events_per_block"]) is not int or settings["events_per_block"] < 1:
        raise ValueError("events_per_block is an explicit positive conditional accounting count")
    by_id = {p["provider_id"]: p for p in config["providers"]}
    for name, changes in settings["provider_overrides"].items():
        if (name not in by_id or by_id[name]["technology"] != "conventional"
                or set(changes) != {"reserve_capacity_mw", "mean_mw"}
                or not all(np.isfinite(x) for x in changes.values())
                or changes["reserve_capacity_mw"] < by_id[name]["reserve_capacity_mw"]
                or not 0 <= changes["mean_mw"] <= changes["reserve_capacity_mw"]):
            raise ValueError("only explicit conventional capacity/mean increases are allowed")


def map_capacity_banks(source, source_base, target_base):
    """Pair changed truncated laws by CDF/PPF; preserve unchanged draws exactly."""
    if [p.provider_id for p in source_base.providers] != [p.provider_id for p in target_base.providers]:
        raise ValueError("capacity scenarios must retain provider identities and order")
    rows, diagnostics = [], []
    for row in source.to_dict("records"):
        mapped = dict(row)
        for old, new in zip(source_base.providers, target_base.providers, strict=True):
            a, b = old.availability, new.availability
            x = float(row[old.provider_id])
            if a == b:
                continue
            if (a.boundary_mode != "truncated" or b.boundary_mode != "truncated"
                    or a.variance_mw2 <= 0 or b.variance_mw2 <= 0):
                raise ValueError("changed laws require continuous nondegenerate truncation")
            s, t = np.sqrt(a.variance_mw2), np.sqrt(b.variance_mw2)
            q = float(truncnorm.cdf(x, -a.mean_mw/s, (a.capacity_mw-a.mean_mw)/s,
                                   loc=a.mean_mw, scale=s))
            if not 0 < q < 1:
                raise ValueError("saved draw does not have an interior bounded-law quantile")
            mapped[new.provider_id] = float(truncnorm.ppf(q, -b.mean_mw/t,
                (b.capacity_mw-b.mean_mw)/t, loc=b.mean_mw, scale=t))
            diagnostics.append(dict(bank=row["bank"], sample=row["sample"],
                provider_id=new.provider_id, quantile=q, source_availability_mw=x,
                availability_mw=mapped[new.provider_id]))
        rows.append(mapped)
    return pd.DataFrame(rows), diagnostics


def capacity_markets(base, plant, config, requirements):
    """Screen supply before sampling, retaining every requested demand."""
    maximum = fsum(d.offer.quantity_mw for d in base.decisions)
    rows, blocks, awards = [], {}, []
    for q in requirements:
        row = dict(reference_requirement_mw=q, maximum_offer_supply_mw=maximum,
                   status="insufficient_offer_supply", fcr_block_payment_eur=None,
                   clearing_price_eur_per_mw_block=None)
        if q <= maximum + 1e-10:
            block = clear_requirement(base, plant, config, q)
            blocks[q] = block
            row.update(status="feasible", fcr_block_payment_eur=block.market.total_payment_eur,
                       clearing_price_eur_per_mw_block=block.market.clearing_price_eur_per_mw_block)
            for a, p, mode in zip(block.market.awards, block.providers, block.participation_modes, strict=True):
                awards.append(dict(reference_requirement_mw=q, **asdict(a), role=mode,
                    offered_mw=next(d.offer.quantity_mw for d in block.decisions
                                    if d.offer.provider_id == p.provider_id)))
        rows.append(row)
    return rows, blocks, awards


def _capacity_job(cleared, plant, plan, profile, policy, selection, draw, bank, sample, q, output):
    started = perf_counter()
    trace = comparison_trace(realize_fcr_block(cleared, draw), plant, plan, profile, policy, selection)
    result = comparison_summary(trace)
    if bank == "delivery":
        save_comparison_trace(trace, output / "delivery" / f"q{q:g}" / profile["name"] / policy)
    return dict(reference_requirement_mw=q, bank=bank, profile=profile["name"], policy=policy,
        sample=sample, horizon_s=profile["final_time_s"],
        fcr_requirement_mw=cleared.market.requirement_mw,
        fcr_block_payment_eur=cleared.market.total_payment_eur, **result,
        outcome_source="new integration", wall_s=perf_counter()-started)


def _run_capacity_jobs(executor, jobs, records, output):
    done = {tuple(r[k] for k in KEYS) for r in records}
    if len(done) != len(records):
        raise ValueError("duplicate cached capacity outcomes")
    futures = []
    for args in jobs:
        key = (args[9], args[7], args[3]["name"], args[4], args[8])
        if key not in done:
            futures.append(executor.submit(_capacity_job, *args))
    try:
        for future in as_completed(futures):
            records.append(future.result())
            if len(records) % 16 == 0:
                _csv(output / "outcomes.csv", records)
            if len(records) % 128 == 0:
                print(f"Capacity sensitivity: {len(records)} outcomes", flush=True)
    finally:
        if records:
            _csv(output / "outcomes.csv", records)


def run_capacity_study(manifest, output):
    from .experiment import prepare_experiment
    started = perf_counter()
    settings = tomllib.loads(manifest.read_text(encoding="utf-8"))
    source = (manifest.parent / settings["source_comparison"]).resolve()
    if source == output.resolve() or source in output.resolve().parents or output.resolve() in source.parents:
        raise ValueError("capacity study must use a separate, non-nested output directory")
    previous = json.loads((source / "protocol.json").read_text())
    if json.loads((source / "study_status.json").read_text())["status"] != "complete":
        raise ValueError("capacity study requires a complete frozen-policy comparison")
    for name, digest in previous["code_sha256"].items():
        if name not in ("experiment.py", "plotting.py"):
            current = hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            if current != digest:
                raise ValueError(f"source scientific implementation changed: {name}")
    original_config = previous["effective_config"]
    validate_capacity_settings(settings, original_config)
    point, original_base, plant = prepare_experiment(original_config)
    if asdict(plant) != json.loads((source / "frequency_parameters.json").read_text()):
        raise ValueError("source physical parameters changed")
    config = deepcopy(original_config)
    for raw in config["providers"]:
        raw.update(settings["provider_overrides"].get(raw["provider_id"], {}))
    changed_point, base, changed_plant = prepare_experiment(config)
    if asdict(changed_point) != asdict(point) or asdict(changed_plant) != asdict(plant):
        raise ValueError("reserve sensitivity changed the operating point or online inertia")
    if original_base.gains_mw_per_hz != base.gains_mw_per_hz:
        raise ValueError("reserve sensitivity changed droop gains")
    # Offers and roles resolve before mapping or observing availability draws.
    markets, blocks, awards = capacity_markets(base, plant, config, settings["requirements_mw"])
    output.mkdir(parents=True, exist_ok=True)
    profiles = [dict(p, final_time_s=previous["source_plan"]["final_time_s"])
                for p in previous["source_plan"]["profiles"]] + previous["settings"]["demonstrations"]
    protocol = dict(settings=settings, source_directory=str(source),
        source_files_sha256=_tree_hashes(source), effective_config=config,
        source_plan=previous["source_plan"], profiles=profiles, selection=previous["settings"]["selection"],
        code_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")},
        rocof_reporting="maximum absolute unfiltered physical RHS over each full trajectory; exact event limits; no averaging; no time-series plots")
    path = output / "protocol.json"
    if path.exists() and json.loads(path.read_text()) != protocol:
        raise ValueError("capacity inputs/source/implementation changed; use a new directory")
    _json(path, protocol)
    _json(output / "operating_point.json", asdict(point))
    _json(output / "frequency_parameters.json", asdict(plant))
    _csv(output / "market_screen.csv", markets)
    _csv(output / "provider_awards.csv", awards)
    _json(output / "offers.json", [asdict(d) for d in base.decisions])
    _json(output / "selection.json", dict(protocol["selection"],
        policy_scope="frozen source coefficients at every requirement; no reselection"))
    source_banks = pd.read_csv(Path(previous["source_directory"]) / "availability_draws.csv")
    delivery = json.loads((source / "delivery_draw.json").read_text())["availability_mw"]
    source_banks = pd.concat([source_banks, pd.DataFrame([dict(bank="delivery", sample=0, **delivery)])], ignore_index=True)
    mapped, quantiles = map_capacity_banks(source_banks, original_base, base)
    mapped.to_csv(output / "availability_draws.csv", index=False)
    if quantiles:
        _csv(output / "availability_mapping.csv", quantiles)
    draws = {bank: [(int(r["sample"]), {p.provider_id: r[p.provider_id] for p in base.providers})
        for r in mapped[mapped.bank == bank].to_dict("records")]
        for bank in ("design", "validation", "delivery")}
    records = pd.read_csv(output / "outcomes.csv").to_dict("records") if (output / "outcomes.csv").exists() else []
    existing = {tuple(r[k] for k in KEYS) for r in records}
    if not settings["provider_overrides"] and original_base.market.requirement_mw in blocks:
        # Reuse scalar outcomes in place; do not rerun the preserved q=12 study.
        q = original_base.market.requirement_mw
        imported = pd.read_csv(source / "outcomes.csv")
        for r in imported[imported.policy.isin(BASE_POLICIES)].to_dict("records"):
            key = (q, r["bank"], r["profile"], r["policy"], r["sample"])
            if key not in existing:
                records.append(dict(r, reference_requirement_mw=q,
                    outcome_source="preserved source scalar outcome", wall_s=0.))
                existing.add(key)
    _json(output / "study_status.json", dict(status="running", stage="design", policies_frozen=True))
    selection, plan = protocol["selection"], protocol["source_plan"]
    matches, comparator_blocks = [], {}
    with ProcessPoolExecutor(max_workers=settings["workers"]) as executor:
        jobs = [(cleared, plant, plan, p, policy, selection, draw, "design", sample, q, output)
            for q, cleared in blocks.items() for p in profiles for policy in BASE_POLICIES
            for sample, draw in draws["design"]]
        _run_capacity_jobs(executor, jobs, records, output)
        data = pd.DataFrame(records)
        for q, cleared in blocks.items():
            for p in profiles:
                for mechanism in ("bilinear", "l2"):
                    group = data[(data.reference_requirement_mw == q) & (data.bank == "design")
                        & (data.profile == p["name"]) & (data.policy == mechanism)]
                    if len(group) != len(draws["design"]):
                        raise ValueError("budget demands require complete design outcomes")
                    transfer = float(group.net_payment_eur.mean())
                    match, comparator = match_budget(cleared, plant, config,
                        cleared.market.total_payment_eur + settings["events_per_block"]*transfer)
                    policy = f"fcr_equal_{mechanism}_n{settings['events_per_block']}"
                    matches.append(dict(reference_requirement_mw=q, profile=p["name"], comparator=mechanism,
                        policy=policy, events_per_block=settings["events_per_block"],
                        design_fi_event_payment_eur=transfer,
                        baseline_block_payment_eur=cleared.market.total_payment_eur, **match))
                    if comparator is not None:
                        comparator_blocks[(q, p["name"], policy)] = comparator
        _csv(output / "budget_matches.csv", matches)
        _json(output / "procurement_markets.json", {f"q{q:g}/{p}/{policy}": dict(
            offers=[asdict(d) for d in block.decisions], awards=[asdict(a) for a in block.market.awards],
            clearing_price_eur_per_mw_block=block.market.clearing_price_eur_per_mw_block)
            for (q, p, policy), block in comparator_blocks.items()})
        _json(output / "study_status.json", dict(status="running", stage="independent evaluation",
            policies_frozen=True, procurement_frozen=True, design_outcomes=sum(r["bank"] == "design" for r in records)))
        jobs = [(cleared, plant, plan, p, policy, selection, draw, bank, sample, q, output)
            for q, cleared in blocks.items() for p in profiles for policy in BASE_POLICIES
            for bank in ("validation", "delivery") for sample, draw in draws[bank]]
        for (q, name, policy), cleared in comparator_blocks.items():
            p = next(p for p in profiles if p["name"] == name)
            jobs.extend((cleared, plant, plan, p, policy, selection, draw, bank, sample, q, output)
                for bank in ("design", "validation", "delivery") for sample, draw in draws[bank])
        _run_capacity_jobs(executor, jobs, records, output)
    checks_path = output / "numerical_checks.csv"
    checks = pd.read_csv(checks_path).to_dict("records") if checks_path.exists() else []
    checked = {(r["reference_requirement_mw"], r["profile"], r["policy"]) for r in checks}
    for q in sorted({min(blocks), max(blocks)}):
        for p in profiles[-2:]:
            for policy in BASE_POLICIES:
                if (q, p["name"], policy) in checked:
                    continue
                block = realize_fcr_block(blocks[q], draws["delivery"][0][1])
                a = comparison_trace(block, plant, plan, p, policy, selection)
                b = comparison_trace(block, plant, plan, p, policy, selection, refined=True)
                checks.append(dict(reference_requirement_mw=q, profile=p["name"], policy=policy,
                                   **check_refinement(a, b)))
                _csv(checks_path, checks)
    if _tree_hashes(source) != protocol["source_files_sha256"]:
        raise RuntimeError("preserved source comparison changed")
    _json(output / "study_status.json", dict(status="complete", policies_frozen=True, procurement_frozen=True,
        outcome_rows=len(records), numerical_comparisons=len(checks),
        imported_outcomes=sum(r["outcome_source"] == "preserved source scalar outcome" for r in records),
        last_invocation_wall_s=perf_counter()-started))
    run_capacity_report(output)


def run_capacity_report(output):
    protocol = json.loads((output / "protocol.json").read_text())
    if _tree_hashes(Path(protocol["source_directory"])) != protocol["source_files_sha256"]:
        raise ValueError("preserved source comparison changed")
    data = pd.read_csv(output / "outcomes.csv")
    market = pd.read_csv(output / "market_screen.csv")
    matches = pd.read_csv(output / "budget_matches.csv")
    plan, profiles = protocol["source_plan"], protocol["profiles"]
    sizes = (("design", plan["design_samples"]), ("validation", plan["validation_samples"]), ("delivery", 1))
    expected = {(q, bank, p["name"], policy, sample)
        for q in market[market.status == "feasible"].reference_requirement_mw
        for bank, n in sizes for p in profiles for policy in BASE_POLICIES for sample in range(n)}
    for r in matches[matches.status == "matched"].itertuples():
        expected.update((r.reference_requirement_mw, bank, r.profile, r.policy, sample)
                        for bank, n in sizes for sample in range(n))
    if data.duplicated(KEYS).any() or set(data[KEYS].itertuples(index=False, name=None)) != expected:
        raise ValueError("capacity report requires every planned paired outcome exactly once")
    summary = summarize_groups(data, ["reference_requirement_mw", "bank", "profile", "policy"])
    summary.to_csv(output / "performance_summary.csv", index=False)
    comparisons, same_demand = [], []
    for match in matches[matches.status == "matched"].itertuples():
        for bank, n in sizes:
            subset = data[(data.reference_requirement_mw == match.reference_requirement_mw)
                & (data.bank == bank) & (data.profile == match.profile)]
            fi = subset[subset.policy == match.comparator].set_index("sample").sort_index()
            fcr = subset[subset.policy == match.policy].set_index("sample").sort_index()
            if not fi.index.equals(fcr.index) or len(fi) != n:
                raise ValueError("incomplete paired capacity comparison")
            gap = fcr.fcr_block_payment_eur - fi.fcr_block_payment_eur - match.events_per_block*fi.net_payment_eur
            row = dict(reference_requirement_mw=match.reference_requirement_mw, profile=match.profile,
                comparator=match.comparator, bank=bank, n=n, fcr_equivalent_mw=match.requirement_mw,
                mean_budget_gap_eur=float(gap.mean()), budget_gap_ci95_halfwidth=_halfwidth(gap))
            for metric in METRICS:
                delta = fcr[metric]-fi[metric]
                row[metric+"_difference"] = float(delta.mean())
                row[metric+"_difference_ci95_halfwidth"] = _halfwidth(delta)
            comparisons.append(row)
    for (q, profile), group in data[data.bank == "validation"].groupby(["reference_requirement_mw", "profile"]):
        baseline = group[group.policy == "fcr_base"].set_index("sample").sort_index()
        for policy in ("bilinear", "l2"):
            fi = group[group.policy == policy].set_index("sample").sort_index()
            row = dict(reference_requirement_mw=q, profile=profile, policy=policy, n=len(fi))
            for metric in (*METRICS, *FINANCIAL):
                delta = fi[metric]-baseline[metric]
                row[metric+"_difference"] = float(delta.mean())
                row[metric+"_difference_ci95_halfwidth"] = _halfwidth(delta)
            same_demand.append(row)
    _csv(output / "equal_budget_comparison.csv", comparisons)
    _csv(output / "paired_fi_vs_fcr.csv", same_demand)
    from .plotting import plot_capacity_study
    plot_capacity_study(output, protocol, summary, market, matches, pd.DataFrame(comparisons))
    lines = ["# FCR capacity sensitivity with frozen FI policies", "",
        f"Fleet: {protocol['settings']['fleet_label']}.",
        f"Reference: {protocol['source_directory']} (preserved in place).", "",
        "## Protocol", "",
        "Only procurement demand varies within each fleet. The expanded fleet changes the three",
        "conventional reserve capacities from 4 to 8 MW and underlying means from 3.98 to 7.98 MW.",
        "This is a user-authorized synthetic reserve-availability sensitivity, not a sourced calibration",
        "or a change to installed ratings, solved dispatch, inertia, active-power limits, droop or lags.",
        "Variance, economic coefficients, renewables and the 1 MW positive bid minimum stay fixed.",
        "Expanded capability is checked against symmetric physical headroom. Offers are reoptimized;",
        "awards/roles freeze before draws. Gaussian truncation and the same laws for expected penalties",
        "and sampling are retained. Common bounded-law quantiles pair changed availability laws.", "",
        "Gamma=50 and lambda2=30000 remain the original curve-based choices at every requirement.",
        "They are not reselected or claimed optimal for the expanded fleet or new requirements.",
        "The four-draw design and independent 16-draw assessment are reused across policies/requirements;",
        "a separate common delivery draw illustrates trajectories. The eight original/new profiles",
        "retain their 40/60 s horizons, causal 0.1 s decisions, live frequency-error/cost ledgers and",
        "full-state passive L2 replay. No event preview, secondary control or energy limits are added.", "",
        "## RoCoF reporting", "",
        "Only maximum absolute RoCoF per trajectory is shown. It is calculated from the unfiltered",
        "physical RHS over the complete 40/60 s horizon, including exact left/right event limits,",
        "with no measurement/averaging window. Tables/curves report the mean of these trajectory",
        "peaks, with pointwise Student-t 95% intervals. No RoCoF time-series figure is generated.", "",
        "## Market screen", "",
        "| Demand [MW] | Status | FCR payment [EUR/block] | Clearing price [EUR/MW/block] |",
        "|---:|---|---:|---:|"]
    for r in market.itertuples():
        lines.append(f"| {r.reference_requirement_mw:g} | {r.status} | {r.fcr_block_payment_eur:.4f} | {r.clearing_price_eur_per_mw_block:.4f} |")
    lines += ["", "## Equal expected spending", "",
        f"Conditional events per existing four-hour block: N={protocol['settings']['events_per_block']}.",
        "This is an accounting assumption, not an occurrence forecast or repeated-event simulation.",
        "At each reference demand q, match P_FCR(q_equal)=P_FCR(q)+N*mean_design(S_FI).",
        "The demand freezes before independent outcomes. Each FI mechanism has its own comparator.",
        "Actual validation payments differ; paired budget gaps and 95% intervals are reported.",
        "Only demand changes for the FCR-only comparator within that fleet; the zero-award FI",
        "controller is disabled without changing online inertia. No supply/price/subsidy is invented.",
        "FCR activation costs remain absent in the maintained convention; the transfer budget is",
        "not total social cost or a guarantee of provider profitability.", "",
        "| Reference demand [MW] | Matched profile/contract cases | Supply-limited cases | Other failures |",
        "|---:|---:|---:|---:|"]
    for q, group in matches.groupby("reference_requirement_mw"):
        lines.append(f"| {q:g} | {(group.status == 'matched').sum()} | {(group.status == 'insufficient_offer_supply').sum()} | {(~group.status.isin(['matched', 'insufficient_offer_supply'])).sum()} |")
    lines += ["", "## Independent outcomes", "",
        "Compare profiles separately: their disturbance sizes and horizons differ. Statistical",
        "sample size is 16 shared availability vectors, not the number of outcome rows.",
        "No coefficient is tuned on these results. Positive paired E_f differences below mean",
        "the corresponding FI policy has less integrated error than its FCR-only budget comparator.", "",
        "| Demand [MW] | Profile | Policy | E_f [Hz^2 s] | Nadir [Hz] | Peak absolute RoCoF [Hz/s] | FI net [EUR] | FI profit [EUR] |",
        "|---:|---|---|---:|---:|---:|---:|---:|"]
    for r in summary[(summary.bank == "validation") & summary.policy.isin(BASE_POLICIES)].itertuples():
        lines.append(f"| {r.reference_requirement_mw:g} | {r.profile} | {r.policy} | {r.frequency_error_hz2_s:.7f} | {r.nadir_sampled_hz:.6f} | {r.rocof_max_abs_hz_per_s:.7f} | {r.net_payment_eur:.4f} | {r.profit_eur:.4f} |")
    lines += ["", "## Verification and artifacts", "",
        "Provider offers and awards are saved separately from realized availability and delivered power.",
        "Full pointwise intervals are in performance_summary.csv; paired FI/FCR differences are",
        "in paired_fi_vs_fcr.csv. Budget demands/feasibility are in budget_matches.csv and paired",
        "performance/spending gaps in equal_budget_comparison.csv. Complete single-draw traces",
        "are saved under delivery/; figures omit time-series RoCoF for readability.",
        "Numerical checks cover minimum/maximum feasible reference requirements and the final two",
        "stress profiles under all three policies, halving steps and tightening tolerances tenfold.",
        "Finite trajectories do not establish Lyapunov stability, population optimality or equilibrium.",
        "All earlier studies remain unchanged. Report-only regeneration performs no integration.", ""]
    (output / "ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")


def _halfwidth(values):
    return float(student_t.ppf(.975, len(values)-1)*values.std(ddof=1)/np.sqrt(len(values))) if len(values) > 1 else 0.
