"""Prepare one reserve block and evolve frequency and individual provider states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp

from .frequency import LinearFrequencyParameters, frequency_derivative_hz_per_s, make_output_times
from .providers import BlockProvider, FCRProviderParameters, FIProviderParameters, droop_gain_mw_per_hz
from .decision import OfferDecision, choose_capacity_offer, choose_fi_target, evaluate_offer
from .incentives import FIAccountingTrajectory, fi_financial_rates, predict_fi_financials
from .market import (
    FCRCapacityOffer, FCRMarketResult, ReserveSettlement,
    clear_fcr_market, fixed_capacity_allocation, settle_reserve_block,
)


@dataclass(frozen=True)
class PreparedFCRBlock:
    providers: tuple[BlockProvider, ...]
    decisions: tuple[OfferDecision, ...]
    market: FCRMarketResult
    settlements: tuple[ReserveSettlement, ...]
    response_parameters: tuple[FCRProviderParameters | FIProviderParameters, ...]
    participation_modes: tuple[str, ...]

    @property
    def operating_powers_mw(self) -> tuple[float, ...]:
        return tuple(p.operating_power_mw for p in self.providers)


def prepare_fcr_block(
    providers: Sequence[BlockProvider],
    *,
    requirement_mw: float,
    nominal_frequency_hz: float,
    rng: np.random.Generator,
    droop_pu: float = 0.05,
    minimum_bid_mw: float = 1.0,
    quantity_tolerance_mw: float = 1e-8,
    fixed_offers: Sequence[FCRCapacityOffer] | None = None,
    fixed_awards_mw: Mapping[str, float] | None = None,
    fixed_price_eur_per_mw_block: float | None = None,
) -> PreparedFCRBlock:
    """Decide, clear, then sample exactly once per provider in stable ID order.

    Decisions use the known availability distribution, never the sampled state.
    No draw is made when procurement is infeasible. Keep the returned block
    when comparing solver tolerances; resampling would change the experiment.
    """
    if not np.isfinite(minimum_bid_mw) or minimum_bid_mw <= 0:
        raise ValueError("minimum_bid_mw must be finite and positive")
    ordered = tuple(sorted(providers, key=lambda p: p.provider_id))
    for ids in ([p.provider_id for p in ordered], [p.generator_id for p in ordered]):
        if len(ids) != len(set(ids)):
            raise ValueError("provider and generator identities must be unique")
    gains = {
        p.provider_id: (droop_gain_mw_per_hz(p.power_base_mw, nominal_frequency_hz, droop_pu)
                        if p.gain_mw_per_hz is None else p.gain_mw_per_hz)
        for p in ordered
    }
    if fixed_offers is None:
        decisions = tuple(
            choose_capacity_offer(
                p.provider_id, availability=p.availability, cost=p.cost,
                penalty_eur_per_missing_mw_block=p.penalty_eur_per_missing_mw_block,
                minimum_bid_price_eur_per_mw_block=p.minimum_bid_price_eur_per_mw_block,
                maximum_bid_price_eur_per_mw_block=p.maximum_bid_price_eur_per_mw_block,
                maximum_offer_mw=p.maximum_offer_mw, minimum_bid_mw=minimum_bid_mw,
                quantity_tolerance_mw=quantity_tolerance_mw,
            ) if p.participation in ("fcr", "fcr_or_fi") else evaluate_offer(
                FCRCapacityOffer(p.provider_id, 0.0, 0.0), availability=p.availability,
                cost=p.cost, penalty_eur_per_missing_mw_block=p.penalty_eur_per_missing_mw_block,
            ) for p in ordered
        )
    else:
        if any(p.participation == "fcr_or_fi" for p in ordered):
            raise ValueError("fcr_or_fi requires optimized offers to identify a voluntary opt-out")
        offers_by_id = {o.provider_id: o for o in fixed_offers}
        if len(offers_by_id) != len(fixed_offers) or set(offers_by_id) != {p.provider_id for p in ordered}:
            raise ValueError("fixed offers require exactly the provider identities")
        decisions = tuple(evaluate_offer(
            offers_by_id[p.provider_id], availability=p.availability, cost=p.cost,
            penalty_eur_per_missing_mw_block=p.penalty_eur_per_missing_mw_block,
        ) for p in ordered)
    offers = [d.offer for d in decisions]
    for p, offer in zip(ordered, offers, strict=True):
        if p.participation in ("fi", "inactive") and offer.quantity_mw != 0:
            raise ValueError(f"{p.provider_id}: FI/inactive providers require zero FCR offers")
        if offer.quantity_mw != 0 and not minimum_bid_mw <= offer.quantity_mw <= p.maximum_offer_mw:
            raise ValueError(f"{p.provider_id}: offer must be zero or between minimum bid and maximum offer")
    if fixed_awards_mw is None:
        if fixed_price_eur_per_mw_block is not None:
            raise ValueError("a fixed price requires fixed awards")
        market = clear_fcr_market(offers, requirement_mw)
    else:
        if fixed_price_eur_per_mw_block is None:
            raise ValueError("fixed awards require an explicit price")
        market = fixed_capacity_allocation(
            offers, requirement_mw, fixed_awards_mw, fixed_price_eur_per_mw_block)
    if not market.feasible:
        raise ValueError(f"Insufficient offered reserve: shortage {market.shortage_mw:g} MW")
    awards_by_id = {award.provider_id: award for award in market.awards}
    settlements = tuple(
        settle_reserve_block(
            awards_by_id[p.provider_id],
            available_mw=p.availability.sample_mw(rng),
            cost=p.cost,
            penalty_eur_per_missing_mw_block=p.penalty_eur_per_missing_mw_block,
        ) for p in ordered
    )
    modes = tuple(
        ("fi" if decision.offer.quantity_mw == 0 else "fcr")
        if p.participation == "fcr_or_fi" else p.participation
        for p, decision in zip(ordered, decisions, strict=True)
    )
    response_parameters = tuple(
        FIProviderParameters(
            provider_id=p.provider_id, time_constant_s=p.time_constant_s,
            reserve_mw=settled.available_mw,
            gamma_eur_per_hz_mw_s=p.fi_gamma_eur_per_hz_mw_s,
            rho_eur_per_mw2_s=p.fi_rho_eur_per_mw2_s,
        ) if mode == "fi" else
        FCRProviderParameters(
            provider_id=p.provider_id, gain_mw_per_hz=gains[p.provider_id],
            time_constant_s=p.time_constant_s, reserve_mw=settled.effective_reserve_mw,
        ) for p, settled, mode in zip(ordered, settlements, modes, strict=True)
    )
    return PreparedFCRBlock(ordered, decisions, market, settlements, response_parameters, modes)


@dataclass(frozen=True)
class AggregateFCRTrajectory:
    """One aggregate frequency, each delivered FCR/FI response, and FI ledgers."""

    time_s: NDArray[np.float64]
    delta_frequency_hz: NDArray[np.float64]
    frequency_hz: NDArray[np.float64]
    power_deficit_mw: NDArray[np.float64]
    provider_ids: tuple[str, ...]
    provider_requested_response_mw: NDArray[np.float64]
    provider_response_mw: NDArray[np.float64]
    provider_reserve_mw: NDArray[np.float64]  # effective physical limits
    total_response_mw: NDArray[np.float64]
    provider_active_power_mw: NDArray[np.float64] | None  # P_i,0 + u_i
    solver_message: str
    fi: FIAccountingTrajectory | None = None

    @property
    def total_fi_response_mw(self) -> NDArray[np.float64]:
        indices = [] if self.fi is None else [self.provider_ids.index(i) for i in self.fi.provider_ids]
        return np.sum(self.provider_response_mw[:, indices], axis=1)

    @property
    def total_fcr_response_mw(self) -> NDArray[np.float64]:
        return self.total_response_mw - self.total_fi_response_mw


def simulate_fcr_power_step(
    parameters: LinearFrequencyParameters,
    providers: Sequence[FCRProviderParameters | FIProviderParameters],
    *,
    power_deficit_mw: float,
    start_time_s: float,
    final_time_s: float,
    output_time_step_s: float,
    provider_operating_power_mw: Sequence[float] | None = None,
    maximum_solver_step_s: float | None = None,
    fi_decision_interval_s: float | None = None,
    relative_tolerance: float = 1e-9,
    absolute_tolerance: float = 1e-11,
) -> AggregateFCRTrajectory:
    """Solve coupled frequency, FCR, FI and actual FI settlement dynamics.

    The permanent positive deficit starts at ``start_time_s``.  The state is
    initialized at the solved operating point, so ``delta_f(0) = 0`` and every
    provider deviation ``u_i(0) = 0``.  Before the event this equilibrium is
    represented exactly in FCR-only runs. FI decisions occur on their own
    clock starting at zero, with a shortened final interval. RK45 restarts at
    decisions and the disturbance, preserving the physical state and ledgers.
    Output times only sample the solution; they do not define FI settlement.
    """

    for name, value in (
        ("power_deficit_mw", power_deficit_mw),
        ("start_time_s", start_time_s),
        ("final_time_s", final_time_s),
        ("output_time_step_s", output_time_step_s),
        ("relative_tolerance", relative_tolerance),
        ("absolute_tolerance", absolute_tolerance),
    ):
        if not np.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if maximum_solver_step_s is not None and not np.isfinite(maximum_solver_step_s):
        raise ValueError("maximum_solver_step_s must be finite")
    if power_deficit_mw < 0.0:
        raise ValueError("power_deficit_mw cannot be negative")
    if start_time_s < 0.0:
        raise ValueError("start_time_s cannot be negative")
    if final_time_s <= start_time_s:
        raise ValueError("final_time_s must be greater than start_time_s")
    if output_time_step_s <= 0.0:
        raise ValueError("output_time_step_s must be positive")
    if maximum_solver_step_s is not None and maximum_solver_step_s <= 0.0:
        raise ValueError("maximum_solver_step_s must be positive")
    if relative_tolerance <= 0.0 or absolute_tolerance <= 0.0:
        raise ValueError("solver tolerances must be positive")

    provider_tuple = tuple(providers)
    provider_ids = tuple(provider.provider_id for provider in provider_tuple)
    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("provider_id values must be unique")

    number_of_providers = len(provider_tuple)
    if provider_operating_power_mw is None:
        operating_power = None
    else:
        operating_power = np.asarray(provider_operating_power_mw, dtype=float)
        if operating_power.shape != (number_of_providers,):
            raise ValueError(
                "provider_operating_power_mw must contain one value per provider"
            )
        if not np.all(np.isfinite(operating_power)):
            raise ValueError("provider_operating_power_mw must be finite")

    fi_indices = [j for j, p in enumerate(provider_tuple) if isinstance(p, FIProviderParameters)]
    fi_providers = tuple(provider_tuple[j] for j in fi_indices)
    fi_count = len(fi_indices)
    if fi_decision_interval_s is not None and (
        not np.isfinite(fi_decision_interval_s) or fi_decision_interval_s <= 0
    ):
        raise ValueError("fi_decision_interval_s must be finite and positive")
    if fi_count and fi_decision_interval_s is None:
        raise ValueError("FI providers require fi_decision_interval_s")

    time_s = make_output_times(final_time_s, output_time_step_s)
    physical_size = number_of_providers + 1
    # Ledger blocks: gross rewards, positive penalty magnitudes, activation costs.
    state = np.zeros((time_s.size, physical_size + 3 * fi_count), dtype=float)
    current = np.zeros(state.shape[1], dtype=float)
    requested = np.zeros((time_s.size, number_of_providers), dtype=float)
    sampled_frequency = np.zeros((time_s.size, fi_count), dtype=float)
    benchmark = np.zeros_like(sampled_frequency)
    prediction = np.zeros_like(sampled_frequency)
    benchmark_total = np.zeros(fi_count)
    prediction_total = np.zeros(fi_count)
    interval_records = []
    targets = np.zeros(number_of_providers)
    sampled_error = 0.0
    segment_deficit = 0.0

    def coupled_derivative(_time_s: float, current_state: NDArray[np.float64]) -> NDArray[np.float64]:
        delta_f = float(current_state[0])
        responses = current_state[1:physical_size]
        derivative = np.empty_like(current_state)
        derivative[0] = frequency_derivative_hz_per_s(
            delta_f, power_deficit_mw=segment_deficit,
            power_response_mw=float(np.sum(responses)), parameters=parameters,
        )
        for j, provider in enumerate(provider_tuple):
            signal = targets[j] if isinstance(provider, FIProviderParameters) else delta_f
            derivative[j + 1] = provider.response_derivative_mw_per_s(float(responses[j]), signal)
        for column, (j, provider) in enumerate(zip(fi_indices, fi_providers, strict=True)):
            rates = fi_financial_rates(provider, sampled_error, float(responses[j]))
            derivative[physical_size + column::fi_count] = rates
        return derivative

    boundaries = (make_output_times(final_time_s, fi_decision_interval_s)
                  if fi_count else np.array([start_time_s, final_time_s]))
    for left, right in zip(boundaries[:-1], boundaries[1:], strict=True):
        duration = float(right - left)
        sampled_error = float(current[0])
        initial_responses = current[1:physical_size].copy()
        initial_ledger = current[physical_size:].reshape(3, fi_count).copy()
        decisions = [choose_fi_target(
            provider, sampled_frequency_error_hz=sampled_error,
            initial_response_mw=float(initial_responses[j]), duration_s=duration,
        ) for j, provider in zip(fi_indices, fi_providers, strict=True)]
        for j, decision in zip(fi_indices, decisions, strict=True):
            targets[j] = decision.target_mw

        # An off-clock disturbance must not trigger an extra provider decision.
        segments = [left, right]
        if left < start_time_s < right:
            segments.insert(1, start_time_s)
        for segment_left, segment_right in zip(segments[:-1], segments[1:], strict=True):
            segment_deficit = power_deficit_mw if segment_left >= start_time_s else 0.0
            solver_result = solve_ivp(
                coupled_derivative, (segment_left, segment_right), current,
                method="RK45", dense_output=True,
                max_step=(output_time_step_s if maximum_solver_step_s is None else maximum_solver_step_s),
                rtol=relative_tolerance, atol=absolute_tolerance,
            )
            if not solver_result.success:
                raise RuntimeError(f"Frequency/settlement integration failed: {solver_result.message}")
            if not np.all(np.isfinite(solver_result.y)):
                raise RuntimeError("Frequency/settlement integration produced a non-finite state")
            mask = (time_s >= segment_left) & (
                (time_s <= segment_right) if segment_right == final_time_s else (time_s < segment_right))
            if np.any(mask):
                state[mask] = solver_result.sol(time_s[mask]).T
            current = solver_result.y[:, -1].copy()

        interval_mask = (time_s >= left) & ((time_s <= right) if right == final_time_s else (time_s < right))
        elapsed = time_s[interval_mask] - left
        actual = current[physical_size:].reshape(3, fi_count) - initial_ledger
        for column, (j, provider, decision) in enumerate(zip(fi_indices, fi_providers, decisions, strict=True)):
            requested[interval_mask, j] = decision.target_mw
            sampled_frequency[interval_mask, column] = sampled_error
            price = -provider.gamma_eur_per_hz_mw_s * sampled_error
            benchmark[interval_mask, column] = benchmark_total[column] + price * decision.target_mw * elapsed
            prediction[interval_mask, column] = prediction_total[column] + np.array([
                predict_fi_financials(
                    provider, sampled_frequency_error_hz=sampled_error,
                    initial_response_mw=float(initial_responses[j]), target_mw=decision.target_mw,
                    duration_s=float(h),
                )[0] for h in elapsed
            ])
            rewards, penalties, cost = actual[:, column]
            interval_records.append({
                "provider_id": provider.provider_id, "start_time_s": float(left), "end_time_s": float(right),
                "sampled_frequency_error_hz": sampled_error,
                "initial_response_mw": float(initial_responses[j]), "target_mw": decision.target_mw,
                "final_response_mw": float(current[j + 1]),
                "target_benchmark_eur": decision.target_benchmark_eur,
                "predicted_settlement_eur": decision.predicted_settlement_eur,
                "predicted_activation_cost_eur": decision.predicted_activation_cost_eur,
                "predicted_profit_eur": decision.predicted_profit_eur,
                "gross_rewards_eur": float(rewards), "gross_penalties_eur": float(penalties),
                "net_settlement_eur": float(rewards - penalties), "activation_cost_eur": float(cost),
                "profit_eur": float(rewards - penalties - cost),
            })
            benchmark_total[column] += decision.target_benchmark_eur
            prediction_total[column] += decision.predicted_settlement_eur

    delta_frequency_hz = state[:, 0]
    provider_response_mw = state[:, 1:physical_size]
    for j, provider in enumerate(provider_tuple):
        if isinstance(provider, FCRProviderParameters):
            requested[:, j] = provider.requested_response_mw(delta_frequency_hz)
    accounting = None
    if fi_count:
        ledger = state[:, physical_size:].reshape(time_s.size, 3, fi_count)
        payment_rates = (-sampled_frequency * np.array([p.gamma_eur_per_hz_mw_s for p in fi_providers])
                         * provider_response_mw[:, fi_indices])
        accounting = FIAccountingTrajectory(
            tuple(p.provider_id for p in fi_providers), sampled_frequency, payment_rates,
            ledger[:, 0], ledger[:, 1], ledger[:, 2], benchmark, prediction, tuple(interval_records),
        )

    step_is_active = time_s >= start_time_s
    power_deficit = np.where(step_is_active, power_deficit_mw, 0.0)
    provider_reserve_mw = np.asarray(
        [provider.reserve_mw for provider in provider_tuple], dtype=float
    )
    provider_active_power_mw = (
        None
        if operating_power is None
        else operating_power[np.newaxis, :] + provider_response_mw
    )

    return AggregateFCRTrajectory(
        time_s=time_s,
        delta_frequency_hz=delta_frequency_hz,
        frequency_hz=parameters.nominal_frequency_hz + delta_frequency_hz,
        power_deficit_mw=power_deficit,
        provider_ids=provider_ids,
        provider_requested_response_mw=requested,
        provider_response_mw=provider_response_mw,
        provider_reserve_mw=provider_reserve_mw,
        total_response_mw=np.sum(provider_response_mw, axis=1),
        provider_active_power_mw=provider_active_power_mw,
        solver_message=str(solver_result.message),
        fi=accounting,
    )
