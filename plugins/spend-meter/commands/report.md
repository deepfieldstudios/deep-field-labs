---
description: Spend across all projects for the last 14 days - per day, per session, top 5 sessions, by model, cache hit ratio
argument-hint: "[--days N]"
---
Run this command with the Bash tool and show the user its Markdown output exactly as printed:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" $ARGUMENTS
```

After the tables, add at most three short bullet observations a cost-conscious user would act on (for example: which session or model dominates spend, whether the cache hit ratio is low, how much went to subagents). Do not restate the tables. Note once that prices come from the plugin's `data/prices.json` and should be checked against current pricing.
