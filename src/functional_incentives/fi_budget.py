"""Replace FCR procurement with event FI at a fixed reference block budget."""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from hashlib import sha256
import json
from math import fsum
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .experiment import load_block_providers
from .frequency import LinearFrequencyParameters
from .grid import AndesOperatingPoint
from .lyapunov import _csv, _json
from .lyapunov_comparison import (
    _draws, _job, check_refinement, clear_requirement, comparison_trace,
)
from .simulation import clear_fcr_block, realize_fcr_block


def _match(args):
    base, plant, config, plan, profile, policy, selection, draws, tolerance, output = args
    folder = output / "search" / profile["name"] / policy
    folder.mkdir(parents=True, exist_ok=True)
    cache = {}
    budget = base.market.total_payment_eur

    def gap(q):
        q = float(q)
        if q not in cache:
            path = folder / (q.hex().replace("+", "p") + ".csv")
            if path.exists():
                rows = pd.read_csv(path)
            else:
                cleared = clear_requirement(base, plant, config, q)
                rows = pd.DataFrame([_job(cleared, plant, plan, profile, policy, selection,
                    draw, "design", sample, output) for sample, draw in draws])
                _csv(path, rows.to_dict("records"))
            cache[q] = float((rows.fcr_block_payment_eur + rows.net_payment_eur).mean() - budget)
        return cache[q]

    # Search down from the reference. Never interpolate over a change of
    # marginal supplier or FI eligibility. At a kink inspect each side.
    quantities = []
    bounds = [0.]
    for offer in sorted(base.market.offers, key=lambda o: (o.price_eur_per_mw_block, o.provider_id)):
        if offer.quantity_mw > 0:
            quantities.append(offer.quantity_mw)
            bounds.append(min(fsum(quantities), base.market.requirement_mw))
    bounds = sorted(set(bounds), reverse=True)
    selected = None
    for upper, lower in zip(bounds[:-1], bounds[1:], strict=True):
        left = lower + min(1e-7, (upper - lower) / 2)
        gu = gap(upper)
        if abs(gu) <= tolerance:
            selected = upper
            break
        gl = gap(left)
        if abs(gl) <= tolerance:
            selected = left
            break
        if gu * gl < 0:
            root = brentq(gap, left, upper, xtol=1e-7)
            if abs(gap(root)) <= tolerance:
                selected = root
                break
    if selected is None and abs(gap(0.)) <= tolerance:
        selected = 0.
    closest = min(cache, key=lambda q: abs(cache[q]))
    result = dict(profile=profile["name"], policy=policy, budget_eur=budget,
        status="matched" if selected is not None else "no_verified_match",
        requirement_mw=selected, closest_tested_mw=closest, closest_gap_eur=cache[closest])
    print(profile["name"], policy, result["status"], selected, flush=True)
    return result


def _episode(args):
    return _job(*args)


