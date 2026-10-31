"""Prepare one reserve block and evolve frequency and individual provider states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp

from .frequency import LinearFrequencyParameters, frequency_derivative_hz_per_s, make_output_times
from .providers import (BlockProvider, FCRProviderParameters, FIProviderParameters,
                        FPPProviderParameters, droop_gain_mw_per_hz)
from .fpp import DispatchSchedule
from .decision import OfferDecision, choose_capacity_offer, choose_fi_target, evaluate_offer
from .incentives import FIAccountingTrajectory, fi_financial_rates, predict_fi_financials
from .market import (
    FCRCapacityOffer, FCRMarketResult, ReserveSettlement,
    clear_fcr_market, fixed_capacity_allocation, settle_reserve_block,
)


@dataclass(frozen=True)
class ClearedFCRBlock:
    """Market information known before any availability realization."""

    providers: tuple[BlockProvider, ...]
    decisions: tuple[OfferDecision, ...]
    market: FCRMarketResult
    participation_modes: tuple[str, ...]
    gains_mw_per_hz: tuple[float, ...]


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


def clear_fcr_block(
    providers: Sequence[BlockProvider],
    *,
    requirement_mw: float,
    nominal_frequency_hz: float,
    droop_pu: float = 0.05,
    minimum_bid_mw: float = 1.0,
    quantity_tolerance_mw: float = 1e-8,
    fixed_offers: Sequence[FCRCapacityOffer] | None = None,
    fixed_awards_mw: Mapping[str, float] | None = None,
    fixed_price_eur_per_mw_block: float | None = None,
) -> ClearedFCRBlock:
    """Decide, clear and resolve roles without drawing availability.

    Decisions use the known availability distribution, never the sampled state.
    A fcr_or_fi provider enters FI exactly when its FCR award is zero, including
    after a rejected positive offer. Any positive award keeps it exclusively FCR.
    Infeasible procurement stops before any simulation or availability draw.
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
    # Eligibility depends on the award, never on the later availability draw.
    # Keep the original offer/decision for distinguishing opt-out from rejection.
    modes = tuple(
        ("fi" if awards_by_id[p.provider_id].quantity_mw == 0 else "fcr")
        if p.participation == "fcr_or_fi" else p.participation
        for p in ordered
    )
    return ClearedFCRBlock(ordered, decisions, market, modes,
                           tuple(gains[p.provider_id] for p in ordered))


def realize_fcr_block(
    cleared: ClearedFCRBlock, availability_mw: Mapping[str, float],
) -> PreparedFCRBlock:
    """Apply explicit draws to a frozen market; also used by the TSO sample bank."""
    ordered = cleared.providers
    if set(availability_mw) != {p.provider_id for p in ordered}:
        raise ValueError("availability draws require exactly the provider identities")
    for p in ordered:
        available = availability_mw[p.provider_id]
        if not np.isfinite(available) or not 0 <= available <= p.availability.capacity_mw:
            raise ValueError(f"{p.provider_id}: availability draw outside physical capacity")
    awards_by_id = {a.provider_id: a for a in cleared.market.awards}
    settlements = tuple(
        settle_reserve_block(
            awards_by_id[p.provider_id],
            available_mw=availability_mw[p.provider_id],
            cost=p.cost,
            penalty_eur_per_missing_mw_block=p.penalty_eur_per_missing_mw_block,
        ) for p in ordered
    )
    response_parameters = tuple(
        FIProviderParameters(
            provider_id=p.provider_id, time_constant_s=p.time_constant_s,
            reserve_mw=settled.available_mw,
            gamma_eur_per_hz_mw_s=p.fi_gamma_eur_per_hz_mw_s,
            rho_eur_per_mw2_s=p.fi_rho_eur_per_mw2_s,
        ) if mode == "fi" else
        FCRProviderParameters(
            provider_id=p.provider_id, gain_mw_per_hz=gain,
            time_constant_s=p.time_constant_s, reserve_mw=settled.effective_reserve_mw,
        ) for p, settled, mode, gain in zip(
            ordered, settlements, cleared.participation_modes, cleared.gains_mw_per_hz, strict=True)
    )
    return PreparedFCRBlock(ordered, cleared.decisions, cleared.market, settlements,
                            response_parameters, cleared.participation_modes)


