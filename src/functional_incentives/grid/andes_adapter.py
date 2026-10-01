"""Read a solved ANDES case as an operating point for simpler models.

The adapter deliberately exports ordinary engineering quantities (MW, MVA,
Hz and seconds).  The linear dynamics module therefore does not need to know
anything about ANDES objects or ANDES' per-unit conversions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def solve_pjm5_operating_point(*, no_output: bool = True) -> AndesOperatingPoint:
    """Solve ANDES' bundled PJM 5-bus case and return a compact snapshot."""

    case_path = Path(andes.get_case("5bus/pjm5bus.xlsx"))
    system = andes.load(str(case_path), no_output=no_output)

    power_flow_ok = bool(system.PFlow.run())
    if not power_flow_ok or not system.PFlow.converged:
        raise RuntimeError("ANDES power flow did not converge for the PJM 5-bus case.")

    return extract_operating_point(system, case_path=case_path)


def extract_operating_point(system: Any, *, case_path: str | Path) -> AndesOperatingPoint:
    """Extract the quantities used by the first aggregate frequency model.

    This first adapter supports the ``GENCLS`` synchronous machines used by
    ANDES' ``pjm5bus.xlsx`` case.  In that model, the spreadsheet parameter
    ``M`` is ``2H`` on the machine rating.  ANDES later converts ``M.v`` to the
    system base, so this function intentionally reads the original input
    values from ``M.vin`` before calculating ``H = M/2``.
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
    )