def run_budget_study(manifest, output):
    settings = tomllib.loads(manifest.read_text(encoding="utf-8"))
    design = (manifest.parent / settings["source_design"]).resolve()
    comparison = (manifest.parent / settings["source_comparison"]).resolve()
    sources = [design / n for n in ("frozen_inputs.json", "operating_point.json",
        "frequency_parameters.json", "availability_draws.csv", "market.json", "design_samples.csv")]
    sources += [comparison / n for n in ("selection.json", "protocol.json", "delivery_draw.json")]
    for source in (design, comparison):
        if source == output.resolve() or source in output.resolve().parents or output.resolve() in source.parents:
            raise ValueError("use a separate study directory")
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    frozen = read(design / "frozen_inputs.json")
    config, plan = frozen["effective_config"], frozen["plan"]
    selection = read(comparison / "selection.json")
    if selection["source_design_sha256"] != sha256((design / "design_samples.csv").read_bytes()).hexdigest():
        raise ValueError("frozen policies and source design differ")
    plant = LinearFrequencyParameters(**read(design / "frequency_parameters.json"))
    point = AndesOperatingPoint(**read(design / "operating_point.json"))
    providers = load_block_providers(config, point)
    base = clear_fcr_block(providers, requirement_mw=config["market"]["requirement_mw"],
        nominal_frequency_hz=plant.nominal_frequency_hz, droop_pu=config["scenario"]["droop_pu"],
        minimum_bid_mw=config["market"]["minimum_bid_mw"],
        quantity_tolerance_mw=config["decision"].get("quantity_tolerance_mw", 1e-8))
    if [asdict(a) for a in base.market.awards] != read(design / "market.json")["awards"]:
        raise ValueError("reference market changed")
    rise = settings["disturbance_rise_s"]
    tolerance = settings["budget_tolerance_eur"]
    if not np.isfinite(rise) or rise <= 0 or not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("the ramp and budget tolerance must be finite and positive")
    catalog = {p["name"]: dict(p, final_time_s=plan["final_time_s"]) for p in plan["profiles"]}
    catalog.update({p["name"]: p for p in read(comparison / "protocol.json")["settings"]["demonstrations"]})
    profiles = [dict(catalog[name], disturbance_rise_s=rise) for name in settings["profiles"]]
    if not profiles or len(set(settings["profiles"])) != len(profiles):
        raise ValueError("choose distinct named disturbance profiles")
    files = sources + [Path(__file__).with_name(name + ".py") for name in
        ("fi_budget", "lyapunov_comparison", "lyapunov", "simulation", "incentives", "decision",
         "market", "providers", "frequency", "metrics", "experiment")]
    protocol = dict(settings=settings, profiles=profiles, selection=selection, events_per_block=1,
        reference_budget_eur=base.market.total_payment_eur,
        source_sha256={str(p.resolve()): sha256(p.read_bytes()).hexdigest() for p in files})
    output.mkdir(exist_ok=True, parents=True)
    protocol_path = output / "protocol.json"
    if protocol_path.exists() and read(protocol_path) != protocol:
        raise ValueError("inputs or implementation changed; use a new study directory")
    if not protocol_path.exists() and any(output.iterdir()):
        raise ValueError("new study requires an empty output directory")
    _json(protocol_path, protocol)
    banks = pd.read_csv(design / "availability_draws.csv")
    draws = _draws(base, banks, "design")
    with ProcessPoolExecutor(settings["workers"]) as pool:
        baseline = list(pool.map(_episode, [(base, plant, plan, p, "fcr_base", selection, draw,
            "design", i, output) for p in profiles for i, draw in draws]))
        _csv(output / "reference_design.csv", baseline)
        matches = list(pool.map(_match, [(base, plant, config, plan, p, policy, selection, draws,
            tolerance, output) for p in profiles for policy in ("bilinear", "l2")]))
        _json(output / "frozen_demands.json", matches)
        delivery = read(comparison / "delivery_draw.json")["availability_mw"]
        jobs, checks, markets = [], [], []
        for p in profiles:
            policies = {"fcr_base": base.market.requirement_mw}
            policies.update({m["policy"]: m["requirement_mw"] for m in matches
                if m["profile"] == p["name"] and m["status"] == "matched"})
            for policy, q in policies.items():
                cleared = clear_requirement(base, plant, config, q)
                markets.append(dict(profile=p["name"], policy=policy, market=asdict(cleared.market),
                    provider_ids=[r.provider_id for r in cleared.providers], modes=cleared.participation_modes))
                for bank, samples in (("validation", _draws(base, banks, "validation")),
                                      ("delivery", [(0, delivery)])):
                    jobs.extend((cleared, plant, plan, p, policy, selection, draw, bank, i, output)
                                for i, draw in samples)
                checks.append((cleared, plant, plan, p, policy, selection, delivery))
        _json(output / "markets.json", markets)
        outcomes = list(pool.map(_episode, jobs))
        _csv(output / "outcomes.csv", outcomes)
        _csv(output / "refinement.csv", list(pool.map(_refine, checks)))


def _refine(args):
    cleared, plant, plan, p, policy, selection, draw = args
    block = realize_fcr_block(cleared, draw)
    a = comparison_trace(block, plant, plan, p, policy, selection)
    b = comparison_trace(block, plant, plan, p, policy, selection, refined=True)
    return dict(profile=p["name"], policy=policy, **check_refinement(a, b))
