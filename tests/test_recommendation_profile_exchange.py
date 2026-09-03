from __future__ import annotations

import json

import pytest

from gfl2tool.repository import Repository
from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService
from gfl2tool.services.recommendation_profiles import (
    PROFILE_FORMAT,
    PROFILE_VERSION,
    export_recommendation_profiles,
    import_recommendation_profiles,
)


def test_remolding_recommendation_target_profile_file_round_trips_all_builtin_characters(tmp_path):
    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"
    exchange = tmp_path / "targets.json"

    with Repository(source_path) as repo:
        service = RemoldingRecommendationService(repo)
        custom = service.save_target_profile(
            "nemesis",
            {"sentinel_1": {"level": 4, "weight": 1777, "priority": 2}},
        )
        expected_count = len(service.base_characters)
        assert export_recommendation_profiles(repo, exchange) == expected_count

    payload = json.loads(exchange.read_text(encoding="utf-8"))
    assert payload["format"] == PROFILE_FORMAT
    assert payload["version"] == PROFILE_VERSION
    assert len(payload["characters"]) == expected_count
    assert next(row for row in payload["characters"] if row["key"] == "nemesis")["targets"] == custom

    with Repository(target_path) as repo:
        result = import_recommendation_profiles(repo, exchange)
        assert result == {"imported": expected_count, "skipped": 0}
        imported = RemoldingRecommendationService(repo).get_target_profile("nemesis", with_default=False)
        assert imported == custom


def test_remolding_recommendation_target_profile_import_is_atomic_on_invalid_current_value(tmp_path):
    path = tmp_path / "data.db"
    exchange = tmp_path / "targets.json"
    with Repository(path) as repo:
        service = RemoldingRecommendationService(repo)
        original = service.save_target_profile(
            "nemesis",
            {"sentinel_1": {"level": 3, "weight": 500, "priority": 1}},
        )
        exchange.write_text(
            json.dumps(
                {
                    "format": PROFILE_FORMAT,
                    "version": PROFILE_VERSION,
                    "characters": [
                        {
                            "key": "nemesis",
                            "name": "네메시스",
                            "targets": {"sentinel_1": {"level": 5, "weight": 900, "priority": 1}},
                        },
                        {
                            "key": "colphne",
                            "name": "콜펜",
                            "targets": {"not_a_current_option": {"level": 1, "weight": 1, "priority": 1}},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            import_recommendation_profiles(repo, exchange)
        assert RemoldingRecommendationService(repo).get_target_profile("nemesis", with_default=False) == original


def test_remolding_recommendation_target_profile_import_skips_unknown_character_keys(tmp_path):
    path = tmp_path / "data.db"
    exchange = tmp_path / "targets.json"
    with Repository(path) as repo:
        defaults = RemoldingRecommendationService(repo).get_target_profile("nemesis")
        exchange.write_text(
            json.dumps(
                {
                    "format": PROFILE_FORMAT,
                    "version": PROFILE_VERSION,
                    "characters": [
                        {"key": "nemesis", "name": "네메시스", "targets": defaults},
                        {"key": "future_character", "name": "미래 캐릭터", "targets": {}},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = import_recommendation_profiles(repo, exchange)
        assert result == {"imported": 1, "skipped": 1}
