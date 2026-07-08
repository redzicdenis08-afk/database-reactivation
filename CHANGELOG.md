# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Planned
- CSV upload validation with column mapping UI
- Configurable re-engagement gap (days since last contact)
- SQLite backend for persistent state

## [0.2.0] - 2026-07-08

### Added
- `examples/` with sample lead CSV and expected ranking output
- `docs/RANKING.md`: explains the win-back scoring formula
- `docs/SCHEDULER.md`: explains compliant scheduling rules

### Changed
- README: added one-liner install and quick-start

## [0.1.0] - 2026-06-24

### Added
- Win-back ranking engine with recency, frequency, and value scoring
- Compliant call scheduling with DNC list and opt-out enforcement
- Intake module: CSV and JSON lead ingestion
- Lifecycle tracking: pending, contacted, re-engaged, closed
- CLI: `dbreactivation run`, `dbreactivation status`
- Test suite and CI
