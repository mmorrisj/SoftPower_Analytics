# Event Summary Pipeline (DEPRECATED)

**This pipeline is deprecated.** `generate_event_summaries.py` uses the old RawEvent + Document path and is superseded by the canonical event summary pipeline (see the script's own docstring).

Use `services/pipeline/summaries/generate_{daily,weekly,monthly,yearly}_summaries.py` instead — they work from `canonical_events` + `daily_event_mentions` (the current pipeline) and populate the `event_summaries` table.
