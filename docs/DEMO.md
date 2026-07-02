# Demo

This is the fastest way to show why Database Reactivation is worth starring. Use fictional example data only.

## Run it

```bash
pip install -e .
python -m dbreactivation check --leads examples/leads.json --config examples/config.json --suppression examples/suppression.txt
python -m dbreactivation rank --leads examples/leads.json
python -m dbreactivation schedule --leads examples/leads.json --config examples/config.json --suppression examples/suppression.txt
```

## What to screenshot

Readiness gate, ranked dormant leads, skipped suppressed leads, and timezone-safe call schedule.

A good launch screenshot should show the command and the useful output in one image. Avoid giant terminal dumps.

## 30-second narration

1. Say the pain this repo solves.
2. Run the command.
3. Point at the output that proves it works.
4. Mention that the examples are fictional and the engine is inspectable.

## Good caption

This is the whole point of Database Reactivation: small input in, useful decision output, no black-box dashboard needed.
