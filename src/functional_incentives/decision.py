"""Independent offer decisions: R(c,q) = c*q - C(q) - expected shortage penalty."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from scipy.optimize import minimize_scalar

from .availability import GaussianReserve
from .providers import QuadraticCommitmentCost
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
