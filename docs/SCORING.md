# Ad Scoring Model

Adoracle scores ads on engagement efficiency and creative strength.

## Engagement score

`
ctr = clicks / impressions
cpc = spend / clicks
ctr_normalized = min(ctr / 0.05, 1.0) * 100  # 5% CTR = 100
cpc_score = max(0, 100 - (cpc / max_cpc) * 100)

engagement_score = (ctr_normalized * 0.6) + (cpc_score * 0.4)
`

## Hook strength score

Each hook angle detected adds to the hook score. Scores are:

| Hooks detected | Hook score |
|---|---|
| 0 | 0 |
| 1 | 40 |
| 2 | 70 |
| 3+ | 100 |

## Final score

`
final_score = (engagement_score * 0.7) + (hook_score * 0.3)
`

## Output interpretation

| Score | Interpretation |
|---|---|
| 80-100 | High-performer â€” study and model this ad |
| 60-79 | Solid â€” test variations of the hook |
| 40-59 | Mediocre â€” rewrite the headline |
| 0-39 | Poor â€” do not model this creative |
