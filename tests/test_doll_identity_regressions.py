from __future__ import annotations

from gfl2tool.models import Doll
from gfl2tool.qtui.data import OwnedDollCatalog
from gfl2tool.repository import Repository
from gfl2tool.services.dolls import DollCharacterResolver, expand_linked_owned_doll_rows


def test_linked_nemesis_forms_are_both_exposed_from_either_owned_id(tmp_path):
    for owned_id, owned_name in ((1008, "네메시스"), (1075, "네메시스·연광")):
        with Repository(tmp_path / f"nemesis-{owned_id}.db") as repo:
            repo.replace_dolls([Doll(owned_id, owned_name, 60, 6)])
            entries = {int(row["doll_id"]): row for row in OwnedDollCatalog(repo).entries()}

            assert {1008, 1075}.issubset(entries)
            assert entries[1008]["name"] == "네메시스"
            assert entries[1075]["name"] == "네메시스·연광"
            assert entries[1008]["character_key"] == "nemesis"
            assert entries[1075]["character_key"] == "nemesis_gnosis"
            assert entries[1008]["element_type"] == "corrosion"
            assert entries[1075]["element_type"] == "corrosion"

            mirrored_id = 1075 if owned_id == 1008 else 1008
            assert entries[mirrored_id]["linked_ownership"] is True
            assert entries[mirrored_id]["ownership_source_doll_id"] == owned_id
            # Storage remains faithful to the imported account payload.
            assert [int(row["doll_id"]) for row in repo.rows("dolls", order_by="doll_id")] == [owned_id]


def test_linked_ownership_expansion_does_not_duplicate_when_both_are_present():
    rows = [
        {"doll_id": 1008, "name": "네메시스", "level": 60, "rank": 6},
        {"doll_id": 1075, "name": "네메시스·연광", "level": 60, "rank": 6},
    ]
    expanded = expand_linked_owned_doll_rows(rows)
    assert sorted(int(row["doll_id"]) for row in expanded) == [1008, 1075]


def test_api_names_and_resource_names_resolve_remolding_identity(tmp_path):
    with Repository(tmp_path / "resolver.db") as repo:
        repo.replace_dolls([
            Doll(1025, "토로로", 60, 6),
            Doll(1045, "비욜카", 60, 6),
            Doll(1075, "네메시스·연광", 60, 6),
        ])
        resolver = DollCharacterResolver(repo)
        assert resolver.character_key_for_doll(1025) == "tololo"
        assert resolver.character_key_for_doll(1045) == "belka"
        assert resolver.character_key_for_doll(1075) == "nemesis_gnosis"


def test_owned_catalog_uses_resolved_element_for_tololo_and_biyoca(tmp_path):
    with Repository(tmp_path / "elements.db") as repo:
        repo.replace_dolls([
            Doll(1025, "토로로", 60, 6),
            Doll(1045, "비욜카", 60, 6),
        ])
        entries = {int(row["doll_id"]): row for row in OwnedDollCatalog(repo).entries()}
        assert entries[1025]["factor_type"] == "sentinel"
        assert entries[1025]["element_type"] == "hydro"
        assert entries[1025]["element_label"] != "속성 미확인"
        assert entries[1045]["factor_type"] == "vanguard"
        assert entries[1045]["element_type"] == "electric"
        assert entries[1045]["element_label"] != "속성 미확인"
