"""ANDES operating-point data, configurable generation fleet and inertia extraction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Sequence

import andes
import numpy as np


@dataclass(frozen=True)
class AndesOperatingPoint:
    """Small, immutable snapshot of a solved ANDES operating point."""

    case_path: Path
    system_base_mva: float
    nominal_frequency_hz: float
    total_load_mw: float
    total_generation_mw: float
    network_losses_mw: float
    generator_ids: tuple[Any, ...]
    generator_buses: tuple[Any, ...]
    generator_active_power_mw: tuple[float, ...]
    synchronous_ratings_mva: tuple[float, ...]
    synchronous_inertia_constants_s: tuple[float, ...]
    generator_ratings_mva: tuple[float, ...]  # machine/converter bases in generator order

    @property
    def total_synchronous_rating_mva(self) -> float:
        return float(sum(self.synchronous_ratings_mva))

    @property
    def inertia_energy_mw_s(self) -> float:
        """Return sum(H_i S_i), in MW s (numerically also MVA s)."""

        return float(
            sum(
                inertia * rating
                for inertia, rating in zip(
                    self.synchronous_inertia_constants_s,
                    self.synchronous_ratings_mva,
                    strict=True,
                )
            )
        )

    @property
    def equivalent_inertia_s(self) -> float:
        """Rating-weighted equivalent H from PSDCO equations 8.8--8.10."""

        rating = self.total_synchronous_rating_mva
        if rating <= 0.0:
            raise ValueError("The operating point contains no synchronous rating.")
        return self.inertia_energy_mw_s / rating


@dataclass(frozen=True)
class FleetGenerator:
    generator_id: str
    bus_id: int
    technology: str
    rating_mva: float
    power_setpoint_mw: float
    inertia_s: float
    minimum_power_mw: float
    maximum_power_mw: float
    is_slack: bool = False

    def __post_init__(self) -> None:
        if not self.generator_id.strip():
            raise ValueError("generator_id cannot be empty")
        if self.technology not in ("conventional", "renewable"):
            raise ValueError("fleet technology must be conventional or renewable")
        if not all(isfinite(v) for v in (
            self.rating_mva, self.power_setpoint_mw, self.inertia_s,
            self.minimum_power_mw, self.maximum_power_mw,
        )):
            raise ValueError("fleet parameters must be finite")
        if self.rating_mva <= 0 or self.minimum_power_mw > self.maximum_power_mw:
            raise ValueError("invalid rating or active-power limits")
        if not self.is_slack and not self.minimum_power_mw <= self.power_setpoint_mw <= self.maximum_power_mw:
            raise ValueError("non-slack dispatch must be within active-power limits")
        if self.technology == "conventional" and self.inertia_s <= 0:
            raise ValueError("conventional generators require positive inertia")
        if self.technology == "renewable" and (self.inertia_s != 0 or self.is_slack):
            raise ValueError("this renewable model has zero inertia and cannot be slack")


def solve_operating_point(fleet: Sequence[FleetGenerator] | None = None) -> AndesOperatingPoint:
    """Solve PJM power flow with an explicit fleet, or the bundled fleet.

    Renewable converters contribute a droop power base but no synchronous
    inertia. The source case file is never modified. Voltage targets remain
    1 pu with unrestricted teaching-case reactive limits.
    """
    case_path = Path(andes.get_case("5bus/pjm5bus.xlsx"))
    system = andes.load(str(case_path), setup=False, no_output=True)
    nonsynchronous = {}
    if fleet is not None:
        ids = [g.generator_id for g in fleet]
        if len(ids) != len(set(ids)) or sum(g.is_slack for g in fleet) != 1:
            raise ValueError("fleet requires unique identities and exactly one slack")
        if any(g.bus_id not in system.Bus.idx.v for g in fleet):
            raise ValueError("fleet bus does not exist in the PJM network")
        if (set(system.PV.idx.v) | set(system.Slack.idx.v)).intersection(ids):
            raise ValueError("use identities distinct from the bundled generators")
        for model in (system.PV, system.Slack, system.GENCLS):
            for row in range(model.n):
                model.u.v[row] = 0
        base_mva = float(system.config.mva)
        for g in fleet:
            system.add(
                "Slack" if g.is_slack else "PV", idx=g.generator_id, bus=g.bus_id,
                Sn=g.rating_mva, p0=g.power_setpoint_mw/base_mva,
                pmin=g.minimum_power_mw/base_mva, pmax=g.maximum_power_mw/base_mva,
                v0=1.0, qmin=-999.0, qmax=999.0,
            )
            if g.technology == "conventional":
                system.add(
                    "GENCLS", idx=g.generator_id, gen=g.generator_id, bus=g.bus_id,
                    Sn=g.rating_mva, M=2*g.inertia_s, fn=60.0, ra=0.0, xd1=1.7,
                )
            else:
                nonsynchronous[g.generator_id] = g.rating_mva
    system.setup()
    if not system.PFlow.run() or not system.PFlow.converged:
        raise RuntimeError("ANDES power flow did not converge")
    point = extract_operating_point(
        system, case_path=case_path, nonsynchronous_ratings_mva=nonsynchronous)
    if fleet is not None:
        specs = {g.generator_id: g for g in fleet}
        for identity, power in zip(point.generator_ids, point.generator_active_power_mw, strict=True):
            if not specs[identity].minimum_power_mw <= power <= specs[identity].maximum_power_mw:
                raise ValueError(f"Solved power of {identity} is outside its active-power limits")
    return point


def extract_operating_point(
    system: Any, *, case_path: str | Path,
    nonsynchronous_ratings_mva: Mapping[Any, float] | None = None,
) -> AndesOperatingPoint:
    """Extract the quantities used by the first aggregate frequency model.

    This first adapter supports the ``GENCLS`` synchronous machines used by
    ANDES' ``pjm5bus.xlsx`` case.  In that model, the spreadsheet parameter
    ``M`` is ``2H`` on the machine rating.  ANDES later converts ``M.v`` to the
    system base, so this function intentionally reads the original input
    values from ``M.vin`` before calculating ``H = M/2``. Nonsynchronous
    generators require explicit converter bases; they add no synchronous inertia.
    """

    base_mva = float(system.config.mva)

    frequency_values = np.concatenate(
        (
            np.asarray(system.Line.fn.v, dtype=float),
            np.asarray(system.GENCLS.fn.v, dtype=float),
        )
    )
    if frequency_values.size == 0:
        raise ValueError("No nominal-frequency data were found in the ANDES case.")
    if not np.allclose(frequency_values, frequency_values[0]):
        raise ValueError("The ANDES case contains inconsistent nominal frequencies.")
    nominal_frequency_hz = float(frequency_values[0])

    load_pu = np.asarray(system.PQ.p0.v, dtype=float)
    load_status = np.asarray(system.PQ.u.v, dtype=float)
    total_load_mw = float(np.sum(load_status * load_pu) * base_mva)

    generator_ids: list[Any] = []
    generator_buses: list[Any] = []
    generator_active_power_mw: list[float] = []
    for model in (system.PV, system.Slack):
        for idx, bus, active_power_pu, status in zip(
            model.idx.v,
            model.bus.v,
            model.p.v,
            model.u.v,
            strict=True,
        ):
            if status:
                generator_ids.append(idx)
                generator_buses.append(bus)
                generator_active_power_mw.append(float(active_power_pu) * base_mva)

    total_generation_mw = float(sum(generator_active_power_mw))

    machine_status = np.asarray(system.GENCLS.u.v, dtype=bool)
    ratings_mva = np.asarray(system.GENCLS.Sn.v, dtype=float)[machine_status]
    machine_startup_times_s = np.asarray(system.GENCLS.M.vin, dtype=float)[
        machine_status
    ]
    inertia_constants_s = machine_startup_times_s / 2.0

    # GENCLS order need not equal the PV/Slack order above. Match the static
    # generator reference explicitly before using ratings as droop bases.
    rating_by_generator: dict[Any, float] = {}
    for generator_id, rating, online in zip(
        system.GENCLS.gen.v, system.GENCLS.Sn.v, system.GENCLS.u.v, strict=True
    ):
        if online:
            if generator_id in rating_by_generator:
                raise ValueError("Expected one online GENCLS machine per generator")
            rating_by_generator[generator_id] = float(rating)
    for generator_id, rating in (nonsynchronous_ratings_mva or {}).items():
        if generator_id in rating_by_generator:
            raise ValueError("A generator cannot be both synchronous and nonsynchronous")
        if not np.isfinite(rating) or rating <= 0:
            raise ValueError("Converter ratings must be finite and positive")
        rating_by_generator[generator_id] = float(rating)
    if set(rating_by_generator) != set(generator_ids):
        raise ValueError("Online generators and declared machine/converter bases do not match")

    return AndesOperatingPoint(
        case_path=Path(case_path),
        system_base_mva=base_mva,
        nominal_frequency_hz=nominal_frequency_hz,
        total_load_mw=total_load_mw,
        total_generation_mw=total_generation_mw,
        network_losses_mw=total_generation_mw - total_load_mw,
        generator_ids=tuple(generator_ids),
        generator_buses=tuple(generator_buses),
        generator_active_power_mw=tuple(generator_active_power_mw),
        synchronous_ratings_mva=tuple(float(value) for value in ratings_mva),
        synchronous_inertia_constants_s=tuple(
            float(value) for value in inertia_constants_s
        ),
        generator_ratings_mva=tuple(rating_by_generator[i] for i in generator_ids),
    )
