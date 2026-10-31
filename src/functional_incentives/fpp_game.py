"""Matched scenarios and best responses in a two-provider FPP gain game."""

from itertools import combinations, product

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from .simulation import power_deficit_events


GAP_TOLERANCE_MW_H = 1e-10


def validate_game(game, horizon):
    if set(game) != {"players", "gains", "paths", "design_samples"}:
        raise ValueError("gain game requires players, gains, paths and design_samples")
    players = game["players"]
    if len(players) != 2 or len(set(players)) != 2 or not all(isinstance(p, str) and p for p in players):
        raise ValueError("declare two distinct FPP provider identities")
    gains = np.asarray(game["gains"], dtype=float)
    if (gains.ndim != 1 or not gains.size or not np.all(np.isfinite(gains))
            or gains[0] < 0 or np.any(np.diff(gains) <= 0)):
        raise ValueError("FPP gain catalogue must be finite, nonnegative and strictly increasing")
    if type(game["design_samples"]) is not int or game["design_samples"] < 2:
        raise ValueError("use at least two design samples")
    paths = game["paths"]
    if len(paths) < 2 or len({path["name"] for path in paths}) != len(paths):
        raise ValueError("declare at least two distinctly named disturbance paths")
    for path in paths:
        if set(path) - {"name", "power_deficit_mw", "start_time_s", "changes"}:
            raise ValueError("unsupported disturbance path field")
        level, start = path["power_deficit_mw"], path["start_time_s"]
        if not np.isfinite(level) or level < 0 or not 0 <= start < horizon:
            raise ValueError("invalid initial disturbance level/time")
        power_deficit_events(level, start, horizon, path.get("changes", ()))


def draw_gain_bank(bank, size, stream, providers, paths):
    """Draw the complete case once, including an availability for every unit."""
    rng = np.random.default_rng(stream)
    return [dict(bank=bank, sample=i, path=paths[rng.integers(len(paths))]["name"],
                 **{p.provider_id: p.availability.sample_mw(rng) for p in providers})
            for i in range(size)]


def path_options(path):
    return dict(power_deficit_mw=path["power_deficit_mw"], start_time_s=path["start_time_s"],
                power_deficit_changes=tuple(map(tuple, path.get("changes", ()))))


def evaluate_gain_bank(bank, gains, score):
    """Evaluate both private scores on the very same joint case for every profile."""
    values = np.empty((len(bank), len(gains), len(gains), 2))
    records = []
    for sample, row in enumerate(bank):
        for i, j in product(range(len(gains)), repeat=2):
            values[sample, i, j] = score(row, (gains[i], gains[j]))
            first, second = values[sample, i, j]
            records.append(dict(bank=row["bank"], sample=row["sample"], path=row["path"],
                                theta_1=gains[i], theta_2=gains[j],
                                score_1_mw_h=first, score_2_mw_h=second))
    if not np.all(np.isfinite(values)):
        raise ValueError("non-finite FPP performance in gain table")
    return values, pd.DataFrame(records)


def deviation_gains(payoffs, x, y):
    """Gain from each pure unilateral deviation against the frozen opponent."""
    rows = payoffs[..., 0] @ y
    columns = x @ payoffs[..., 1]
    return rows - (rows @ x)[..., None], columns - (columns @ y)[..., None]


def _opponent_mix(matrix, best, support):
    """Opponent probabilities that make every included own strategy a best response."""
    restricted = matrix[:, support]
    inequalities = np.vstack([restricted - restricted[i] for i in best])
    scale = np.max(abs(inequalities), initial=0.)
    if scale:
        inequalities = inequalities / scale
    size = len(support)
    result = linprog(np.zeros(size), A_ub=inequalities, b_ub=np.zeros(len(inequalities)),
                     A_eq=np.ones((1, size)), b_eq=[1.], bounds=(0., 1.), method="highs",
                     options={"primal_feasibility_tolerance": 1e-10, "dual_feasibility_tolerance": 1e-10})
    if result.status == 2:
        return None
    if not result.success:
        raise RuntimeError(f"FPP mixed-equilibrium feasibility check failed: {result.message}")
    probabilities = np.zeros(matrix.shape[1])
    probabilities[list(support)] = np.maximum(result.x, 0.)
    return probabilities / probabilities.sum()


def gain_equilibrium(mean):
    """Prefer the lexicographically first pure equilibrium, else enumerate supports."""
    mean = np.asarray(mean, dtype=float)
    if mean.ndim != 3 or mean.shape[2] != 2 or min(mean.shape) == 0 or not np.all(np.isfinite(mean)):
        raise ValueError("expected a finite pair of nonempty payoff matrices")
    first, second = mean[..., 0], mean[..., 1]
    pure = np.argwhere((first.max(axis=0) - first <= GAP_TOLERANCE_MW_H)
                       & (second.max(axis=1)[:, None] - second <= GAP_TOLERANCE_MW_H))
    if len(pure):
        i, j = pure[0]
        return np.eye(len(first))[i], np.eye(len(second.T))[j], pure.tolist()
    row_supports = [s for k in range(1, len(first) + 1) for s in combinations(range(len(first)), k)]
    col_supports = [s for k in range(1, len(second.T) + 1) for s in combinations(range(len(second.T)), k)]
    for rows, cols in product(row_supports, col_supports):
        y = _opponent_mix(first, rows, cols)
        if y is None:
            continue
        x = _opponent_mix(second.T, cols, rows)
        if x is not None and all(g.max() <= GAP_TOLERANCE_MW_H for g in deviation_gains(mean, x, y)):
            return x, y, []
    raise RuntimeError("could not numerically verify an equilibrium of the design payoff matrices")
