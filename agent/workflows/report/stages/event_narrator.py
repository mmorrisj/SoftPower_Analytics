"""Stage 7: Event Narrator.

For each top-priority event from event_prioritizer, fetch context and ask
an LLM to draft a re-framed overview + outcomes for THIS report's scope.

Context strategy (cheapest-and-best-grounded first):
  1. Pull the highest-period event_summary tied to canonical_event_id —
     monthly/yearly summaries already have grounded LLM narratives the
     publication workflow produced. Reuse that as primary context.
  2. Supplement with the most recent source documents (title, source,
     date, distilled snippet) from daily_event_mentions.doc_ids so the
     narrator can cite specific doc_ids.
  3. If no event_summary exists, fall back to source docs alone.

The narrator's job is RE-FRAMING for the analyst's scope, not generating
from scratch. Grounding is enforced via an explicit allow-list of doc_ids
in the context and a post-LLM hallucination check (cited_doc_ids that
weren't in the context are stripped).

Per-event LLM call: ~5k input tokens, ~500 visible output tokens (plus
hidden reasoning tokens on reasoning models; see NARRATOR_MAX_TOKENS). Sequential
for v1; parallelizing across events is straightforward in a follow-up.

Output:
    narratives: [
        {event_id, event_name, overview, outcomes,
         cited_doc_ids, hallucinated_doc_ids, used_existing_summary,
         context_doc_count}
    ]
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from sqlalchemy import text

from agent.workflows.base import Stage, StageResult, WorkflowContext

logger = logging.getLogger(__name__)

# Per-event context budgets.
MAX_SOURCE_DOCS_PER_EVENT = 6
SNIPPET_CHAR_LIMIT = 600

# Output budget per narration. Reasoning models (gpt-5 family, o-series)
# spend max_completion_tokens on hidden reasoning BEFORE any visible text,
# so a budget that is ample for ~500 tokens of JSON can come back empty
# (reasoning ate it all) or cut off mid-JSON. On finish_reason == "length"
# the call is retried once with RETRY_MULTIPLIER x the budget.
NARRATOR_MAX_TOKENS = int(os.getenv("AGENT_NARRATOR_MAX_TOKENS", "2500"))
NARRATOR_RETRY_MULTIPLIER = 2
_RAW_PREVIEW_CHARS = 200

NARRATOR_SYSTEM_PROMPT = """\
You are an event narrator drafting one section of a soft-power analyst report.

You will receive context about a single event: an existing narrative summary
(if available) and a set of source documents. Your job is to produce a
focused overview + outcomes for THIS report's scope.

Rules:
  - Stay grounded. Use only facts present in the supplied context.
  - Cite source documents inline using bracketed doc_ids: [doc_id]
  - Cite doc_ids using their FULL UUID exactly as shown in the sources
    list. Never abbreviate, shorten, truncate, or split doc_ids.
    Wrong: [0e7f341f]. Right: [0e7f341f-1234-5678-90ab-cdef12345678]
  - Only cite doc_ids that appear in the provided sources list. Never
    invent a doc_id.
  - Overview: 3-5 sentences. What happened, who was involved, when, where.
  - Outcomes: 2-4 sentences. What changed, what's still in motion, what
    secondary effects emerged.
  - Be specific. Name actors, give dates, cite amounts.
  - Skip caveats unless they're substantive.

