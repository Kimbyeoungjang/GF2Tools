import pytest

from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService


class _BrokenConnection:
    def execute(self, _sql, _params=()):
        raise RuntimeError("transient profile read failure")


class _BrokenRepo:
    con = _BrokenConnection()


def test_recommendation_profile_read_errors_are_not_silently_treated_as_empty_profiles():
    with pytest.raises(RuntimeError, match="transient profile read failure"):
        RemoldingRecommendationService(_BrokenRepo())
