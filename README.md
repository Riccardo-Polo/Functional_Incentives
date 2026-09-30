# Functional Incentives for Frequency Control

Semester Project — ETH Zürich, Automatic Control Laboratory.

## Goal

Study functional incentive mechanisms for frequency control,
with particular focus on uncertain providers operating alongside
the traditional reserve market.

## Project structure

- `src/functional_incentives/grid`: power-system models
- `src/functional_incentives/incentives`: incentive mechanisms
- `src/functional_incentives/simulation`: simulation tools
- `notebooks`: exploratory analysis
- `scripts`: reproducible experiments
- `configs`: experiment configurations
- `results`: generated results and figures
- `tests`: tests
- `docs`: notes and documentation

## Setup

```bash
python -m venv .venv
pip install -e .