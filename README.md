# Database Reactivation

**Win-back engine for a business's dead leads.** Rank dormant customers by reactivation potential, build a call schedule that respects timezones, quiet hours, attempt caps, and do-not-call lists, then feed call outcomes back into the loop until leads become booked work.

[![CI](https://github.com/redzicdenis08-afk/database-reactivation/actions/workflows/ci.yml/badge.svg)](https://github.com/redzicdenis08-afk/database-reactivation/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

> This repository is the open **reference implementation** of the pipeline behind my Database Reactivation service — an AI voice system that re-engages old, cold leads for local businesses and turns them back into booked appointments, on a pay-per-booking model. The production system (voice agents, prompts, dialer fleet, customer data) is private; the campaign engine that decides *who to call, when, and what to do with the result* is what you see here.

## Why this exists

Every local business is sitting on a database of past customers and dead inquiries that nobody follows up with. Reactivating that list is the highest-ROI outreach there is — the business already earned these contacts — but doing it *well* is an engineering problem, not a dialing problem:

- **Who first?** A lead that went cold 60 days ago with two past jobs is gold; one from 2023 is landfill. Burning the list top-to-bottom wastes your best shots.
- **When?** Calling a Los Angeles number at 7 AM local because your server runs on Eastern time is how you lose the whole list (and break the law).
- **What happened?** Opt-outs must become permanent suppressions, voicemails need backoff, and interested leads must never be auto-abandoned by a retry cap.

`dbreactivation` packages those decisions as a small, deterministic, dependency-free Python library and CLI.

## Pipeline architecture

```
                ┌─────────────────────────────────────────────────────────┐
                │                     READINESS GATE                      │
                │  config sane? DNC list present? window legal? leads ok? │
                │            (refuses to start the campaign)              │
                └────────────────────────────┬────────────────────────────┘
                                             │ ready
   dormant leads                             ▼
  ┌────────────┐    ┌──────────────┐   ┌───────────────┐    ┌─────────────┐
  │ leads.json ├───>│   RANKING    ├──>│   SCHEDULER   ├───>│ call sched. │
  └────────────┘    │ recency      │   │ tz windows    │    │ (dialer/    │
                    │ engagement   │   │ quiet hours   │    │  voice AI)  │
        ┌──────────>│ value band   │   │ attempt caps  │    └──────┬──────┘
        │           │ seasonality  │   │ cooldowns     │           │
        │           └──────────────┘   │ suppression   │           │ outcomes
        │                              └───────▲───────┘           ▼
        │                                      │            ┌─────────────┐
  ┌─────┴──────┐   state, attempts, cooldowns  │            │   INTAKE    │
  │ LIFECYCLE  │<──────────────────────────────┼────────────┤ classify:   │
  │ dormant →  │                               │            │ reached /   │
  │ … → booked │        opt-out → permanent    │            │ voicemail / │
  │ (enforced) │   ┌───────────────────────────┴──┐         │ no-answer / │
  └────────────┘   │       SUPPRESSION LIST       │<────────┤ opt-out     │
                   └──────────────────────────────┘         └─────────────┘
```

The loop: **rank → schedule → call → ingest → (re-rank) → …** until every lead is booked, opted out, or exhausted.

## Install

```bash
pip install -e .            # from source; zero runtime dependencies
pip install -e ".[dev]"     # + pytest, ruff
```

## Quickstart

The `examples/` folder is a complete synthetic campaign (fictional `+1-555-01XX` numbers, invented names).

### 1. Pre-flight check — the gate that refuses bad campaigns

```bash
dbreactivation check --leads examples/leads.json --config examples/config.json \
    --suppression examples/suppression.txt
```

```
  [PASS] config_file        ready                  examples/config.json
  [PASS] config_window      ready                  9:00-18:00 lead-local
  [PASS] config_limits      ready                  caps, attempts, cooldowns in range
  [PASS] campaign_timezone  ready                  America/New_York
  [PASS] suppression_list   ready                  1 suppressed number(s)
  [PASS] leads_file         ready                  examples/leads.json
  [PASS] leads              ready                  8 lead(s)
  [PASS] lead_timezones     dual_clock_fallback    1 lead(s) without a timezone will use the conservative dual-clock (ET+PT) rule
----------------------------------------------------------------
READY - campaign may start
```

Forget the suppression list and it refuses (exit code 3) — "we lost the DNC file" must never fail silently. `schedule` runs this gate automatically.

### 2. Rank — who is worth a dial, and why

```bash
dbreactivation rank --leads examples/leads.json
```

```
  #  LEAD       NAME                    SCORE  FACTORS
------------------------------------------------------------------------------
  1  L-1001     Maria Alvarez            94.0  recency=1.00  engagement=0.80  value=1.00  seasonality=1.00
  2  L-1002     James Okafor             68.5  recency=0.90  engagement=0.40  value=0.60  seasonality=1.00
  3  L-1006     Ahmed Hassan             67.0  recency=0.90  engagement=0.60  value=0.30  seasonality=1.00
  4  L-1003     Sandra Lee               66.5  recency=0.70  engagement=0.40  value=1.00  seasonality=0.50
  5  L-1007     Grace Kim                63.5  recency=0.90  engagement=0.40  value=0.60  seasonality=0.50
  6  L-1004     Bill Turner              55.5  recency=0.10  engagement=1.00  value=0.60  seasonality=0.70
  7  L-1008     Tom Nowak                55.0  recency=0.90  engagement=0.20  value=0.30  seasonality=1.00
  8  L-1005     Dana White               48.0  recency=0.20  engagement=0.20  value=1.00  seasonality=1.00
------------------------------------------------------------------------------
8 lead(s) ranked
```

Maria went cold 73 days ago (the dormancy sweet spot), has two prior jobs, a high-value trade, and HVAC is in season in July — every point of that 94.0 is auditable via `--json`, which includes the full factor breakdown with human-readable reasons.

### 3. Schedule — compliant by construction

```bash
dbreactivation schedule --leads examples/leads.json --config examples/config.json \
    --suppression examples/suppression.txt --now 2026-07-02T14:00:00Z
```

```
WHEN (UTC)         LEAD LOCAL                             LEAD       ATT  SCORE
--------------------------------------------------------------------------------
2026-07-02 14:00   2026-07-02 10:00 America/New_York      L-1001       1  94.0
2026-07-02 14:15   2026-07-02 09:15 America/Chicago       L-1002       1  68.5
2026-07-02 15:00   2026-07-02 09:00 America/Denver        L-1005       1  48.0
2026-07-02 16:00   2026-07-02 09:00 America/Phoenix       L-1006       1  67.0
2026-07-02 16:15   2026-07-02 09:15 America/Los_Angeles   L-1003       1  66.5
2026-07-02 16:30   2026-07-02 16:30 UTC (tz unknown)      L-1004       1  55.5
--------------------------------------------------------------------------------
6 call(s) scheduled | 2 skipped
  skipped L-1007: suppressed
  skipped L-1008: state_not_callable:opted_out
```

Read that carefully: at 14:00 UTC it dials the New York lead immediately, but *holds the West Coast leads until 9:00 AM their time*. The lead with no known timezone (L-1004) waits until the slot is legal in **both** Eastern and Pacific — an unmapped number can never be dialed too early or too late anywhere in the continental US. Suppressed and opted-out leads never make it into the schedule at all, with the reason stated.

### 4. Ingest — outcomes feed back into the campaign

```bash
dbreactivation ingest --leads examples/leads.json --config examples/config.json \
    --suppression examples/suppression.txt --outcomes examples/outcomes.json \
    --out leads.updated.json
```

```
LEAD       OUTCOME    NEW STATE    ATT  NOTES
----------------------------------------------------------------
L-1001     reached    booked         1  BOOKED
L-1002     voicemail  voicemail      1  cooldown until 2026-07-03T15:20:00Z
L-1003     reached    opted_out      1  OPT-OUT -> suppressed
L-1005     no_answer  no_answer      1  cooldown until 2026-07-02T20:30:00Z
----------------------------------------------------------------
4 outcome(s) applied | 1 new suppression(s) | 0 error(s)
```

Outcomes come in as JSON — either an explicit `outcome` field or a raw transcript that gets classified (voicemail greetings, opt-out phrases, booking language). A voicemail backs off 24 hours, a no-answer 4, and the opt-out is appended to the suppression file *permanently*. Classification only scans what the **customer** said — scanning agent lines too would match the agent's own compliance script on every call.

### 5. Status — where the campaign stands

```bash
dbreactivation status --leads leads.updated.json --suppression examples/suppression.txt
```

```
CAMPAIGN STATUS - 8 lead(s)
----------------------------------------
  dormant         3
  voicemail       1
  no_answer       1
  booked          1
  opted_out       2
----------------------------------------
  attempts made      6
  booked             1
  opted out          2
  suppression list   2 number(s)
```

Every command also takes `--json` for machine-readable output, and `--now <ISO-8601>` to pin the clock for reproducible runs.

## Library use

```python
from dbreactivation import (
    CampaignConfig, Lead, SuppressionList,
    rank_leads, build_schedule, ingest_outcomes, OutcomeRecord,
)

leads = [Lead.from_dict(d) for d in raw_leads]
config = CampaignConfig(window_start_hour=9, window_end_hour=18, max_attempts=4)
dnc = SuppressionList.from_text(open("suppression.txt").read())

for r in rank_leads(leads)[:5]:
    print(r.lead.lead_id, r.score, [f"{f.name}={f.value:.2f} ({f.detail})" for f in r.factors])

schedule = build_schedule(leads, config, suppression=dnc)
report = ingest_outcomes(leads, [OutcomeRecord.from_dict(o) for o in results], config, dnc)
```

## The lifecycle state machine

```
dormant ──> queued ──> attempted ──> reached ─────> interested ──> BOOKED
               ^            │            │ │             │
               │            ├─> voicemail│ └─> queued    ├─> OPTED_OUT
               │            └─> no_answer│  (callback)   └─> (stays live,
               │                 │  │    ├─> OPTED_OUT        never auto-
               └─────────────────┘  │    └─> EXHAUSTED        exhausted)
                    (retry)         └─> EXHAUSTED
```

Illegal transitions raise `LifecycleError`. `BOOKED`, `OPTED_OUT`, and `EXHAUSTED` are terminal — `OPTED_OUT` in particular is a one-way door with zero exits, and every transition is recorded in the lead's history for auditability.

## Design principles

- **Zero runtime dependencies.** Pure Python standard library. `pip install` pulls in nothing; it runs anywhere Python 3.9+ runs, offline.
- **Deterministic.** Same leads + config + clock in, same schedule out. Every time-dependent function accepts an explicit `now`. Ties break on lead id. You can diff two runs.
- **Compliance-first.** The scheduler cannot emit a call outside the lead's local window, past the attempt cap, into a cooldown, or to a suppressed number — those aren't filters bolted on after, they're the slot-assignment rules. The readiness gate refuses to start a campaign without a DNC list, and the legal 8 AM-9 PM boundary is enforced against the *configured* window before a single call is planned.
- **Conservative under uncertainty.** Unknown timezone? The dual-clock rule requires the slot to be legal in both ET and PT. Missing tz database on the host? Fixed standard-time offsets — the direction that never dials early.
- **Explainable.** Rankings carry per-factor breakdowns with plain-language reasons; skipped leads carry explicit skip reasons; state changes carry an audit history. "Why did it do that?" always has an answer in the output.

## Production notes

The private production system wraps this engine with: VAPI voice agents that hold the actual conversation and booking handoff, a dialer fleet with per-account daily ceilings, Google Sheets / SQLite state, transcript-level call analytics (see [callscope](https://github.com/redzicdenis08-afk/callscope)), and unattended operation on EC2 via systemd timers with per-cycle health checks. Those layers are deliberately out of scope here; this repo is the decision core.

## Roadmap

- [ ] Area-code → timezone inference for NANP numbers
- [ ] Per-campaign ranking weight overrides via config
- [ ] CSV import/export for leads and outcomes
- [ ] Multi-line (concurrent slot) scheduling
- [ ] Optional LLM outcome classification (bring-your-own-key)

## Contributing

Issues and PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Run the tests with `python -m pytest tests/ -q`.

## Demo script

A short demo plan for launch screenshots and GIFs lives in [docs/DEMO.md](docs/DEMO.md).

## Star this repo if

- You build in this niche and want a small reference engine instead of a black-box demo.
- You want synthetic examples that run locally.
- You care about readable implementation details, not just screenshots.

Launch notes and topic suggestions live in [docs/LAUNCH_PACK.md](docs/LAUNCH_PACK.md).

## Repository health

This repo now includes GitHub issue templates, a PR checklist, Dependabot checks for GitHub Actions, and a public boundary checklist in [docs/REPO_HEALTH.md](docs/REPO_HEALTH.md).

## License

[MIT](LICENSE) © Denis Redzic

---

Part of the work of [Denis Redzic](https://denis.denisai.online).
