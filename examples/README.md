# Examples

Sample data for testing the database reactivation engine.

## leads.csv

A fictional batch of 5 dormant leads. Note lead-004 has opt_out=true
and should be excluded from all outreach.

## Run ranking

`ash
python -m dbreactivation rank examples/leads.csv --output ranked.csv
`

## Expected top-3 ranking order

1. Carol White (score ~88) - recent + frequent + high value
2. Eve Davis (score ~76) - recent, frequent
3. Alice Smith (score ~61) - moderate on all dimensions
