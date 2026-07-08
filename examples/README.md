# Examples

Sample ad creative data for testing Adoracle scoring.

## sample_ads.json

Three fictional ads across Facebook, Google, and LinkedIn with impression/click/spend data.

## Run

`ash
npx adoracle batch examples/sample_ads.json --output csv
`

## Expected hook angles detected

| id | hooks |
|---|---|
| ad-001 | pain, social_proof |
| ad-002 | authority, transformation |
| ad-003 | scarcity |
