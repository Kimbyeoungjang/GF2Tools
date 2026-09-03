from __future__ import annotations

from gfl2tool.services.doll_categories import DollCategoryStore


def test_doll_category_store_assign_remove_and_persist(tmp_path):
    store = DollCategoryStore(tmp_path)
    store.assign("레이드 1군", ["doll:1", "doll:2", "doll:1"])
    store.assign("애정", ["doll:2"])

    assert store.names() == ["레이드 1군", "애정"]
    assert store.keys("레이드 1군") == {"doll:1", "doll:2"}
    assert store.categories_for("doll:2") == ["레이드 1군", "애정"]

    store.remove("레이드 1군", ["doll:1"])
    assert store.keys("레이드 1군") == {"doll:2"}
    store.remove("레이드 1군", ["doll:2"])
    assert "레이드 1군" not in store.names()

    reloaded = DollCategoryStore(tmp_path)
    assert reloaded.keys("애정") == {"doll:2"}
