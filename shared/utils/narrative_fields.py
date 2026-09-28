"""Read overview / outcomes text from event_summaries.narrative_summary.

The narrative_summary JSONB key names differ by writer and period type:

    daily/weekly batch, generate_daily_summaries   overview / outcomes
    monthly batch                                  monthly_overview / key_outcomes
    yearly batch                                   yearly_overview / annual_outcomes
    generate_event_summaries, summary_generator    overview / outcome

Readers must go through these helpers instead of chaining dict.get()
defaults: a chained default only applies when a key is absent, not when it
holds an empty string, and each hand-written chain drifted to cover a
different subset of the names above.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

OVERVIEW_KEYS = ("overview", "monthly_overview", "yearly_overview")
OUTCOMES_KEYS = ("outcomes", "key_outcomes", "annual_outcomes", "outcome")


def _first_text(narrative: Optional[Mapping[str, Any]], keys: tuple[str, ...]) -> str:
    if not isinstance(narrative, Mapping):
        return ""
    for key in keys:
        value = narrative.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def narrative_overview(narrative: Optional[Mapping[str, Any]]) -> str:
    """Overview text from any narrative_summary shape, or '' if none."""
    return _first_text(narrative, OVERVIEW_KEYS)


def narrative_outcomes(narrative: Optional[Mapping[str, Any]]) -> str:
    """Outcomes text from any narrative_summary shape, or '' if none."""
    return _first_text(narrative, OUTCOMES_KEYS)
