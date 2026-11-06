"""Controlled reversal experiment reusing the FI and FPP provider implementations."""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from time import perf_counter
import tomllib
import subprocess

import numpy as np
import pandas as pd

from .experiment import prepare_experiment
from .fpp import IDEAL_PROFILE, settle_fpp
from .fpp_game import draw_gain_bank, validate_game
from .fpp_study import (load_fpp_overlay, make_dispatch_schedules, policy_response_parameters,
                         prepare_gain_study, save_trajectory, write_json)
from .metrics import summarize_fpp_benchmark, summarize_fpp_assessment
from .metrics import empirical_pareto_flags
from .providers import FIProviderParameters
from .simulation import realize_fcr_block, simulate_fcr_power_step
from .tso import simulate_common_gamma


def verified_cache(source, overlay, config, cleared):
    """Reuse completed numerical scores only for identical cases and physical inputs."""
    if not source.exists():
        return {}, {}
    read = lambda name: json.loads((source/name).read_text(encoding="utf-8"))
    prior = read("overlay.json")
    if (read("study_status.json")["status"] != "complete" or read("effective_config.json") != config
            or prior["source_profile"] != overlay["source_profile"] or prior["numerics"] != overlay["numerics"]
            or prior["assessment"] != overlay["assessment"]
            or {k:v for k,v in prior["game"].items() if k != "gains"} !=
               {k:v for k,v in overlay["game"].items() if k != "gains"}):
        raise ValueError("cached gain study inputs differ")
    streams = np.random.SeedSequence(overlay["assessment"]["seed"]).spawn(5)
    cache, hashes = {}, {}
    for bank, size, stream, file in (
            ("design", overlay["game"]["design_samples"], streams[2], "design_scenarios.csv"),
            ("assessment", overlay["assessment"]["samples"], streams[0], "availability_draws.csv")):
        expected = pd.DataFrame(draw_gain_bank(bank,size,stream,cleared.providers,overlay["game"]["paths"]))
        saved = pd.read_csv(source/file).query("bank == @bank")[expected.columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(saved,expected,check_exact=False,rtol=1e-14,atol=1e-14)
        path = source/f"{bank}_gain_scores.csv"
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        scores=pd.read_csv(path)
        expected_keys={(i,a,b) for i in range(size) for a in prior["game"]["gains"] for b in prior["game"]["gains"]}
        if (scores.duplicated(["sample","theta_1","theta_2"]).any() or
                set(scores[["sample","theta_1","theta_2"]].itertuples(index=False,name=None))!=expected_keys or
                not np.isfinite(scores[[f"score_{i}_{'eur' if 'prices' in overlay['game'] else 'mw_h'}"
                                       for i in (1, 2)]].to_numpy()).all()):
            raise ValueError("cached score table is incomplete or non-finite")
        for row in scores.to_dict("records"):
            if row["path"] != expected.iloc[row["sample"]]["path"]:
                raise ValueError("cached score path differs from the matched case")
            cache[bank,row["sample"],row["theta_1"],row["theta_2"]] = {
                k:v for k,v in row.items() if k not in ("bank", "sample", "path", "theta_1", "theta_2")}
    return cache, hashes


def run_controlled(path, output):
    settings = tomllib.loads(path.read_text(encoding="utf-8"))
    if (settings["fi_design_samples"] < 2 or settings["evaluation_samples"] < 2
            or settings["seed"] < 0 or len(set(settings["gamma_values"])) != len(settings["gamma_values"])
            or any(not np.isfinite(g) or g < 0 for g in settings["gamma_values"])):
        raise ValueError("invalid samples, seed or gamma grid")
    output.mkdir(parents=True, exist_ok=True)
    if (output/"study_status.json").exists() and json.loads((output/"study_status.json").read_text())["status"]=="complete":
        raise ValueError("study is complete; use a new directory to repeat the experiment")
    overlay, config, prices, _ = load_fpp_overlay(path.parent/settings["overlay"])
    overlay["game"]["gains"] = settings["gain_catalogue"]
    validate_game(overlay["game"],300.)
    point, cleared, plant = prepare_experiment(config)
    if [a.quantity_mw for a in cleared.market.awards] != [4.,4.,4.,0.,0.]:
        raise ValueError("controlled comparison requires the prescribed 12 MW conventional awards")
    schedule = make_dispatch_schedules(cleared,overlay["schedules"],300)["constant"]
    options = dict(power_deficit_mw=10.,start_time_s=1.,power_deficit_changes=((120.05,-7.),),
        final_time_s=300.,output_time_step_s=config["scenario"]["output_time_step_s"],maximum_solver_step_s=.1,
        fi_decision_interval_s=.1,measurement_times_s=(),**overlay["numerics"])
    plan = {k:v for k,v in settings.items() if k not in ("selected_gamma","gamma_reason")}
    if (output/"plan.json").exists() and json.loads((output/"plan.json").read_text()) != plan:
        raise ValueError("study inputs changed on restart")
    for filename, current in (("effective_config.json", config), ("overlay.json", overlay)):
        if (output/filename).exists() and json.loads((output/filename).read_text()) != current:
            raise ValueError("study model or settlement profile changed; use a new output directory")
    write_json(output/"plan.json",plan)
    write_json(output/"effective_config.json",config)
    write_json(output/"overlay.json",overlay)
    files=[Path(__file__),Path(__file__).with_name("simulation.py"),Path(__file__).with_name("decision.py"),
           Path(__file__).with_name("fpp.py"),Path(__file__).with_name("fpp_game.py")]
    write_json(output/"code_provenance.json",dict(base_commit=subprocess.check_output(
        ["git","rev-parse","HEAD"],text=True).strip(),sha256={str(p.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}))
    write_json(output/"awards.json",[asdict(a) for a in cleared.market.awards])
    write_json(output/"frequency_parameters.json",asdict(plant))
    write_json(output/"operating_point.json",{**asdict(point),"case_path":str(point.case_path)})
    write_json(output/"prescribed_offers.json",[asdict(d.offer) for d in cleared.decisions])
    streams = np.random.SeedSequence(settings["seed"]).spawn(4)
    ids = [p.provider_id for p in cleared.providers]
    def bank(label,n,stream):
        rng = np.random.default_rng(stream)
        return [dict(bank=label,sample=i,**{p.provider_id:p.availability.sample_mw(rng) for p in cleared.providers}) for i in range(n)]
    design = bank("fi_design",settings["fi_design_samples"],streams[0])
    pd.DataFrame(design).to_csv(output/"fi_design_draws.csv",index=False)
    curve_path=output/"fi_curve_samples.csv"
    records = pd.read_csv(curve_path).to_dict("records") if curve_path.exists() else []
    done={(r["gamma"],r["sample"]) for r in records}
    for gamma in settings["gamma_values"]:
        for row in design:
            if (gamma,row["sample"]) in done:
                continue
            block=realize_fcr_block(cleared,{p:row[p] for p in ids})
            start=perf_counter()
            trace=simulate_common_gamma(block,plant,gamma,options)
            metric,_=summarize_fpp_benchmark(trace,schedule,1.)
            records.append(dict(gamma=gamma,sample=row["sample"],wall_s=perf_counter()-start,**metric))
            pd.DataFrame(records).to_csv(curve_path,index=False)
        print(f"FI trade-off gamma={gamma} complete",flush=True)
    curve=pd.DataFrame(records).groupby("gamma").mean(numeric_only=True)
    for name,maximize in (("frequency_error_hz2_s",False),("nadir_sampled_hz",True),("zenith_sampled_hz",False),
                          ("rocof_max_abs_hz_per_s",False),("final_absolute_error_hz",False)):
        curve["frontier_"+name]=empirical_pareto_flags(curve.fi_net_settlement_eur,curve[name],maximize=maximize)
    curve.to_csv(output/"fi_curve_means.csv")
    if "selected_gamma" not in settings:
        write_json(output/"study_status.json",dict(status="awaiting_illustrative_gamma",curve_complete=True))
        return
    gamma=settings["selected_gamma"]
    if gamma not in settings["gamma_values"] or not settings.get("gamma_reason"):
        raise ValueError("selected gamma must be evaluated and accompanied by a reason")
    frozen=dict(gamma=gamma,reason=settings["gamma_reason"],tso_knows_reversal=True,
                tso_knows_realized_availability=False)
    if (output/"fi_selection.json").exists() and json.loads((output/"fi_selection.json").read_text())!=frozen:
        raise ValueError("FI policy changed after selection")
    write_json(output/"fi_selection.json",frozen)
    write_json(output/"experiment_config.json",settings)
    write_json(output/"study_status.json",dict(status="running",gamma_frozen=gamma))
    game_dir=output/"fpp_design"
    game_dir.mkdir(exist_ok=True)
    if not (game_dir/"gain_equilibrium.json").exists() or not (game_dir/"holdout_deviations.csv").exists():
        source = (path.parent/settings["reuse_gain_study"]).resolve()
        # A historical coefficient-only game cannot supply net-profit payoffs.
        if source.exists() and json.loads((source/"overlay.json").read_text())["source_profile"] != overlay["source_profile"]:
            cache, hashes = {}, {}
        else:
            cache,hashes=verified_cache(source,overlay,config,cleared)
        write_json(output/"reused_source_hashes.json",hashes)
        prepare_gain_study(game_dir,overlay,cleared,plant,schedule,
                           {**options,"provider_operating_power_mw":schedule.initial_operating_power_mw},
                           cached_scores=cache)
    eq=json.loads((game_dir/"gain_equilibrium.json").read_text())
    players=eq["players"]
    rows=bank("assessment",settings["evaluation_samples"],streams[1])+bank("delivery",1,streams[2])
    rng=np.random.default_rng(streams[3])
    for row in rows:
        row["theta_1"],row["theta_2"]=[float(rng.choice(eq["catalogue"],p=eq["probabilities"][p])) for p in players]
    pd.DataFrame(rows).to_csv(output/"evaluation_draws.csv",index=False)
    write_json(output/"frozen_policies.json",dict(fi=frozen,fpp=eq["probabilities"],
        prior=overlay["game"]["paths"],evaluation_seed=settings["seed"]))
    metrics,financial,accounts,saturation,checks,diagnostics,block_finance=[],[],[],[],[],[],[]
    started=perf_counter()
    for row in rows:
        block=realize_fcr_block(cleared,{p:row[p] for p in ids})
        block_finance.extend(dict(bank=row["bank"],sample=row["sample"],**asdict(s)) for s in block.settlements)
        gains=dict(zip(players,[row["theta_1"],row["theta_2"]]))
        for policy in ("fcr_alone","bilinear_fi","fpp"):
            responses=policy_response_parameters(cleared,block,policy,gains=gains if policy=="fpp" else None)
            responses=tuple(replace(p,gamma_eur_per_hz_mw_s=gamma) if isinstance(p,FIProviderParameters) else p for p in responses)
            trace=simulate_fcr_power_step(plant,responses,provider_operating_power_mw=block.operating_powers_mw,
                                          dispatch_schedule=schedule,**options)
            tag=dict(bank=row["bank"],sample=row["sample"],schedule="constant",policy=policy)
            metric,limits=summarize_fpp_benchmark(trace,schedule,1.)
            renewable_indices=[trace.provider_ids.index(p) for p in players]
            aggregate=trace.provider_response_mw[:,renewable_indices].sum(axis=1)
            metric.update(renewable_aggregate_peak_mw=float(abs(aggregate).max()),
                          renewable_aggregate_terminal_mw=float(aggregate[-1]))
            for label,mask in (("before_reversal",trace.time_s<=120.05),("after_reversal",trace.time_s>=120.05)):
                metric[label+"_peak_absolute_error_hz"]=float(abs(trace.delta_frequency_hz[mask]).max())
            metrics.append({**tag,**metric})
            saturation.extend({**tag,**r} for r in limits)
            for j,p in enumerate(players):
                col=trace.provider_ids.index(p)
                record={**tag,"provider_id":p,"peak_response_mw":float(abs(trace.provider_response_mw[:,col]).max()),
                        "terminal_response_mw":float(trace.provider_response_mw[-1,col]),
                        "activation_cost_eur":0.,"fi_net_payment_eur":0.}
                if trace.fi is not None:
                    k=trace.fi.provider_ids.index(p)
                    record.update(activation_cost_eur=float(trace.fi.activation_cost_eur[-1,k]),
                        fi_net_payment_eur=float(trace.fi.net_settlement_eur[-1,k]),
                        fi_gross_rewards_eur=float(trace.fi.gross_rewards_eur[-1,k]),
                        fi_charges_eur=float(trace.fi.gross_penalties_eur[-1,k]))
                    record["fi_profit_eur"]=record["fi_net_payment_eur"]-record["activation_cost_eur"]
                if trace.fpp_activation_cost_eur is not None:
                    record["activation_cost_eur"]=float(trace.fpp_activation_cost_eur[-1,trace.fpp_provider_ids.index(p)])
                financial.append(record)
            if policy=="fpp":
                settled=settle_fpp(trace.time_s,trace.delta_frequency_hz,trace.provider_active_power_mw,schedule,prices,profile=IDEAL_PROFILE)
                if settled["accounts"].groupby(["interval","direction"]).coefficient_mw_h.sum().abs().max()>1e-10:
                    raise RuntimeError("unbalanced FPP coefficients")
                accounts.append(settled["accounts"].assign(**tag))
                revenues = settled["accounts"].groupby("provider_id").net_settlement.sum()
                for record in financial[-len(players):]:
                    record["fpp_net_payment_eur"] = float(revenues.loc[record["provider_id"]])
                    record["fpp_profit_eur"] = record["fpp_net_payment_eur"] - record["activation_cost_eur"]
            if row["bank"]=="delivery":
                directory=output/"delivery"/policy
                directory.mkdir(parents=True,exist_ok=True)
                save_trajectory(trace,schedule,directory)
                fine=simulate_fcr_power_step(plant,responses,provider_operating_power_mw=block.operating_powers_mw,
                    dispatch_schedule=schedule,**{**options,"output_time_step_s":options["output_time_step_s"]/2,"maximum_solver_step_s":.05,
                    "relative_tolerance":options["relative_tolerance"]/10,"absolute_tolerance":options["absolute_tolerance"]/10})
                idx=np.searchsorted(fine.time_s,trace.time_s)
                check=dict(policy=policy,frequency_diff_hz=float(abs(trace.frequency_hz-fine.frequency_hz[idx]).max()),
                    response_diff_mw=float(abs(trace.provider_response_mw-fine.provider_response_mw[idx]).max()),
                    error_diff_hz2_s=float(abs(trace.integrated_squared_frequency_error_hz2_s[-1]-fine.integrated_squared_frequency_error_hz2_s[-1])))
                if check["frequency_diff_hz"]>1e-6 or check["response_diff_mw"]>1e-5 or check["error_diff_hz2_s"]>1e-7:
                    raise RuntimeError(f"numerical refinement failed: {check}")
                if trace.fi is not None:
                    for name in ("net_settlement_eur","activation_cost_eur"):
                        if abs(getattr(trace.fi,name)[-1]-getattr(fine.fi,name)[-1]).max()>1e-5:
                            raise RuntimeError("FI financial refinement failed")
                if policy=="fpp":
                    other=settle_fpp(fine.time_s,fine.delta_frequency_hz,fine.provider_active_power_mw,schedule,prices,profile=IDEAL_PROFILE)
                    check["coefficient_diff_mw_h"]=float(abs(settled["accounts"].coefficient_mw_h-other["accounts"].coefficient_mw_h).max())
                    if check["coefficient_diff_mw_h"]>1e-6: raise RuntimeError("coefficient refinement failed")
                    settled["accounts"].to_csv(directory/"fpp_accounts.csv",index=False)
                checks.append(check)
            print(f"Controlled comparison {row['bank']} {row['sample']} {policy}",flush=True)
        # Frozen-policy diagnostics. These outcomes never change either selected policy.
        if row["bank"]=="assessment":
            for theta in (2.,3.):
                responses=policy_response_parameters(cleared,block,"fpp",gains=dict.fromkeys(players,theta))
                trace=simulate_fcr_power_step(plant,responses,provider_operating_power_mw=block.operating_powers_mw,
                                              dispatch_schedule=schedule,**options)
                metric,limits=summarize_fpp_benchmark(trace,schedule,1.)
                diagnostics.append(dict(sample=row["sample"],theta=theta,**metric,
                    renewable_target_bound_fraction=float(np.mean([r["target_bound_fraction"] for r in limits if r["provider_id"] in players]))))
    for name,data in (("metrics",metrics),("financial",financial),("saturation",saturation),("numerical_checks",checks),
                      ("gain_physical_diagnostics",diagnostics),("fcr_block_financial",block_finance)):
        pd.DataFrame(data).to_csv(output/f"{name}.csv",index=False)
    pd.concat(accounts).to_csv(output/"fpp_accounts.csv",index=False)
    table=pd.DataFrame(metrics).query("bank == 'delivery'").set_index("policy")
    for r in pd.DataFrame(financial).query("bank == 'delivery'").to_dict("records"):
        for k,v in r.items():
            if k not in ("bank","sample","policy","schedule","provider_id"):
                table.loc[r["policy"],r["provider_id"]+"_"+k]=v
    for r in pd.DataFrame(saturation).query("bank == 'delivery'").to_dict("records"):
        if r["provider_id"] in players:
            for k in ("target_bound_fraction","delivered_bound_fraction"):
                table.loc[r["policy"],r["provider_id"]+"_"+k]=r[k]
    for r in pd.concat(accounts).query("bank == 'delivery'").to_dict("records"):
        table.loc["fpp",r["provider_id"]+"_"+r["direction"]+"_coefficient_mw_h"]=r["coefficient_mw_h"]
    table.to_csv(output/"summary_table.csv")
    summary,paired=summarize_fpp_assessment(pd.DataFrame(metrics))
    summary.to_csv(output/"assessment_summary.csv",index=False)
    paired.to_csv(output/"paired_differences.csv",index=False)
    plot_controlled(output)
    write_analysis(output)
    write_json(output/"study_status.json",dict(status="complete",evaluation_wall_s=perf_counter()-started))


def write_analysis(out):
    config=json.loads((out/"experiment_config.json").read_text())
    selection=json.loads((out/"fi_selection.json").read_text())
    eq=json.loads((out/"fpp_design"/"gain_equilibrium.json").read_text())
    data=pd.read_csv(out/"metrics.csv")
    summary=pd.read_csv(out/"assessment_summary.csv")
    representative=data.query("bank == 'delivery'")
    lines=["# Controlled FCR, FI and FPP reversal comparison","",
        "The TSO is assumed to forecast the complete prescribed reversal perfectly when inspecting "
        "the FI trade-off. Providers receive causal current measurements and private availability. "
        "FI and FPP policies are frozen before the common independent evaluation bank and representative delivery.","",
        f"The illustrative gamma is {selection['gamma']} EUR/(Hz MW s). {selection['reason']}","",
        f"The expanded FPP game selected {eq['kind']} probabilities {eq['probabilities']}. "
        f"Pure equilibria in the design table are {eq['pure_profiles']}. "
        "The prior samples uniformly from no event, +5 MW at 40 s, +8 MW at 20 s, and +10 MW at 1 s "
        "followed by -7 MW at 120.05 s. Availability is drawn independently from each configured law. "
        "Final evaluation always uses the prescribed reversal, with fresh common availability draws.","",
        "## Representative delivery","",
        "| Mechanism | Nadir [Hz] | Zenith [Hz] | Max abs RoCoF [Hz/s] | Squared error [Hz² s] | Terminal deviation [Hz] | FI net payment [EUR] | Renewable activation cost [EUR] |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    fields=["nadir_sampled_hz","zenith_sampled_hz","rocof_max_abs_hz_per_s","frequency_error_hz2_s",
            "terminal_error_hz","fi_net_settlement_eur","switched_activation_cost_eur"]
    if eq.get("score_unit") == "eur":
        prices = eq["prices"]
        lines[2:2] = [f"FPP directional prices are {prices['raise_per_mw_h']:g}/{prices['lower_per_mw_h']:g} "
            "EUR/(MW h) (raise/lower), synthetic illustrative scales. The private objective subtracts "
            "the live integral of rho*z(t)^2/2 using delivered power, with rho "
            f"{eq['activation_cost_rho_eur_per_mw2_s']} EUR/(MW^2 s). Internal costs are outside "
            "the balanced transfer ledger.", ""]
    for r in representative.to_dict("records"):
        lines.append("| "+r["policy"]+" | "+" | ".join(f"{r[k]:.8g}" for k in fields)+" |")
    lines+=["","## Independent evaluation averages","",
        "| Mechanism | Metric | Mean | Pointwise 95% interval |","|---|---|---:|---|"]
    for r in summary[summary.metric.isin(fields)].to_dict("records"):
        lines.append(f"| {r['policy']} | {r['metric']} | {r['mean']:.7g} | [{r['ci95_low']:.7g}, {r['ci95_high']:.7g}] |")
    means=data.query("bank == 'assessment'").groupby("policy").mean(numeric_only=True)
    base=means.loc["fcr_alone","frequency_error_hz2_s"]
    lines+=["","## Physical interpretation",""]
    for p in ("bilinear_fi","fpp"):
        r=means.loc[p]
        lines.append(f"- {p} changes mean squared frequency error by {100*(r.frequency_error_hz2_s/base-1):+.2f}% "
            f"against FCR-only. Mean nadir is {r.nadir_sampled_hz:.6f} Hz and mean zenith {r.zenith_sampled_hz:.6f} Hz. "
            f"Mean peak absolute error after reversal is {r.after_reversal_peak_absolute_error_hz:.6f} Hz.")
    diag=pd.read_csv(out/"gain_physical_diagnostics.csv").groupby("theta").mean(numeric_only=True)
    if len(eq["pure_profiles"])==1 and eq["pure_profiles"][0][0]==eq["pure_profiles"][0][1]:
        theta=eq["pure_profiles"][0][0]
        for key in ("frequency_error_hz2_s","nadir_sampled_hz","zenith_sampled_hz"):
            diag.loc[theta,key]=means.loc["fpp",key]
        sat=pd.read_csv(out/"saturation.csv")
        diag.loc[theta,"renewable_target_bound_fraction"]=sat[
            (sat.bank=="assessment") & (sat.policy=="fpp") & sat.provider_id.isin(eq["players"])].target_bound_fraction.mean()
    diag=diag.sort_index()
    lines+=["","| Symmetric gain | Mean squared error [Hz² s] | Mean nadir [Hz] | Mean zenith [Hz] | Mean target-bound fraction |",
            "|---:|---:|---:|---:|---:|"]
    for theta,r in diag.iterrows():
        lines.append(f"| {theta:g} | {r.frequency_error_hz2_s:.8g} | {r.nadir_sampled_hz:.6f} | {r.zenith_sampled_hz:.6f} | {r.renewable_target_bound_fraction:.6f} |")
    fin=pd.read_csv(out/"financial.csv")
    lines+=["","## Renewable behavior and accounting","",
        "| Policy | Provider | Mean peak response [MW] | Mean activation cost [EUR] | Mean FI net payment [EUR] |",
        "|---|---|---:|---:|---:|"]
    for (p,i),r in fin.query("bank == 'assessment'").groupby(["policy","provider_id"]).mean(numeric_only=True).iterrows():
        lines.append(f"| {p} | {i} | {r.peak_response_mw:.6f} | {r.activation_cost_eur:.6f} | {r.fi_net_payment_eur:.6f} |")
    fpp=pd.read_csv(out/"fpp_accounts.csv").query("bank == 'assessment'")
    lines+=["","| FPP entity | Mean raise coefficient [MW h] | Mean lower coefficient [MW h] |",
            "|---|---:|---:|"]
    for entity in fpp.provider_id.unique():
        r=fpp[fpp.provider_id==entity].groupby("direction").coefficient_mw_h.mean()
        lines.append(f"| {entity} | {r['raise']:.8f} | {r['lower']:.8f} |")
    lines += ["", ("The selected gains are empirical best responses for expected monetary settlement minus "
        "FI-style delivered-response activation cost. Prices are explicit illustrative EUR scales. "
        "Ideal settlement uses full-trajectory time quadrature with no four-second measurement clock. "
        if eq.get("score_unit") == "eur" else
        "The boundary gain is an empirical best response within this catalogue. Its coefficient objective "
        "values a private share of frequency support and excludes the activation-cost penalty. ") + "Higher private "
        "coefficients alone do not prove better system performance. The diagnostic rows and saturation data "
        "separate that question from finite-game equilibrium selection. Physical benefits and higher activation "
        "cost can coexist. The four-path design prior includes the prescribed reversal, while the final "
        "availability bank and representative draw remain independent of selection."]
    lines+=["","## Response and settlement tables","",
        "financial.csv records individual peak and terminal renewable responses, actual activation costs "
        "and FI payments and charges for every common draw. New net-profit studies also retain FPP "
        "monetary payments and profits. saturation.csv records individual target "
        "and delivered-bound fractions. fpp_accounts.csv records signed raise/lower coefficients for "
        "every metered unit and the residual. fcr_block_financial.csv retains whole-block capacity "
        "payments, commitment costs and shortage penalties separately from the 300 s episode.","",
        "gain_physical_diagnostics.csv evaluates the frozen symmetric gains 2 and 3 on the final common "
        "bank for interpreting saturation and frequency effects. These diagnostic outcomes never "
        "select or modify the policies.","",
        "## Verification and limits","",
        "Representative trajectories pass half-step/tighter-tolerance refinement for frequency, response, "
        "integrated error and available financial ledgers. FPP directional coefficients balance including "
        "the residual. All providers retain the same operating point, realized availability and conventional "
        "awards. Both renewable zero offers are prescribed exogenously. RoCoF uses the unfiltered model "
        "RHS at output times and exact one-sided event limits, with maximum absolute value over [0,300] s "
        "and no averaging window.","",
        "FI uses perfect disturbance forecasting at the TSO level and unknown future availability. FPP "
        "uses its declared prior and private objective. These are different information and objective "
        "assumptions. " + ("Synthetic FPP prices are monetary scales, not market calibration. "
                         if eq.get("score_unit") == "eur" else
                         "Historical FPP coefficients are unpriced and cannot determine monetary profit. ") +
        "The passive residual closes accounting but "
        "has no specified real funding counterparty. The plant is an aggregate model initialized by "
        "ANDES power flow. These results are conditional on the disturbance, parameters, gain catalogue "
        "and small design/evaluation banks. An empirical finite-game equilibrium is not a population "
        "or unconstrained profit-optimality claim.","",
        "Reproduction uses scripts/run_fcr.py --controlled-study --config configs/controlled_comparison.toml "
        "--output-dir results/controlled_comparison_replay. The selected gamma is an illustrative analyst "
        "choice from the curve, not a mathematical optimum or a value chosen by the user."]
    (out/"ANALYSIS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def plot_controlled(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels=["FCR only","FCR + FI","FCR + FPP"]
    policies=["fcr_alone","bilinear_fi","fpp"]
    colors=["#737b82","#246997","#bb4821"]
    traces={p:pd.read_csv(out/"delivery"/p/"trajectory.csv") for p in policies}
    def save(fig,name):
        fig.savefig(out/f"{name}.png",dpi=170,bbox_inches="tight")
        fig.savefig(out/f"{name}.pdf",bbox_inches="tight")
        plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(11,7),layout="constrained")
    for p,c,label in zip(policies,colors,labels):
        f=traces[p]
        for ax in axes.flat[:3]: ax.plot(f.time_s,f.frequency_hz,color=c,label=label)
        axes[1,1].plot(f.time_s,f.rocof_hz_per_s,color=c,label=label)
        for r in pd.read_csv(out/"delivery"/p/"disturbance_limits.csv").itertuples():
            axes[1,1].plot([r.time_s]*2,[r.rocof_left_hz_per_s,r.rocof_right_hz_per_s],color=c)
    for ax in axes.flat:
        ax.grid(alpha=.2); ax.axvline(1,color="grey",ls=":"); ax.axvline(120.05,color="grey",ls=":")
        ax.set_xlabel("Time [s]")
    axes[0,0].set(xlim=(0,300),ylabel="Frequency [Hz]",title="Representative delivery")
    axes[0,1].set(xlim=(0,15),ylabel="Frequency [Hz]",title="Initial deficit")
    axes[1,0].set(xlim=(118,135),ylabel="Frequency [Hz]",title="Deficit to surplus")
    axes[1,1].set(xlim=(0,300),ylabel="RoCoF [Hz/s]",title="Unfiltered model RHS and exact event limits")
    axes[0,0].legend();save(fig,"figure_1_frequency")
    fig,axes=plt.subplots(2,2,figsize=(11,6),layout="constrained")
    for i,p in enumerate(["renewable_reliable","renewable_uncertain"]):
        for j,window in enumerate([(0,20),(118,135)]):
            ax=axes[i,j]
            for policy,c,label in zip(policies[1:],colors[1:],labels[1:]):
                f=traces[policy];ax.plot(f.time_s,f[p+"_response_mw"],color=c,label=label)
            ax.set(xlim=window,xlabel="Time [s]",ylabel="Delivered power [MW]",title=p.replace("_"," "))
            ax.grid(alpha=.2);ax.legend()
    save(fig,"figure_2_responses")
    curve=pd.read_csv(out/"fi_curve_means.csv");selected=json.loads((out/"fi_selection.json").read_text())["gamma"]
    fig,axes=plt.subplots(2,3,figsize=(12,7),layout="constrained")
    for ax,field,label in zip(axes.flat,["frequency_error_hz2_s","nadir_sampled_hz","zenith_sampled_hz","rocof_max_abs_hz_per_s","terminal_error_hz","switched_activation_cost_eur"],
                              ["Integrated error [Hz² s]","Nadir [Hz]","Zenith [Hz]","Max abs RoCoF [Hz/s]","Terminal deviation [Hz]","Activation cost [EUR]"]):
        ax.plot(curve.fi_net_settlement_eur,curve[field],"-o",color=colors[1])
        for r in curve.itertuples(): ax.annotate(str(r.gamma),(r.fi_net_settlement_eur,getattr(r,field)),fontsize=8)
        r=curve[curve.gamma==selected].iloc[0];ax.plot(r.fi_net_settlement_eur,r[field],"*",ms=16,color=colors[2])
        ax.set(xlabel="Mean net FI payment [EUR / 300 s]",ylabel=label);ax.grid(alpha=.2)
        if field=="rocof_max_abs_hz_per_s":
            mid=curve[field].mean();ax.set_ylim(mid-.01,mid+.01)
    fig.suptitle(f"Known reversal, unknown availability | illustrative gamma = {selected:g}")
    save(fig,"figure_3_gamma")
    table=pd.read_csv(out/"fpp_design"/"design_gain_payoffs.csv")
    eq=json.loads((out/"fpp_design"/"gain_equilibrium.json").read_text())
    fig,axes=plt.subplots(1,2,figsize=(11,5),layout="constrained")
    for i,ax in enumerate(axes):
        unit = eq.get("score_unit", "mw_h")
        matrix=table.pivot(index="theta_1",columns="theta_2",values=f"score_{i+1}_{unit}")
        im=ax.imshow(matrix,origin="lower",aspect="auto",cmap="Blues")
        fig.colorbar(im,ax=ax,label="Mean FPP net payoff [EUR]" if unit == "eur" else "Mean FPP coefficient [MW h]")
        for (a,b),v in np.ndenumerate(matrix.to_numpy()):
            ax.text(b,a,f"{v:.3f}",ha="center",va="center",fontsize=7,color="white" if v>matrix.to_numpy().max()*.55 else "black")
        for a,b in eq["pure_profiles"]:
            ax.add_patch(plt.Rectangle((list(matrix.columns).index(b)-.48,list(matrix.index).index(a)-.48),.96,.96,fill=False,edgecolor=colors[2],lw=2))
        ax.set(xticks=range(len(matrix.columns)),xticklabels=matrix.columns,yticks=range(len(matrix.index)),yticklabels=matrix.index,
               xlabel="Provider 2 gain",ylabel="Provider 1 gain",title=f"Provider {i+1} private payoff")
    boundary=any(max(p)==max(eq["catalogue"]) for p in eq["pure_profiles"])
    fig.suptitle("Empirical equilibrium at catalogue boundary" if boundary else "Empirical gain-game equilibria")
    save(fig,"figure_4_payoffs")