Output JSON only — no prose, no code fences, no markdown:
{
  "overview": "<3-5 sentences with inline [doc_id] citations>",
  "outcomes": "<2-4 sentences with inline [doc_id] citations>",
  "cited_doc_ids": ["<every doc_id you cited, deduplicated>"]
}
"""


class EventNarratorStage(Stage):
    name = "event_narrator"
    description = "Draft overview + outcomes narrative for each top-priority event."
    required = True
    depends_on = ["event_prioritizer"]

    def run(self, ctx: WorkflowContext) -> StageResult:
        intent = ctx.require("query_interpreter").data
        prioritized = ctx.require("event_prioritizer").data
        events = prioritized.get("events") or []

        if not events:
            return StageResult(
                ok=True,
                data={"narratives": []},
                confidence=0.0,
                summary="event_narrator: no events to narrate",
                notes=["upstream produced 0 events; skipping LLM calls"],
            )

        try:
            from shared.database.database import get_session
            from agent.llm.provider import get_provider, LLMMessage
        except Exception as e:  # pragma: no cover
            return StageResult(ok=False, error=f"runtime layer unavailable: {e}")

        provider = get_provider()
        narratives: list[dict[str, Any]] = []
        successes = 0
        failures: list[str] = []

        for event in events:
            event_id = event.get("event_id")
            if not event_id:
                failures.append("missing event_id in prioritized event")
                continue

            try:
                with get_session() as session:
                    context = _fetch_event_context(session, event_id)
            except Exception as e:
                logger.exception("event context fetch failed for %s", event_id)
                narratives.append(_failed_narrative(event, f"context fetch failed: {e}"))
                failures.append(event_id)
                continue

            if not context["allowed_doc_ids"] and not context["existing_narrative"]:
                # Nothing to ground on — flag, don't hallucinate.
                narratives.append(_failed_narrative(
                    event,
                    "no source documents or existing narrative available",
                ))
                failures.append(event_id)
                continue

            try:
                result = _narrate_one(
                    provider=provider,
                    LLMMessage=LLMMessage,
                    analyst_query=_compose_analyst_query(intent),
                    event=event,
                    context=context,
                )
            except Exception as e:
                logger.exception("LLM narration failed for %s", event_id)
                narratives.append(_failed_narrative(event, f"LLM call failed: {e}"))
                failures.append(event_id)
                continue

            narratives.append(result)
            if result["ok"]:
                successes += 1
            else:
                failures.append(event_id)

        ok = successes > 0
        total = len(events)
        confidence = successes / total if total > 0 else 0.0
        summary = f"event_narrator: {successes}/{total} narrated"
        if failures:
            summary += f" ({len(failures)} failed)"

        all_citations: list[str] = []
        for n in narratives:
            all_citations.extend(n.get("cited_doc_ids") or [])

        return StageResult(
            ok=ok,
            data={"narratives": narratives},
            confidence=confidence,
            summary=summary,
            citations=list(dict.fromkeys(all_citations)),
            notes=[f"{len(failures)} event(s) failed: {failures}"] if failures else [],
            error=None if ok else "all events failed to narrate",
        )


# ---------------------------------------------------------------------------
# Context fetch
# ---------------------------------------------------------------------------

def _fetch_event_context(session, event_id: str) -> dict[str, Any]:
    """Pull existing narrative (if any) + recent source docs for one event."""
    existing = _fetch_best_event_summary(session, event_id)
    docs = _fetch_event_docs(session, event_id, limit=MAX_SOURCE_DOCS_PER_EVENT)
    allowed = [d["doc_id"] for d in docs if d.get("doc_id")]
    return {
        "existing_narrative": existing,
        "source_docs": docs,
        "allowed_doc_ids": allowed,
    }


# narrative_summary JSONB key names differ by writer and period type:
#   daily/weekly batch + generate_daily_summaries: overview / outcomes
#   monthly batch:  monthly_overview / key_outcomes
#   yearly batch:   yearly_overview  / annual_outcomes
#   legacy generate_event_summaries + publication: overview / outcome
# Checked in order; the first non-empty value wins.
_OVERVIEW_KEYS = ("overview", "monthly_overview", "yearly_overview")
_OUTCOMES_KEYS = ("outcomes", "key_outcomes", "annual_outcomes", "outcome")

# Candidate rows to scan: the top-ranked row can carry a JSONB shape with
# no recognised keys, so fall through to the next-best period.
_SUMMARY_CANDIDATES = 5


def _first_text(narrative: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = narrative.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fetch_best_event_summary(session, canonical_event_id: str) -> dict[str, Any] | None:
    """Return the highest-period event_summary tied to this canonical event.

    Period ordering: yearly > monthly > weekly > daily. Within the same
    period type, prefer the most recent.

    The summary pipelines write their text into the narrative_summary JSONB
    (key names vary, see _OVERVIEW_KEYS / _OUTCOMES_KEYS); the
    overall_summary / outcomes_summary columns are read as a fallback but
    no pipeline stage currently populates them."""
    sql = """
        SELECT
            period_type,
            period_start,
            period_end,
            narrative_summary,
            overall_summary,
            outcomes_summary
        FROM event_summaries
        WHERE canonical_event_id = :event_id
          AND is_deleted = FALSE
          AND (overall_summary IS NOT NULL
               OR outcomes_summary IS NOT NULL
               OR (narrative_summary IS NOT NULL
                   AND narrative_summary <> '{}'::jsonb))
        ORDER BY
            CASE period_type
                WHEN 'YEARLY'  THEN 1
                WHEN 'MONTHLY' THEN 2
                WHEN 'WEEKLY'  THEN 3
                WHEN 'DAILY'   THEN 4
                ELSE 5
            END,
            period_end DESC
        LIMIT :limit
    """
    rows = session.execute(
        text(sql), {"event_id": canonical_event_id, "limit": _SUMMARY_CANDIDATES}
    ).fetchall()
    for row in rows:
        summary = _summary_from_row(row)
        if summary is not None:
            return summary
    return None


def _summary_from_row(row) -> dict[str, Any] | None:
    """Normalise one event_summaries row to {overall_summary, outcomes_summary},
    or None when it carries no usable text."""
    narrative = row.narrative_summary if isinstance(row.narrative_summary, dict) else {}
    overall = _first_text(narrative, _OVERVIEW_KEYS) or (row.overall_summary or "").strip() or None
    outcomes = _first_text(narrative, _OUTCOMES_KEYS) or (row.outcomes_summary or "").strip() or None
    if overall is None and outcomes is None:
        return None
    return {
        "period_type": str(row.period_type),
        "period_start": str(row.period_start) if row.period_start else None,
        "period_end": str(row.period_end) if row.period_end else None,
        "overall_summary": overall,
        "outcomes_summary": outcomes,
    }


def _fetch_event_docs(session, canonical_event_id: str, limit: int) -> list[dict[str, Any]]:
    """Recent source documents for this event, via daily_event_mentions.doc_ids."""
    sql = """
        SELECT DISTINCT ON (d.doc_id)
            d.doc_id,
            d.title,
            d.source_name,
            d.date,
            d.distilled_text
        FROM daily_event_mentions dem
        JOIN documents d ON d.doc_id = ANY(dem.doc_ids)
        WHERE dem.canonical_event_id = :event_id
        ORDER BY d.doc_id, d.date DESC
        LIMIT :limit
    """
    rows = session.execute(text(sql), {"event_id": canonical_event_id, "limit": limit}).fetchall()
    docs: list[dict[str, Any]] = []
    for r in rows:
        snippet = (r.distilled_text or "")[:SNIPPET_CHAR_LIMIT]
        docs.append(
            {
                "doc_id": r.doc_id,
                "title": r.title,
                "source_name": r.source_name,
                "date": str(r.date) if r.date else None,
                "snippet": snippet,
            }
        )
    return docs


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _narrate_one(
    *,
    provider,
    LLMMessage,
    analyst_query: str,
    event: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    user_payload = _compose_user_payload(analyst_query, event, context)

    messages = [
        LLMMessage(role="system", content=NARRATOR_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_payload),
    ]
    budget = NARRATOR_MAX_TOKENS
    response = provider.complete(
        messages=messages, tools=None, temperature=0.2, max_tokens=budget,
    )
    parsed = _parse_narrator_response(response.text)

    if not parsed and response.finish_reason == "length":
        # Truncated before the JSON closed — the budget, not the model's
        # formatting, is the problem. One retry with more room.
        logger.warning(
            "narrator output truncated for %s at max_tokens=%d (%d chars); "
            "retrying with %d",
            event.get("event_id"), budget, len(response.text or ""),
            budget * NARRATOR_RETRY_MULTIPLIER,
        )
        budget *= NARRATOR_RETRY_MULTIPLIER
        response = provider.complete(
            messages=messages, tools=None, temperature=0.2, max_tokens=budget,
        )
        parsed = _parse_narrator_response(response.text)

    overview = (parsed.get("overview") or "").strip()
    outcomes = (parsed.get("outcomes") or "").strip()
    claimed_cites = parsed.get("cited_doc_ids") or []

    failure_reason = None
    if not (overview or outcomes):
        failure_reason = _describe_empty_response(response, budget, parsed)
        logger.warning(
            "narrator produced no narrative for %s: %s",
            event.get("event_id"), failure_reason,
        )

    # Hallucination check + prefix-match salvage: keep doc_ids exact-matching
    # the allow-list, plus any that uniquely-prefix an allow-list ID (LLMs
    # occasionally truncate UUIDs by 1+ chars; the salvage corrects those
    # back to the full ID rather than dropping them as hallucinations).
    allowed = set(context["allowed_doc_ids"])
    raw_cites = list(
        dict.fromkeys(_extract_inline_citations(overview + " " + outcomes) + list(claimed_cites))
    )
    actual_cites, salvaged_map, hallucinated = _salvage_citations(raw_cites, allowed)

    return {
        "event_id": event.get("event_id"),
        "event_name": event.get("event_name"),
        "overview": overview,
        "outcomes": outcomes,
        "cited_doc_ids": list(dict.fromkeys(actual_cites)),
        "salvaged_doc_ids": salvaged_map,        # truncated -> corrected
        "hallucinated_doc_ids": hallucinated,
        "used_existing_summary": context["existing_narrative"] is not None,
        "context_doc_count": len(context["source_docs"]),
        "ok": failure_reason is None,
        "failure_reason": failure_reason,
        "finish_reason": response.finish_reason,
    }


def _describe_empty_response(response, budget: int, parsed: dict[str, Any]) -> str:
    """Say WHY a narration came back empty, so the validator's
    narrative_failure note distinguishes budget exhaustion from bad JSON."""
    raw = response.text or ""
    finish = response.finish_reason
    if not raw.strip():
        if finish == "length":
            return (f"empty output with finish_reason=length at max_tokens={budget} "
                    f"(reasoning tokens likely consumed the budget)")
        return f"empty output (finish_reason={finish})"
    if finish == "length":
        cause = f"truncated at max_tokens={budget}"
    elif not parsed:
        cause = "unparseable JSON"
    else:
        cause = "JSON parsed but overview/outcomes empty"
    preview = raw[:_RAW_PREVIEW_CHARS]
    tail = raw[-_RAW_PREVIEW_CHARS:] if len(raw) > _RAW_PREVIEW_CHARS else ""
    detail = f"{cause} (finish_reason={finish}, {len(raw)} chars): head={preview!r}"
    if tail:
        detail += f" tail={tail!r}"
    return detail


def _compose_analyst_query(intent: dict[str, Any]) -> str:
    parts = [f"Report scope: {intent.get('scope')}."]
    if intent.get("influencer"):
        parts.append(f"Influencer: {intent['influencer']}.")
    if intent.get("recipient"):
        parts.append(f"Recipient: {intent['recipient']}.")
    if intent.get("region"):
        parts.append(f"Region: {intent['region']}.")
    parts.append(f"Date range: {intent.get('start_date')} to {intent.get('end_date')}.")
    return " ".join(parts)


def _compose_user_payload(
    analyst_query: str,
    event: dict[str, Any],
    context: dict[str, Any],
) -> str:
    """Flatten the context into a single user message for the narrator."""
    lines: list[str] = [
        f"ANALYST QUERY: {analyst_query}",
        "",
        "EVENT:",
        f"  event_id: {event.get('event_id')}",
        f"  event_name: {event.get('event_name')}",
        f"  date_span: {event.get('date_span')}",
        f"  initiating_country: {event.get('initiating_country')}",
        f"  categories: {', '.join(event.get('categories') or []) or '-'}",
        f"  materiality_score: {event.get('materiality_score')}",
        f"  coverage (articles): {event.get('coverage_score')}",
        f"  story_phase: {event.get('story_phase')}",
    ]

    existing = context.get("existing_narrative")
    if existing:
        lines += [
            "",
            f"EXISTING NARRATIVE (period: {existing.get('period_type')}, "
            f"{existing.get('period_start')} to {existing.get('period_end')}):",
            f"  overall: {existing.get('overall_summary') or '-'}",
            f"  outcomes: {existing.get('outcomes_summary') or '-'}",
        ]
    else:
        lines += ["", "EXISTING NARRATIVE: none available"]

    docs = context.get("source_docs") or []
    if docs:
        lines += ["", f"SOURCE DOCUMENTS ({len(docs)} most recent, you may cite these doc_ids):"]
        for d in docs:
            lines += [
                f"  - doc_id: {d['doc_id']}",
                f"    title: {d.get('title') or '-'}",
                f"    source: {d.get('source_name') or '-'}",
                f"    date: {d.get('date') or '-'}",
                f"    snippet: {d.get('snippet') or '-'}",
            ]
    else:
        lines += ["", "SOURCE DOCUMENTS: none available"]

    lines += [
        "",
        "Produce overview + outcomes for this event, re-framed for the analyst's scope.",
        "Output JSON only.",
    ]
    return "\n".join(lines)


def _parse_narrator_response(text_response: str) -> dict[str, Any]:
    """Tolerate code fences and surrounding prose; return the first JSON object."""
    if not text_response:
        return {}
    cleaned = text_response.strip()
    if cleaned.startswith("```"):
        # strip a leading fence if the model ignored the no-fences rule
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


_CITATION_RE = re.compile(r"\[([A-Za-z0-9_\-:.]+)\]")


def _extract_inline_citations(text_blob: str) -> list[str]:
    return _CITATION_RE.findall(text_blob or "")


def _salvage_citations(
    cited: list[str], allowed: set[str]
) -> tuple[list[str], dict[str, str], list[str]]:
    """Three-way split of cited doc_ids:

      validated   exact-match against the allow-list
      salvaged    cited as a unique prefix of an allow-list ID; corrected
                  back to the full ID. Handles common LLM truncation patterns
                  (8-char abbreviation, last-char drop) deterministically.
      hallucinated cited but neither exact-match nor uniquely-prefix-matches
                  anything in the allow-list.

    Returns (validated, salvaged_map, hallucinated) where:
      validated     is the deduped list of validated + salvaged-corrected IDs
      salvaged_map  is {original_truncated: full_corrected} for auditability
      hallucinated  is the list of unresolvable cites
    """
    allowed_list = list(allowed)
    validated: list[str] = []
    salvaged_map: dict[str, str] = {}
    hallucinated: list[str] = []
    for c in cited:
        if c in allowed:
            validated.append(c)
            continue
        matches = [a for a in allowed_list if a.startswith(c)]
        if len(matches) == 1:
            salvaged_map[c] = matches[0]
            validated.append(matches[0])
        else:
            # Either zero matches or ambiguous (multiple prefix candidates) —
            # don't guess; flag for the validator.
            hallucinated.append(c)
    return list(dict.fromkeys(validated)), salvaged_map, hallucinated


def _failed_narrative(event: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "event_name": event.get("event_name"),
        "overview": "",
        "outcomes": "",
        "cited_doc_ids": [],
        "hallucinated_doc_ids": [],
        "used_existing_summary": False,
        "context_doc_count": 0,
        "ok": False,
        "failure_reason": reason,
    }


STAGE = EventNarratorStage()
