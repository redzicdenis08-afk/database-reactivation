# Priority Lead Ranking Logic

Prioritize database reactivation leads using elapsed time since last touch and past purchase history:
- Score = (days_since_last_contact / 30) + (10 if past_buyer else 0)
