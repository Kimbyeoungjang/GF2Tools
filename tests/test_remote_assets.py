from pathlib import Path

from gfl2tool.services.remote_assets import (
    ensure_cache_path, remote_asset_cache_path, request_from_cache_path,
)


def test_rest_asset_cache_paths_are_deterministic(tmp_path):
    target = remote_asset_cache_path(tmp_path, 1052, kind="gacha")
    assert target == tmp_path / "master_data" / "illustrations" / "doll_1052_gacha.png"
    assert request_from_cache_path(target) == (1052, "gacha")
    portrait = remote_asset_cache_path(tmp_path, 1052, kind="portrait")
    assert request_from_cache_path(portrait) == (1052, "portrait")


def test_rest_asset_loader_never_fetches_missing_files(tmp_path):
    target = remote_asset_cache_path(tmp_path, 1052)
    assert ensure_cache_path(target) is False
    target.parent.mkdir(parents=True)
    target.write_bytes(b"rest-cache")
    assert ensure_cache_path(target) is True


def test_non_rest_cache_filename_is_rejected(tmp_path):
    assert request_from_cache_path(Path(tmp_path) / "custom.png") is None