def prepare_fcr_block(
    providers: Sequence[BlockProvider], *, requirement_mw: float,
    nominal_frequency_hz: float, rng: np.random.Generator, droop_pu: float = 0.05,
    minimum_bid_mw: float = 1.0, quantity_tolerance_mw: float = 1e-8,
    fixed_offers: Sequence[FCRCapacityOffer] | None = None,
    fixed_awards_mw: Mapping[str, float] | None = None,
    fixed_price_eur_per_mw_block: float | None = None,
) -> PreparedFCRBlock:
    """Clear before sampling once per provider in ID order; preserve fixed-gamma API."""
    cleared = clear_fcr_block(
        providers, requirement_mw=requirement_mw, nominal_frequency_hz=nominal_frequency_hz,
        droop_pu=droop_pu, minimum_bid_mw=minimum_bid_mw,
        quantity_tolerance_mw=quantity_tolerance_mw, fixed_offers=fixed_offers,
        fixed_awards_mw=fixed_awards_mw, fixed_price_eur_per_mw_block=fixed_price_eur_per_mw_block,
    )
    return realize_fcr_block(cleared, {
        p.provider_id: p.availability.sample_mw(rng) for p in cleared.providers})


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
    integrated_squared_frequency_error_hz2_s: NDArray[np.float64]
    rocof_hz_per_s: NDArray[np.float64]  # swing-equation RHS at output times
    initial_rocof_hz_per_s: float  # exact right limit at the disturbance
    fi: FIAccountingTrajectory | None = None
    fpp_provider_ids: tuple[str, ...] = ()
    fpp_activation_cost_eur: NDArray[np.float64] | None = None
    disturbance_limits: tuple[dict[str, float], ...] = ()

    @property
    def total_fi_response_mw(self) -> NDArray[np.float64]:
        indices = [] if self.fi is None else [self.provider_ids.index(i) for i in self.fi.provider_ids]
        return np.sum(self.provider_response_mw[:, indices], axis=1)

    @property
    def total_fcr_response_mw(self) -> NDArray[np.float64]:
        return self.total_response_mw - self.total_fi_response_mw - self.total_fpp_response_mw

    @property
    def total_fpp_response_mw(self) -> NDArray[np.float64]:
        indices = [self.provider_ids.index(i) for i in self.fpp_provider_ids]
        return np.sum(self.provider_response_mw[:, indices], axis=1)


