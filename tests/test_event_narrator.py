"""event_narrator: truncation retry and failure diagnostics (no DB/LLM)."""
from agent.llm.provider import LLMMessage, LLMResponse
from agent.workflows.report.stages import event_narrator as en

GOOD = '{"overview": "A happened [d1].", "outcomes": "B followed.", "cited_doc_ids": ["d1"]}'
CONTEXT = {"existing_narrative": None, "source_docs": [{"doc_id": "d1"}], "allowed_doc_ids": ["d1"]}
EVENT = {"event_id": "e1", "event_name": "Event"}


class _ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.budgets = []

    def complete(self, messages, tools=None, temperature=0.2, max_tokens=2048):
        self.budgets.append(max_tokens)
        return self.responses.pop(0)


def _narrate(provider):
    return en._narrate_one(provider=provider, LLMMessage=LLMMessage,
                           analyst_query="q", event=EVENT, context=CONTEXT)


def test_success_single_call():
    p = _ScriptedProvider([LLMResponse(text=GOOD)])
    r = _narrate(p)
    assert r["ok"] and r["failure_reason"] is None
    assert r["cited_doc_ids"] == ["d1"]
    assert p.budgets == [en.NARRATOR_MAX_TOKENS]


def test_truncated_output_retries_with_larger_budget():
    p = _ScriptedProvider([
        LLMResponse(text='{"overview": "A happ', finish_reason="length"),
        LLMResponse(text=GOOD),
    ])
    r = _narrate(p)
    assert r["ok"]
    assert p.budgets == [en.NARRATOR_MAX_TOKENS,
                         en.NARRATOR_MAX_TOKENS * en.NARRATOR_RETRY_MULTIPLIER]


def test_empty_output_on_length_reports_reasoning_budget():
    p = _ScriptedProvider([LLMResponse(text="", finish_reason="length")] * 2)
    r = _narrate(p)
    assert not r["ok"]
    assert "reasoning" in r["failure_reason"]


def test_unparseable_without_truncation_does_not_retry():
    p = _ScriptedProvider([LLMResponse(text="Sure! Here is the narrative.")])
    r = _narrate(p)
    assert not r["ok"]
    assert "unparseable JSON" in r["failure_reason"]
    assert "Sure!" in r["failure_reason"]
    assert len(p.budgets) == 1
