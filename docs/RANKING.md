# Win-Back Ranking

The ranking engine scores dormant leads so you call the most valuable ones first.

## Scoring formula

`
score = (recency_score * 0.4) + (frequency_score * 0.3) + (value_score * 0.3)
`

### Recency score

Days since last contact, inverted and normalized to 0-100:

`
recency_score = max(0, 100 - (days_since_contact / max_days) * 100)
`

### Frequency score

Number of prior contacts normalized to 0-100. More prior contacts = higher score
(they engaged before).

### Value score

Estimated deal value normalized to 0-100 relative to the highest-value lead
in the batch.

## Customizing weights

`python
from dbreactivation.ranking import RankingConfig

config = RankingConfig(
    recency_weight=0.5,
    frequency_weight=0.2,
    value_weight=0.3,
)
`

## Output

Returns a sorted list of `RankedLead` objects with `.score`, `.rank`,
and all original lead fields preserved.
