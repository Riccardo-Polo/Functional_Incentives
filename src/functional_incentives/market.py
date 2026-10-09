"""Divisible FCR procurement, explicit fixed awards and block settlement."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite
from typing import Mapping, Sequence

from .providers import QuadraticCommitmentCost


@dataclass(frozen=True)
class FCRCapacityOffer:
    provider_id: str
    price_eur_per_mw_block: float
    quantity_mw: float

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id cannot be empty")
        for name in ("price_eur_per_mw_block", "quantity_mw"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class FCRCapacityAward:
    provider_id: str
    quantity_mw: float
    capacity_payment_eur: float


@dataclass(frozen=True)
class FCRMarketResult:
    """Awards retain input offer order; matching elsewhere must use identity.

    Insufficient supply gives feasible=False, zero awards, and no price.
    shortage_mw is the difference between requirement and offered capacity.
    """

    offers: tuple[FCRCapacityOffer, ...]
    awards: tuple[FCRCapacityAward, ...]
    requirement_mw: float
    feasible: bool
    clearing_price_eur_per_mw_block: float | None
    shortage_mw: float

    @property
    def total_payment_eur(self) -> float:
        return fsum(award.capacity_payment_eur for award in self.awards)


def clear_fcr_market(
    offers: Sequence[FCRCapacityOffer], requirement_mw: float
) -> FCRMarketResult:
    """Minimize sum(c_i*q_i), then pay the highest accepted bid uniformly.

    Capacity is divisible. Equal bids are ordered by provider ID. Buy exactly
    the requirement, including with zero-price bids. A zero requirement has
    zero awards and price zero. Prices are EUR/MW for the whole block.
    """
    if not isfinite(requirement_mw) or requirement_mw < 0.0:
        raise ValueError("requirement_mw must be finite and nonnegative")
    offers = tuple(offers)
    ids = [offer.provider_id for offer in offers]
    if len(ids) != len(set(ids)):
        raise ValueError("provider_id values must be unique")
    supply = fsum(offer.quantity_mw for offer in offers)
    if not isfinite(supply):
        raise ValueError("total offered capacity must be finite")
    if supply < requirement_mw:
        return FCRMarketResult(
            offers=offers,
            awards=tuple(FCRCapacityAward(i, 0.0, 0.0) for i in ids),
            requirement_mw=requirement_mw,
            feasible=False,
            clearing_price_eur_per_mw_block=None,
            shortage_mw=requirement_mw - supply,
        )

    quantities = dict.fromkeys(ids, 0.0)
    remaining = requirement_mw
    price = 0.0
    for offer in sorted(
        offers, key=lambda item: (item.price_eur_per_mw_block, item.provider_id)
    ):
        if remaining <= 0.0:
            break
        quantity = min(remaining, offer.quantity_mw)
        if quantity > 0.0:
            quantities[offer.provider_id] = quantity
            # Recompute from a compensated sum: repeated subtraction can leave
            # a tiny residual and spuriously accept a more expensive offer.
            remaining = max(0.0, requirement_mw - fsum(quantities.values()))
            price = offer.price_eur_per_mw_block
    awards = tuple(
        FCRCapacityAward(i, quantities[i], price * quantities[i]) for i in ids
    )
    if any(not isfinite(award.capacity_payment_eur) for award in awards):
        raise ValueError("capacity payment must be finite")
    return FCRMarketResult(offers, awards, requirement_mw, True, price, 0.0)


def fixed_capacity_allocation(
    offers: Sequence[FCRCapacityOffer], requirement_mw: float,
    quantities_mw: Mapping[str, float], price_eur_per_mw_block: float,
) -> FCRMarketResult:
    """Use explicit awards while enforcing the same identity and capacity bounds."""
    offers = tuple(offers)
    ids = [o.provider_id for o in offers]
    if len(ids) != len(set(ids)) or set(ids) != set(quantities_mw):
        raise ValueError("fixed awards require exactly the offered provider identities")
    if not isfinite(price_eur_per_mw_block) or price_eur_per_mw_block < 0:
        raise ValueError("fixed price must be finite and nonnegative")
    if not isfinite(requirement_mw) or requirement_mw < 0:
        raise ValueError("requirement must be finite and nonnegative")
    for offer in offers:
        q = quantities_mw[offer.provider_id]
        if not isfinite(q) or not 0 <= q <= offer.quantity_mw:
            raise ValueError("fixed awards must lie between zero and offered capacity")
    total = fsum(quantities_mw.values())
    if abs(total - requirement_mw) > 1e-9:
        raise ValueError("fixed awards must sum to the reserve requirement")
    if requirement_mw == 0 and price_eur_per_mw_block != 0:
        raise ValueError("zero procurement requires zero clearing price")
    awards = tuple(FCRCapacityAward(i, quantities_mw[i],
                                   price_eur_per_mw_block * quantities_mw[i]) for i in ids)
    if any(not isfinite(a.capacity_payment_eur) for a in awards):
        raise ValueError("fixed payments must be finite")
    return FCRMarketResult(offers, awards, requirement_mw, True, price_eur_per_mw_block, 0.0)


@dataclass(frozen=True)
class ReserveSettlement:
    provider_id: str
    awarded_mw: float
    available_mw: float
    effective_reserve_mw: float
    capacity_shortfall_mw: float
    capacity_payment_eur: float
    commitment_cost_eur: float
    penalty_eur: float
    profit_eur: float


def settle_reserve_block(
    award: FCRCapacityAward,
    *,
    available_mw: float,
    cost: QuadraticCommitmentCost,
    penalty_eur_per_missing_mw_block: float,
) -> ReserveSettlement:
    """Pi = lambda*q_star - C(q_star) - kappa*(q_star-A)+.

    Costs apply to awarded capacity, rejected offers have zero commitment
    cost, and no extra energy payment or payment clawback is assumed.
    This is the experiment's accounting convention, not a market regulation.
    """
    for value in (award.quantity_mw, award.capacity_payment_eur, available_mw,
                  penalty_eur_per_missing_mw_block):
        if not isfinite(value) or value < 0:
            raise ValueError("settlement inputs must be finite and nonnegative")
    shortfall = max(0.0, award.quantity_mw - available_mw)
    penalty = penalty_eur_per_missing_mw_block * shortfall
    commitment_cost = cost.total_eur(award.quantity_mw)
    profit = award.capacity_payment_eur - commitment_cost - penalty
    if not isfinite(profit):
        raise ValueError("settlement profit must be finite")
    return ReserveSettlement(
        award.provider_id, award.quantity_mw, available_mw,
        min(award.quantity_mw, available_mw), shortfall,
        award.capacity_payment_eur, commitment_cost, penalty, profit,
    )
