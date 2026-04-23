---
name: software-function-research
goal: >
  Research a target software product and extract a structured record for each
  major function or feature it offers. For every function capture: what it does,
  how many reviews or mentions exist online, how users rate its importance, common
  praise, common complaints, and which competitor products offer a similar function.
  One row per function. Target software: REPLACE_WITH_SOFTWARE_NAME.

model: qwen2.5-3b-instruct-q4_k_m.gguf

fields:
  - software_name: Name of the software product
  - function_name: Name of the specific feature or function (e.g. "reporting dashboard")
  - function_category: Category this function belongs to (e.g. automation, analytics, collaboration, integrations, security)
  - description: One sentence describing what this function does
  - review_count_approx: Approximate number of reviews or mentions found online referencing this function (integer or range e.g. "50-100")
  - importance_rating: How users rate importance of this function — high / medium / low — based on frequency of mentions and sentiment
  - avg_user_sentiment: Overall sentiment users express about this function — positive / mixed / negative
  - top_praise: The most common positive thing users say about this function (one sentence)
  - top_complaint: The most common criticism or pain point users mention (one sentence, or null if none found)
  - competitor_equivalent: Name of a competing software that offers a similar function (or null)
  - source_url: URL where this data was found

stop:
  mode: quantity
  quantity: 50          # 50 rows = roughly 50 distinct functions across the product

status: active

interval: 20

queries:
  - "REPLACE_WITH_SOFTWARE_NAME features review site:g2.com"
  - "REPLACE_WITH_SOFTWARE_NAME features review site:capterra.com"
  - "REPLACE_WITH_SOFTWARE_NAME user review functions site:trustradius.com"
  - "REPLACE_WITH_SOFTWARE_NAME feature breakdown review 2024"
  - "REPLACE_WITH_SOFTWARE_NAME what does it do user review"
  - "REPLACE_WITH_SOFTWARE_NAME reporting feature review"
  - "REPLACE_WITH_SOFTWARE_NAME automation feature review"
  - "REPLACE_WITH_SOFTWARE_NAME integration feature review"
  - "REPLACE_WITH_SOFTWARE_NAME pros cons review"
  - "REPLACE_WITH_SOFTWARE_NAME feature comparison competitor"
  - "REPLACE_WITH_SOFTWARE_NAME missing features user complaints"
  - "REPLACE_WITH_SOFTWARE_NAME best features users love"
---

## Notes

- Replace REPLACE_WITH_SOFTWARE_NAME throughout this file with the actual product name before running.
- Each row must represent one distinct function or feature — do not combine multiple functions into a single row.
- If the same function appears across multiple review sources, merge into one row and increase review_count_approx.
- importance_rating should be inferred from how frequently the function is mentioned in reviews and how strongly users feel about it.
- For competitor_equivalent, only include a competitor name if a specific product is mentioned by reviewers in comparison — do not guess.
- Preferred sources in order: G2, Capterra, TrustRadius, GetApp, Reddit r/software reviews, official product docs.
- Avoid low-confidence sources: anonymous forums, AI-generated review roundups, affiliate comparison sites.
- If a function is mentioned but has very few reviews, still include it with review_count_approx set to the real count and importance_rating set to low.
- Stop condition is 50 rows. If the product has fewer than 50 distinct functions, the worker will stop early once queries are exhausted.
