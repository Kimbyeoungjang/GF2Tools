from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

from gfl2tool.models import Doll, Remolding, RemoldingSlot
from gfl2tool.qtui.data import OwnedDollCatalog
from gfl2tool.repository import Repository
from gfl2tool.services.formations import FormationService
from gfl2tool.services.optimizer import EquipmentOptimizer, _hungarian_max

ROOT = Path(__file__).resolve().parents[1]


def _piece(uid: str, rid: int, factor: str, option_key: str) -> Remolding:
    return Remolding(uid, rid, "", [RemoldingSlot("x", option_key, option_key=option_key, factor_type=factor)])


def test_clean_bootstrap_installs_only_main_app_runtime_dependencies():
    source = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    assert "UnityPy" not in source
    assert "mitmproxy" not in source
    assert "PIL" in source
    assert "Pillow" in source
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "PySide6-Essentials" in pyproject
    assert "UnityPy" not in pyproject and "mitmproxy" not in pyproject
    assert "select_requirements" in source


def test_read_transaction_keeps_one_wal_snapshot(tmp_path):
    db = tmp_path / "snapshot.db"
    with Repository(db) as reader:
        reader.replace_dolls([Doll(1008, "A", 60, 1)])
        with reader.read_transaction():
            assert reader.inventory_summary()["dolls"] == 1
            with Repository(db) as writer:
                writer.replace_dolls([Doll(1008, "A", 60, 1), Doll(1032, "B", 60, 1)])
                writer.replace_remoldings([_piece("r1", 985401, "sentinel", "sentinel_1")])
            # Both reads remain on the snapshot established before writer commit.
            summary = reader.inventory_summary()
            assert summary["dolls"] == 1
            assert summary["remoldings"] == 0
        summary = reader.inventory_summary()
        assert summary["dolls"] == 2
        assert summary["remoldings"] == 1


def test_catalog_ignores_unrelated_db_writes_but_invalidates_doll_changes(tmp_path):
    with Repository(tmp_path / "catalog.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        catalog = OwnedDollCatalog(repo)
        first = catalog.entries()
        repo.set_meta("unrelated", "1")
        assert catalog.entries() is first
        repo.set_doll_favorite(1008, True)
        second = catalog.entries()
        assert second is not first
        assert second[0]["favorite"] is True


def test_optimizer_cancellation_reaches_hungarian_and_public_entrypoints(tmp_path):
    with pytest.raises(InterruptedError):
        _hungarian_max([[3.0, 2.0], [2.0, 3.0]], lambda: True)

    with Repository(tmp_path / "cancel.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        repo.replace_remoldings([_piece("s1", 985401, "sentinel", "sentinel_1")])
        optimizer = EquipmentOptimizer(repo)
        with pytest.raises(InterruptedError):
            optimizer.best_remolding_set("nemesis", should_cancel=lambda: True)
        with pytest.raises(InterruptedError):
            optimizer.allocate_remoldings(["nemesis"], should_cancel=lambda: True)
        pid = FormationService(repo).create("cancel")
        FormationService(repo).set_member(pid, 1, 1008)
        with pytest.raises(InterruptedError):
            optimizer.optimize_formation(pid, {"remolding"}, should_cancel=lambda: True)


def test_release_package_is_byte_deterministic(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("gfl2_package_release", ROOT / "tools/package_release.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("A", encoding="utf-8")
    nested = project / "src"
    nested.mkdir()
    (nested / "한글.txt").write_text("B", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", project)

    first = tmp_path / "out1"
    second = tmp_path / "out2"
    z1, _, _ = module.package("9.9.9", first)
    # Deliberately change source mtimes; archive bytes must remain identical.
    (project / "a.txt").touch()
    z2, _, _ = module.package("9.9.9", second)
    assert module.sha256(z1) == module.sha256(z2)
    assert z1.read_bytes() == z2.read_bytes()


def test_release_audit_enforces_shared_qt_hot_path_helpers():
    source = (ROOT / "tools/package_release.py").read_text(encoding="utf-8")
    assert "direct QTableView configuration outside widgets.py" in source
    assert "direct Worker construction outside workers.py" in source
    assert "direct portrait path resolution outside data.py" in source


def test_all_registered_characters_have_exactly_six_slots(tmp_path):
    from gfl2tool import reference
    from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService
    characters = reference.remolding_characters()
    assert len(characters) >= 50
    with Repository(tmp_path / "character-slots.db") as repo:
        svc = RemoldingRecommendationService(repo)
        for key in characters:
            char = svc.get_character(key)
            slots = list(char.get("slotDistribution") or [])
            assert sum(int(row.get("count") or 0) for row in slots) == 6, key
            assert all(str(row.get("factorType") or "") in {"sentinel", "vanguard", "support", "bulwark"} for row in slots), key


@pytest.mark.parametrize("seed", range(24))
def test_global_allocation_randomized_invariants(tmp_path, seed):
    import random
    from gfl2tool import reference

    rng = random.Random(seed)
    keys = sorted(reference.remolding_characters())
    selected = rng.sample(keys, rng.randint(1, min(4, len(keys))))
    factor_meta = {
        "sentinel": (985401, "sentinel_1"),
        "vanguard": (985201, "vanguard_1"),
        "support": (985301, "support_1"),
        "bulwark": (985101, "bulwark_1"),
    }
    pieces = []
    serial = 0
    for factor, (rid, option) in factor_meta.items():
        for _ in range(rng.randint(0, 10)):
            serial += 1
            pieces.append(_piece(f"{factor[0]}-{seed}-{serial}", rid, factor, option))
    rng.shuffle(pieces)

    with Repository(tmp_path / f"fuzz-{seed}.db") as repo:
        repo.replace_remoldings(pieces)
        result = EquipmentOptimizer(repo).allocate_remoldings(selected)

    rows = list(result.get("rows") or [])
    assert {str(row.get("character_key")) for row in rows} == set(selected)
    all_uids = [str(piece.get("uid")) for row in rows for piece in row.get("pieces") or []]
    assert len(all_uids) == len(set(all_uids))
    assert all(len(row.get("pieces") or []) <= 6 for row in rows)
    assert int(result.get("missing_slots") or 0) == sum(int(row.get("missing") or 0) for row in rows)


def test_shared_error_dialog_is_used_for_sync_and_worker_failures():
    widgets = (ROOT / "src/gfl2tool/qtui/widgets.py").read_text(encoding="utf-8")
    assert widgets.count("def show_error(") == 1
    assert "setDetailedText(detail)" in widgets
    for rel in (
        "src/gfl2tool/qtui/pages/remolding_optimizer.py",
        "src/gfl2tool/qtui/dialogs/formation_optimize.py",
        "src/gfl2tool/qtui/pages/data_sync.py",
    ):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "show_error" in source