def power_deficit_events(power_deficit_mw, start_time_s, final_time_s, changes=()):
    """Validate prescribed absolute net-deficit levels; negative means surplus.

    Later events replace the preceding level, rather than adding another step.
    They never reset physical states, ledgers, availability or decision clocks.
    """
    events = [(float(start_time_s), float(power_deficit_mw))]
    for change in changes:
        if len(change) != 2:
            raise ValueError("each disturbance change needs time and absolute net deficit")
        time, deficit = map(float, change)
        if not np.isfinite(time) or not np.isfinite(deficit):
            raise ValueError("disturbance times and net deficits must be finite")
        if not events[-1][0] < time < final_time_s:
            raise ValueError("disturbance changes must be strictly ordered after the initial event and before the horizon")
        events.append((time, deficit))
    return np.asarray(events, dtype=float)


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
    dispatch_schedule: DispatchSchedule | None = None,
    measurement_times_s: Sequence[float] = (),
    power_deficit_changes: Sequence[tuple[float, float]] = (),
) -> AggregateFCRTrajectory:
    """Solve coupled frequency, FCR, FI and actual FI settlement dynamics.

    The initial positive deficit starts at ``start_time_s``. Optional prescribed
    ``power_deficit_changes`` replace its net level at later times; negative
    levels represent surplus. The state is
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

    events = power_deficit_events(power_deficit_mw, start_time_s, final_time_s, power_deficit_changes)
    event_limits = []

    def deficit_at(time):
        index = np.searchsorted(events[:, 0], time, side="right") - 1
        return events[index, 1] if index >= 0 else 0.

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

    if dispatch_schedule is not None:
        if dispatch_schedule.provider_ids != provider_ids or dispatch_schedule.final_time_s != final_time_s:
            raise ValueError("dispatch schedule identities/horizon must match the simulation")
        if operating_power is None or not np.allclose(
                operating_power, dispatch_schedule.initial_operating_power_mw, rtol=0, atol=1e-9):
            raise ValueError("dispatch schedule must start from the supplied solved operating powers")
        if np.any(np.array([p.reserve_mw for p in provider_tuple]) > dispatch_schedule.capacity_mw):
            raise ValueError("response bounds exceed the schedule's validated availability support")
    time_s = make_output_times(final_time_s, output_time_step_s)
    measurements = np.asarray(measurement_times_s, dtype=float)
    if (measurements.ndim != 1 or not np.all(np.isfinite(measurements))
            or np.any((measurements < 0) | (measurements > final_time_s))):
        raise ValueError("measurement times must be finite and within the simulation")
    if measurements.size:
        time_s = np.unique(np.concatenate((time_s, measurements)))
    fpp_indices = [j for j, p in enumerate(provider_tuple) if isinstance(p, FPPProviderParameters)]
    sampled_fpp = [j for j in fpp_indices if provider_tuple[j].theta is not None]
    decision_interval = fi_decision_interval_s if fi_count else None
    if sampled_fpp:
        if fi_count and fi_decision_interval_s != 0.1:
            raise ValueError("joint FI/FPP simulation requires a common 0.1 s control clock")
        decision_interval = 0.1
    physical_size = number_of_providers + 1
    # Ledger blocks: gross rewards, positive penalty magnitudes, activation costs.
    ledger_end = physical_size + 3 * fi_count
    # Last state integrates live frequency error, independently of CSV sampling.
    state = np.zeros((time_s.size, ledger_end + len(fpp_indices) + 1), dtype=float)
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
            if j in sampled_fpp:
                derivative[j + 1] = (targets[j] - responses[j]) / provider.time_constant_s
                continue
            signal = targets[j] if isinstance(provider, FIProviderParameters) else delta_f
            derivative[j + 1] = provider.response_derivative_mw_per_s(float(responses[j]), signal)
        for column, (j, provider) in enumerate(zip(fi_indices, fi_providers, strict=True)):
            rates = fi_financial_rates(provider, sampled_error, float(responses[j]))
            derivative[physical_size + column:ledger_end:fi_count] = rates
        for column, j in enumerate(fpp_indices):
            derivative[ledger_end + column] = provider_tuple[j].rho_eur_per_mw2_s * responses[j]**2 / 2
        derivative[-1] = delta_f**2
        return derivative

    boundaries = (make_output_times(final_time_s, decision_interval)
                  if decision_interval is not None else np.array([start_time_s, final_time_s]))
    for left, right in zip(boundaries[:-1], boundaries[1:], strict=True):
        duration = float(right - left)
        sampled_error = float(current[0])
        for j in sampled_fpp:
            targets[j] = provider_tuple[j].requested_response_mw(sampled_error)
        initial_responses = current[1:physical_size].copy()
        initial_ledger = current[physical_size:ledger_end].reshape(3, fi_count).copy()
        decisions = [choose_fi_target(
            provider, sampled_frequency_error_hz=sampled_error,
            initial_response_mw=float(initial_responses[j]), duration_s=duration,
        ) for j, provider in zip(fi_indices, fi_providers, strict=True)]
        for j, decision in zip(fi_indices, decisions, strict=True):
            targets[j] = decision.target_mw

        # An off-clock disturbance must not trigger an extra provider decision.
        segments = [left, right]
        segments.extend(events[(events[:, 0] > left) & (events[:, 0] < right), 0])
        # Gross rewards/charges have a derivative kink at a response zero.
        # Under a held FI target its time is known exactly. Splitting there
        # preserves ledger accuracy without changing the decision clock.
        for j, provider in zip(fi_indices, fi_providers, strict=True):
            initial, target = initial_responses[j], targets[j]
            if initial * target < 0:
                crossing = left + provider.time_constant_s * np.log1p(-initial / target)
                if left < crossing < right:
                    segments.append(float(crossing))
        segments = sorted(set(segments))
        for segment_left, segment_right in zip(segments[:-1], segments[1:], strict=True):
            segment_deficit = deficit_at(segment_left)
            event_index = np.searchsorted(events[:, 0], segment_left)
            if event_index < len(events) and events[event_index, 0] == segment_left:
                previous = events[event_index - 1, 1] if event_index else 0.
                limits = [frequency_derivative_hz_per_s(float(current[0]),
                    power_deficit_mw=level, power_response_mw=float(current[1:physical_size].sum()),
                    parameters=parameters) for level in (previous, segment_deficit)]
                event_limits.append(dict(time_s=float(segment_left), deficit_before_mw=float(previous),
                    deficit_after_mw=float(segment_deficit), delta_frequency_hz=float(current[0]),
                    total_response_mw=float(current[1:physical_size].sum()),
                    rocof_left_hz_per_s=float(limits[0]), rocof_right_hz_per_s=float(limits[1])))
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
        for j in sampled_fpp:
            requested[interval_mask, j] = targets[j]
        elapsed = time_s[interval_mask] - left
        actual = current[physical_size:ledger_end].reshape(3, fi_count) - initial_ledger
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
        if isinstance(provider, FCRProviderParameters) and j not in sampled_fpp:
            requested[:, j] = provider.requested_response_mw(delta_frequency_hz)
    accounting = None
    if fi_count:
        ledger = state[:, physical_size:ledger_end].reshape(time_s.size, 3, fi_count)
        payment_rates = (-sampled_frequency * np.array([p.gamma_eur_per_hz_mw_s for p in fi_providers])
                         * provider_response_mw[:, fi_indices])
        accounting = FIAccountingTrajectory(
            tuple(p.provider_id for p in fi_providers), sampled_frequency, payment_rates,
            ledger[:, 0], ledger[:, 1], ledger[:, 2], benchmark, prediction, tuple(interval_records),
        )

    event_indices = np.searchsorted(events[:, 0], time_s, side="right") - 1
    power_deficit = np.where(event_indices >= 0, events[np.maximum(event_indices, 0), 1], 0.)
    provider_reserve_mw = np.asarray(
        [provider.reserve_mw for provider in provider_tuple], dtype=float
    )
    provider_active_power_mw = (
        None
        if operating_power is None
        else operating_power[np.newaxis, :] + provider_response_mw
    )
    if dispatch_schedule is not None:
        # Meter the actual model output P0 + delivered response. Dispatch targets
        # define settlement references only; they do not force physical tracking.
        dispatch_schedule.check_total_power(provider_active_power_mw)

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
        integrated_squared_frequency_error_hz2_s=state[:, -1],
        rocof_hz_per_s=frequency_derivative_hz_per_s(
            delta_frequency_hz, power_deficit_mw=power_deficit,
            power_response_mw=np.sum(provider_response_mw, axis=1), parameters=parameters),
        initial_rocof_hz_per_s=frequency_derivative_hz_per_s(
            0., power_deficit_mw=power_deficit_mw, power_response_mw=0., parameters=parameters),
        fi=accounting,
        fpp_provider_ids=tuple(provider_ids[j] for j in fpp_indices),
        fpp_activation_cost_eur=(state[:, ledger_end:-1] if fpp_indices else None),
        disturbance_limits=tuple(event_limits),
    )
