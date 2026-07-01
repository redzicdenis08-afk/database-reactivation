# Example campaign

Everything in this folder is synthetic: `+1-555-01XX` numbers, invented names, "Acme HVAC"-style businesses. It is a complete miniature campaign you can run end to end.

| File | What it is |
|---|---|
| `leads.json` | 8 dormant leads across US timezones (one suppressed, one already opted out, one with an unknown timezone) |
| `config.json` | Campaign knobs: 9:00-18:00 lead-local window, 15-minute slots, 4-attempt cap, per-outcome cooldowns |
| `suppression.txt` | The permanent do-not-call list, one number per line |
| `outcomes.json` | 4 call results: a booking, a voicemail, an opt-out, a no-answer |

## Run it

```bash
# 1. Pre-flight: refuse to start unless config + DNC list + leads validate
dbreactivation check --leads examples/leads.json --config examples/config.json \
    --suppression examples/suppression.txt

# 2. Who is worth calling, and why
dbreactivation rank --leads examples/leads.json

# 3. Build a compliant schedule (deterministic with --now)
dbreactivation schedule --leads examples/leads.json --config examples/config.json \
    --suppression examples/suppression.txt --now 2026-07-02T14:00:00Z

# 4. Feed call results back in (updates state, cooldowns, and the DNC file)
dbreactivation ingest --leads examples/leads.json --config examples/config.json \
    --suppression examples/suppression.txt --outcomes examples/outcomes.json \
    --out leads.updated.json

# 5. Where the campaign stands
dbreactivation status --leads leads.updated.json --suppression examples/suppression.txt
```

Note: step 4 appends the fresh opt-out (`L-1003`) to `suppression.txt` — that is the point — so copy the files somewhere first if you want to keep this folder pristine.
