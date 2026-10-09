"""Prepare one reserve block and evolve frequency and individual provider states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp

from .frequency import LinearFrequencyParameters, frequency_derivative_hz_per_s, make_output_times
from .providers import BlockProvider, FCRProviderParameters, droop_gain_mw_per_hz
from .decision import OfferDecision, choose_capacity_offer, evaluate_offer
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
    response_parameters: tuple[FCRProviderParameters, ...]

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
            ) for p in ordered
        )
    else:
        offers_by_id = {o.provider_id: o for o in fixed_offers}
        if len(offers_by_id) != len(fixed_offers) or set(offers_by_id) != {p.provider_id for p in ordered}:
            raise ValueError("fixed offers require exactly the provider identities")
        decisions = tuple(evaluate_offer(
            offers_by_id[p.provider_id], availability=p.availability, cost=p.cost,
            penalty_eur_per_missing_mw_block=p.penalty_eur_per_missing_mw_block,
        ) for p in ordered)
    offers = [d.offer for d in decisions]
    for p, offer in zip(ordered, offers, strict=True):
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
    response_parameters = tuple(
        FCRProviderParameters(
            provider_id=p.provider_id, gain_mw_per_hz=gains[p.provider_id],
            time_constant_s=p.time_constant_s, reserve_mw=settled.effective_reserve_mw,
        ) for p, settled in zip(ordered, settlements, strict=True)
    )
    return PreparedFCRBlock(ordered, decisions, market, settlements, response_parameters)


@dataclass(frozen=True)
class AggregateFCRTrajectory:
    """Trajectory of ``[delta_f, u_1, ..., u_n]`` for one power step."""

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


def simulate_fcr_power_step(
    parameters: LinearFrequencyParameters,
    providers: Sequence[FCRProviderParameters],
    *,
    power_deficit_mw: float,
    start_time_s: float,
    final_time_s: float,
    output_time_step_s: float,
    provider_operating_power_mw: Sequence[float] | None = None,
    maximum_solver_step_s: float | None = None,
    relative_tolerance: float = 1e-9,
    absolute_tolerance: float = 1e-11,
) -> AggregateFCRTrajectory:
    """Numerically solve the coupled aggregate-frequency/FCR equations.

    The permanent positive deficit starts at ``start_time_s``.  The state is
    initialized at the solved operating point, so ``delta_f(0) = 0`` and every
    provider deviation ``u_i(0) = 0``.  Before the event this equilibrium is
    represented exactly; after the event SciPy's adaptive RK45 method solves
    the coupled ODEs. The output grid includes the exact final time even when
    the last output interval is shorter than ``output_time_step_s``.
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

    time_s = make_output_times(final_time_s, output_time_step_s)
    state = np.zeros((time_s.size, number_of_providers + 1), dtype=float)
    post_event_indices = np.flatnonzero(time_s >= start_time_s)
    post_event_times = time_s[post_event_indices]

    def coupled_derivative(
        _time_s: float, current_state: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        delta_frequency_hz = float(current_state[0])
        provider_response_mw = current_state[1:]
        derivative = np.empty_like(current_state)

        # df/dt = (-Ddeltaf -d + sum(u_i) )/Mf
        derivative[0] = frequency_derivative_hz_per_s(
            delta_frequency_hz,
            power_deficit_mw=power_deficit_mw,
            power_response_mw=float(np.sum(provider_response_mw)),
            parameters=parameters,
        )

        # du_i/dt = (clip(-K_i * delta_f, -reserve_i, reserve_i) - u_i) / T_i
        for provider_index, provider in enumerate(provider_tuple):
            derivative[provider_index + 1] = provider.response_derivative_mw_per_s(
                float(provider_response_mw[provider_index]),
                delta_frequency_hz,
            )
        return derivative

    solver_result = solve_ivp(
        coupled_derivative,
        (start_time_s, final_time_s),
        np.zeros(number_of_providers + 1, dtype=float),
        method="RK45",
        # t_span sets the exact event start; t_eval only selects output times.
        t_eval=post_event_times,
        max_step=(
            output_time_step_s
            if maximum_solver_step_s is None
            else maximum_solver_step_s
        ),
        rtol=relative_tolerance,
        atol=absolute_tolerance,
    )
    if not solver_result.success:
        raise RuntimeError(f"FCR integration failed: {solver_result.message}")
    if not np.all(np.isfinite(solver_result.y)):
        raise RuntimeError("FCR integration produced a non-finite state")

    state[post_event_indices, :] = solver_result.y.T

    delta_frequency_hz = state[:, 0]
    provider_response_mw = state[:, 1:]
    provider_requested_response_mw = np.empty_like(provider_response_mw)
    for provider_index, provider in enumerate(provider_tuple):
        provider_requested_response_mw[:, provider_index] = (
            provider.requested_response_mw(delta_frequency_hz)
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
        provider_requested_response_mw=provider_requested_response_mw,
        provider_response_mw=provider_response_mw,
        provider_reserve_mw=provider_reserve_mw,
        total_response_mw=np.sum(provider_response_mw, axis=1),
        provider_active_power_mw=provider_active_power_mw,
        solver_message=str(solver_result.message),
    )
