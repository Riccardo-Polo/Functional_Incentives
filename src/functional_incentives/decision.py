"""FCR offer rewards and myopic FI target decisions with predicted response lag."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from scipy.optimize import minimize_scalar

from .availability import GaussianReserve
from .providers import QuadraticCommitmentCost, FIProviderParameters, first_order_response_integrals
from .incentives import (predict_fi_financials, l2_endpoint_payment, l2_passive_cost,
                         L2_CONTRACT, validate_l2_contract)
from .market import FCRCapacityOffer


@dataclass(frozen=True)
class OfferDecision:
    offer: FCRCapacityOffer
    expected_shortfall_mw: float
    commitment_cost_eur: float
    expected_penalty_eur: float
    expected_reward_eur: float


def evaluate_offer(
    offer: FCRCapacityOffer, *, availability: GaussianReserve,
    cost: QuadraticCommitmentCost, penalty_eur_per_missing_mw_block: float,
) -> OfferDecision:
    """Evaluate either a candidate or an explicitly supplied fixed offer."""
    penalty = penalty_eur_per_missing_mw_block
    if not isfinite(penalty) or penalty < 0:
        raise ValueError("penalty coefficient must be finite and nonnegative")
    shortfall = availability.expected_shortfall_mw(offer.quantity_mw)
    commitment_cost = cost.total_eur(offer.quantity_mw)
    expected_penalty = penalty * shortfall
    reward = offer.price_eur_per_mw_block * offer.quantity_mw - commitment_cost - expected_penalty
    if not isfinite(reward):
        raise ValueError("offer reward must be finite")
    return OfferDecision(offer, shortfall, commitment_cost, expected_penalty, reward)


def choose_capacity_offer(
    provider_id: str,
    *,
    availability: GaussianReserve,
    cost: QuadraticCommitmentCost,
    penalty_eur_per_missing_mw_block: float,
    minimum_bid_price_eur_per_mw_block: float,
    maximum_bid_price_eur_per_mw_block: float,
    maximum_offer_mw: float,
    minimum_bid_mw: float = 1.0,
    quantity_tolerance_mw: float = 1e-8,
) -> OfferDecision:
    """Maximize reward over {0} union [minimum_bid_mw, maximum_offer_mw].

    Positive bids use the price ceiling: dR/dc = q > 0. With nonnegative
    quadratic cost and penalty, R is concave in q. Bounded scalar minimization
    of -R therefore suffices; explicitly check endpoints and deterministic
    availability's kink. Retain zero unless a positive offer strictly improves
    its reward, so nonpositive rewards opt out. Exact ties prefer smaller quantities.
    This heuristic has no acceptance model and never samples availability.
    """
    penalty = penalty_eur_per_missing_mw_block
    if not isfinite(penalty) or penalty < 0:
        raise ValueError("penalty coefficient must be finite and nonnegative")
    price_min = minimum_bid_price_eur_per_mw_block
    price_max = maximum_bid_price_eur_per_mw_block
    if not all(isfinite(c) for c in (price_min, price_max)) or not 0 <= price_min <= price_max:
        raise ValueError("bid price interval must be finite, nonnegative and ordered")
    if not isfinite(maximum_offer_mw) or not 0 <= maximum_offer_mw <= availability.capacity_mw:
        raise ValueError("maximum offer must lie within physical reserve capacity")
    if not isfinite(minimum_bid_mw) or minimum_bid_mw <= 0:
        raise ValueError("minimum_bid_mw must be finite and positive")
    if not isfinite(quantity_tolerance_mw) or quantity_tolerance_mw <= 0:
        raise ValueError("quantity_tolerance_mw must be finite and positive")

    def evaluate(quantity: float) -> OfferDecision:
        return evaluate_offer(
            FCRCapacityOffer(provider_id, price_max if quantity > 0 else price_min, quantity),
            availability=availability, cost=cost,
            penalty_eur_per_missing_mw_block=penalty,
        )

    best = evaluate(0.0)
    if maximum_offer_mw < minimum_bid_mw:
        return best
    quantities = {minimum_bid_mw, maximum_offer_mw}
    if minimum_bid_mw < maximum_offer_mw:
        optimum = minimize_scalar(
            lambda q: -evaluate(float(q)).expected_reward_eur,
            bounds=(minimum_bid_mw, maximum_offer_mw), method="bounded",
            options={"xatol": quantity_tolerance_mw},
        )
        if (not optimum.success or not isfinite(optimum.fun)
                or not minimum_bid_mw <= optimum.x <= maximum_offer_mw):
            raise RuntimeError("bounded offer optimization did not converge")
        quantities.add(float(optimum.x))
    if availability.variance_mw2 == 0:
        available = min(availability.capacity_mw, max(0.0, availability.mean_mw))
        if minimum_bid_mw <= available <= maximum_offer_mw:
            quantities.add(available)
    for quantity in sorted(quantities):
        candidate = evaluate(quantity)
        if candidate.expected_reward_eur > best.expected_reward_eur:
            best = candidate
    return best


@dataclass(frozen=True)
class FITargetDecision:
    target_mw: float
    target_benchmark_eur: float
    predicted_settlement_eur: float
    predicted_activation_cost_eur: float

    @property
    def predicted_profit_eur(self) -> float:
        return self.predicted_settlement_eur - self.predicted_activation_cost_eur


@dataclass(frozen=True)
class L2TargetDecision:
    target_mw: float
    unconstrained_target_mw: float
    alpha_hz: float
    beta_hz_per_mw: float
    predicted_settlement_eur: float
    predicted_activation_cost_eur: float
    predicted_performance_payment_eur: float
    passive_activation_cost_eur: float
    passive_cost_reimbursement_eur: float

    @property
    def predicted_profit_eur(self):
        return self.predicted_settlement_eur - self.predicted_activation_cost_eur


def choose_l2_target(provider: FIProviderParameters, *, weight_eur_per_hz2: float,
                     sampled_frequency_error_hz: float, sampled_rocof_hz_per_s: float,
                     frequency_mass_mw_s_per_hz: float, initial_response_mw: float,
                     duration_s: float, l2_contract: str = L2_CONTRACT) -> L2TargetDecision:
    """Week 9 causal affine predictor and exact quadratic delivered-power cost.

    Other power-balance terms are frozen only in the predictor. Current model
    RoCoF includes the own initial response, which must not be counted twice.
    No future disturbance, FCR response or frequency enters this decision.
    """
    validate_l2_contract(l2_contract)
    values = (weight_eur_per_hz2, sampled_frequency_error_hz, sampled_rocof_hz_per_s,
              frequency_mass_mw_s_per_hz, initial_response_mw, duration_s)
    if (not all(isfinite(x) for x in values) or weight_eur_per_hz2 < 0
            or frequency_mass_mw_s_per_hz <= 0 or duration_s <= 0
            or abs(initial_response_mw) > provider.reserve_mw + 1e-8):
        raise ValueError("invalid L2 decision inputs")
    _, b, aa, ab, bb = first_order_response_integrals(provider.time_constant_s, duration_s)
    if bb <= 0:
        raise ValueError("L2 interval is too small relative to the response time")
    alpha = (sampled_frequency_error_hz + duration_s * sampled_rocof_hz_per_s
             - b * initial_response_mw / frequency_mass_mw_s_per_hz)
    beta = b / frequency_mass_mw_s_per_hz
    rho = provider.rho_eur_per_mw2_s
    unconstrained = -(2 * weight_eur_per_hz2 * beta * alpha + rho * ab * initial_response_mw) / (
        2 * weight_eur_per_hz2 * beta**2 + rho * bb)
    target = float(max(-provider.reserve_mw, min(provider.reserve_mw, unconstrained)))
    cost = .5 * rho * max(0., aa * initial_response_mw**2
                          + 2 * ab * initial_response_mw * target + bb * target**2)
    performance = l2_endpoint_payment(weight_eur_per_hz2, alpha, alpha + beta * target)
    passive_cost = l2_passive_cost(provider, initial_response_mw, duration_s)
    reimbursement = passive_cost if l2_contract == L2_CONTRACT else 0.
    payment = performance + reimbursement
    if not all(isfinite(x) for x in (alpha, beta, unconstrained, cost, payment)):
        raise ValueError("L2 decision produced a non-finite value")
    return L2TargetDecision(target, float(unconstrained), float(alpha), float(beta), payment,
                            float(cost), performance, passive_cost, reimbursement)


def choose_l2_window_targets(provider: FIProviderParameters, *, durations_s,
                            sampled_frequency_error_hz: float, sampled_rocof_hz_per_s: float,
                            frequency_mass_mw_s_per_hz: float, initial_response_mw: float,
                            weight_eur_per_hz2: float):
    """Causal bounded plan to the next settlement, with exact actuator costs.

    Freeze external balance as in the original affine predictor. Replan at
    each control tick and apply only the first target. No terminal value beyond
    settlement is imposed. Return the plan and predicted endpoint/cost.
    """
    import numpy as np
    from scipy.optimize import lsq_linear
    durations = np.asarray(durations_s, dtype=float)
    if (durations.ndim != 1 or not len(durations) or np.any(durations <= 0)
            or not np.all(np.isfinite(durations))):
        raise ValueError("window plan requires positive finite durations")
    values = [sampled_frequency_error_hz, sampled_rocof_hz_per_s,
              frequency_mass_mw_s_per_hz, initial_response_mw, weight_eur_per_hz2]
    if (not np.all(np.isfinite(values)) or frequency_mass_mw_s_per_hz <= 0
            or weight_eur_per_hz2 < 0 or abs(initial_response_mw) > provider.reserve_mw+1e-8):
        raise ValueError("invalid L2 window predictor inputs")
    n = len(durations)
    # q = [1, v_0, ..., v_(n-1)]; response and its integral are affine in q.
    response = np.zeros(n+1); response[0] = initial_response_mw
    energy = np.zeros(n+1); quadratic = np.zeros((n+1, n+1))
    for i, h in enumerate(durations):
        a, b, aa, ab, bb = first_order_response_integrals(provider.time_constant_s, h)
        basis = np.eye(n+1)[i+1]
        energy += a*response+b*basis
        quadratic += aa*np.outer(response,response)+ab*(np.outer(response,basis)+np.outer(basis,response))+bb*np.outer(basis,basis)
        decay = np.exp(-h/provider.time_constant_s)
        response = decay*response+(1-decay)*basis
    endpoint = energy/frequency_mass_mw_s_per_hz
    endpoint[0] += (sampled_frequency_error_hz+durations.sum()*sampled_rocof_hz_per_s
                    -durations.sum()*initial_response_mw/frequency_mass_mw_s_per_hz)
    objective = weight_eur_per_hz2*np.outer(endpoint,endpoint)+provider.rho_eur_per_mw2_s*quadratic/2
    if provider.reserve_mw == 0:
        targets = np.zeros(n)
    else:
        chol = np.linalg.cholesky(objective[1:,1:])
        answer = lsq_linear(chol.T, -np.linalg.solve(chol,objective[1:,0]),
            bounds=(-provider.reserve_mw,provider.reserve_mw), tol=1e-12, max_iter=200)
        if not answer.success:
            raise RuntimeError("L2 window target optimization failed")
        targets = answer.x
    q = np.r_[1.,targets]
    return targets, float(endpoint@q), float(provider.rho_eur_per_mw2_s*(q@quadratic@q)/2)


def choose_fi_target(
    provider: FIProviderParameters, *, sampled_frequency_error_hz: float,
    initial_response_mw: float, duration_s: float,
) -> FITargetDecision:
    """Exact constrained maximizer of integral(-gamma*e_k*z - rho*z^2/2).

    For z=a*z0+b*v the objective is a concave quadratic in the held target v.
    Differentiating gives v=(-gamma*e_k*integral(b)/rho-z0*integral(a*b)) /
    integral(b^2). Projecting onto [-A,A] gives its unique feasible optimum.
    This optimizes the provider's action, never the fixed incentive parameter.
    """
    if not all(isfinite(x) for x in (sampled_frequency_error_hz, initial_response_mw, duration_s)):
        raise ValueError("FI decision inputs must be finite")
    if duration_s <= 0 or abs(initial_response_mw) > provider.reserve_mw + 1e-8:
        raise ValueError("FI decision requires positive duration and feasible initial response")
    _, b, _, ab, bb = first_order_response_integrals(provider.time_constant_s, duration_s)
    if bb <= 0:
        raise ValueError("FI interval is too small relative to the response time")
    price = -provider.gamma_eur_per_hz_mw_s * sampled_frequency_error_hz
    unconstrained = (price*b/provider.rho_eur_per_mw2_s - initial_response_mw*ab) / bb
    target = float(max(-provider.reserve_mw, min(provider.reserve_mw, unconstrained)))
    payment, cost = predict_fi_financials(
        provider, sampled_frequency_error_hz=sampled_frequency_error_hz,
        initial_response_mw=initial_response_mw, target_mw=target, duration_s=duration_s,
    )
    benchmark = price * target * duration_s
    if not all(isfinite(x) for x in (unconstrained, payment, cost, benchmark)):
        raise ValueError("FI decision produced a non-finite value")
    return FITargetDecision(target, benchmark, payment, cost)
