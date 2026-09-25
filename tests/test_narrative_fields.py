"""narrative_summary overview/outcomes readers across every writer's key names."""
import pytest

from shared.utils.narrative_fields import narrative_outcomes, narrative_overview


@pytest.mark.parametrize("narrative", [
    {"overview": "O", "outcomes": "R"},                 # daily / weekly
    {"monthly_overview": "O", "key_outcomes": "R"},     # monthly
    {"yearly_overview": "O", "annual_outcomes": "R"},   # yearly
    {"overview": "O", "outcome": "R"},                  # legacy singular
])
def test_reads_every_shape(narrative):
    assert narrative_overview(narrative) == "O"
    assert narrative_outcomes(narrative) == "R"


def test_empty_string_falls_through_to_next_key():
    # dict.get(a, dict.get(b)) returned "" here; the helper keeps looking.
    assert narrative_outcomes({"key_outcomes": "", "outcome": "R"}) == "R"
    assert narrative_overview({"monthly_overview": "  ", "overview": "O"}) == "O"


@pytest.mark.parametrize("narrative", [None, {}, {"outcomes": None}, {"outcomes": ["x"]}])
def test_missing_or_non_text_returns_empty(narrative):
    assert narrative_outcomes(narrative) == ""
    assert narrative_overview(narrative) == ""
