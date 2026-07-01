# Contributing to dbreactivation

Thanks for considering a contribution. This project aims to stay small, deterministic, and dependency-free at its core.

## Development setup

```bash
git clone https://github.com/redzicdenis08-afk/database-reactivation
cd database-reactivation
pip install -e ".[dev]"
```

## Running tests

```bash
python -m pytest tests/ -q
```

## Guidelines

- Keep the core dependency-free (standard library only). Optional integrations go behind extras.
- Determinism is a feature: same leads + config + clock in, same output out. Anything time-dependent must accept an explicit `now`.
- Compliance rules (suppression, calling windows, attempt caps) are load-bearing. Never weaken a default; add a test for any edge case you touch.
- Every ranking or scheduling change must keep the factor breakdown / skip reasons explainable.
- Run `ruff check .` before opening a PR.
- One focused change per PR. Describe the before/after behavior in the description.

## Adding a ranking factor

1. Add a `<name>_factor` function in `dbreactivation/ranking.py` returning a `Factor` with a human-readable `detail`.
2. Give it a weight in `WEIGHTS` (weights must sum to 1.0) and wire it into `score_lead`.
3. Add tests covering both ends of the factor's range.

## Adding a lifecycle state

1. Extend `LeadState` in `dbreactivation/models.py`.
2. Add its legal transitions to `TRANSITIONS` in `dbreactivation/lifecycle.py` — terminal states get `frozenset()`.
3. Update `CALLABLE_STATES` / `TERMINAL_STATES` if the scheduler may (or must never) dial from it.
4. Add transition tests, including the illegal paths.
