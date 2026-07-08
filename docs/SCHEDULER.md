# Compliant Scheduler

The scheduler enforces compliance rules before any lead is queued for outreach.

## Rules enforced

| Rule | Source |
|---|---|
| DNC list check | Internal dnc.txt or uploaded file |
| Opt-out field | Lead-level opt_out=true flag |
| Calling window | Configurable start/end hour |
| Max attempts | Per-lead attempt cap |
| Re-engagement gap | Minimum days since last contact |

## Configuration

    from dbreactivation.scheduler import SchedulerConfig

    config = SchedulerConfig(
        call_window_start=9,
        call_window_end=17,
        max_attempts=3,
        re_engagement_gap_days=30,
        dnc_file='examples/dnc.txt',
    )

## DNC file format

One E.164 phone number per line:

    +15550004444
    +15550009999

## Audit trail

    lead-004: SKIPPED -- opt_out=true
    lead-001: SKIPPED -- re_engagement_gap not met (12 < 30 days)
    lead-002: QUEUED -- all compliance checks passed
