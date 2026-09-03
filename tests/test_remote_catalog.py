from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from gfl2tool.services.remote_catalog import (
    DEFAULT_GITHUB_RELEASE_URL, DEFAULT_REMOTE_API_CONFIG, DEFAULT_STATIC_API_URL, RemoteCatalogBootstrap,
)


def test_remote_catalog_placeholder_uses_official_release_then_static_api_defaults(tmp_path):
    catalog = RemoteCatalogBootstrap(tmp_path / "data")
    state = catalog.ensure_placeholder()
    payload = json.loads(state.config_path.read_text(encoding="utf-8"))
    assert payload["github_release_url"] == DEFAULT_GITHUB_RELEASE_URL
    assert payload["pages_base_url"] == DEFAULT_STATIC_API_URL
    assert payload["base_url"] == DEFAULT_STATIC_API_URL
    assert payload["enabled"] is True
    assert DEFAULT_REMOTE_API_CONFIG["github_release_url"] == DEFAULT_GITHUB_RELEASE_URL
    assert DEFAULT_REMOTE_API_CONFIG["pages_base_url"] == DEFAULT_STATIC_API_URL


def test_remote_catalog_migrates_old_unconfigured_endpoint_template_to_official_defaults(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "remote-api.json").write_text(json.dumps({"schema": 2, "base_url": ""}), encoding="utf-8")
    catalog = RemoteCatalogBootstrap(data)
    catalog.ensure_placeholder()
    payload = catalog.load_config()
    assert payload["schema"] == 4
    assert payload["github_release_url"] == DEFAULT_GITHUB_RELEASE_URL
    assert payload["pages_base_url"] == DEFAULT_STATIC_API_URL


def test_remote_catalog_user_can_persist_both_provider_addresses(tmp_path):
    catalog = RemoteCatalogBootstrap(tmp_path / "data")
    catalog.ensure_placeholder()
    github, pages = catalog.set_provider_urls(
        "https://example.com/releases/latest/download/offline.zip",
        "https://example.com/gfl2/",
    )
    assert github.endswith("offline.zip")
    assert pages == "https://example.com/gfl2"
    payload = catalog.load_config()
    assert payload["enabled"] is True
    assert payload["github_release_url"] == github
    assert payload["pages_base_url"] == pages


def test_remote_catalog_rejects_non_http_download_address(tmp_path):
    catalog = RemoteCatalogBootstrap(tmp_path / "data")
    catalog.ensure_placeholder()
    with pytest.raises(ValueError):
        catalog.set_provider_urls("file:///tmp/data.zip", "")


def test_offline_package_import_matches_supplied_package_contract(tmp_path):
    package = next(
        (path for path in (
            Path("/mnt/data/gfl2-gf2tools-offline-table.1151583(2).zip"),
            Path("/mnt/data/gfl2-gf2tools-offline-table.1151583(1).zip"),
            Path("/mnt/data/gfl2-gf2tools-offline-table.1151583.zip"),
        ) if path.is_file()),
        None,
    )
    if package is None:
        pytest.skip("conversation fixture is unavailable")
    catalog = RemoteCatalogBootstrap(tmp_path / "data")
    result = catalog.install_offline_package(package)
    assert result.data_version == "table.1151583"
    assert result.provider == "오프라인 패키지"
    dolls = json.loads((catalog.site_dir / "api/v1/dolls.json").read_text(encoding="utf-8"))
    weapons = json.loads((catalog.site_dir / "api/v1/weapons.json").read_text(encoding="utf-8"))
    assert dolls["count"] == 62
    assert weapons["count"] == 181

    split_counts = {
        "remolding.json": 139,
        "weapon_part_effects.json": 16,
        "weapon_part_power_map.json": 33,
        "weapon_part_types.json": 37,
        "item_ranks.json": 4,
        "unclassified_polarity_plans.json": 6,
    }
    for filename, expected in split_counts.items():
        payload = json.loads((catalog.site_dir / "api/v1" / filename).read_text(encoding="utf-8"))
        assert payload["count"] == expected

    normalized = json.loads(
        (tmp_path / "data/reference_data/program_remolding_catalog.json").read_text(encoding="utf-8")
    )
    assert len(normalized["property_definitions"]) == 139
    assert len(normalized["power_effects"]) == 16
    assert len(normalized["power_map"]) == 33
    assert len(normalized["mod_types"]) == 37
    assert len(normalized["ranks"]) == 4
    assert len(normalized["polarity_plans"]) == 6


def test_pages_sync_preserves_current_split_api_datasets(tmp_path, monkeypatch):
    exported = Path("/mnt/data/new_exported")
    if not (exported / "latest.json").is_file():
        pytest.skip("extracted Pages fixture is unavailable")

    catalog = RemoteCatalogBootstrap(tmp_path / "data")
    catalog.ensure_placeholder()
    catalog.set_provider_urls("", "https://example.invalid/gf2")

    def local_download(url: str) -> bytes:
        marker = "https://example.invalid/gf2/"
        assert url.startswith(marker)
        relative = url[len(marker):]
        return (exported / relative).read_bytes()

    monkeypatch.setattr(catalog, "_download", local_download)
    result = catalog.sync_now()
    assert result.changed is True
    assert result.provider == "정적 API"
    assert result.data_version == "table.1151583"
    for filename in (
        "remolding.json",
        "weapon_part_effects.json",
        "weapon_part_power_map.json",
        "weapon_part_types.json",
        "item_ranks.json",
        "unclassified_polarity_plans.json",
    ):
        assert (catalog.site_dir / "api/v1" / filename).is_file(), filename


def test_update_check_uses_github_release_tag_and_does_not_download_asset(tmp_path, monkeypatch):
    catalog = RemoteCatalogBootstrap(tmp_path / "data")
    catalog.ensure_placeholder()
    version_dir = tmp_path / "data" / "reference_data"
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "program_version.json").write_text(
        json.dumps({"game_version": "2.0.3935", "data_version": "table.1"}), encoding="utf-8"
    )
    calls = []

    def fake_download(url: str) -> bytes:
        calls.append(url)
        assert url.endswith("/releases/latest")
        return json.dumps({
            "tag_name": "v2.0.3936",
            "assets": [{
                "name": "gfl2-gf2tools-offline-table.1151583.zip",
                "browser_download_url": "https://example.invalid/offline.zip",
            }],
        }).encode("utf-8")

    monkeypatch.setattr(catalog, "_download", fake_download)
    result = catalog.check_for_update()
    assert result.reachable is True
    assert result.update_available is True
    assert result.provider == "GitHub Release"
    assert result.current_game_version == "2.0.3935"
    assert result.latest_game_version == "2.0.3936"
    assert calls == ["https://api.github.com/repos/Kimbyeoungjang/GF2Tools-api/releases/latest"]


def test_update_check_falls_back_to_static_api_only_when_release_check_fails(tmp_path, monkeypatch):
    catalog = RemoteCatalogBootstrap(tmp_path / "data")
    catalog.ensure_placeholder()
    version_dir = tmp_path / "data" / "reference_data"
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "program_version.json").write_text(
        json.dumps({"game_version": "2.0.3936", "data_version": "table.1151583"}), encoding="utf-8"
    )

    def fake_download(url: str) -> bytes:
        if "api.github.com" in url:
            raise OSError("release blocked")
        if url.endswith("latest.json"):
            return json.dumps({"game_version": "2.0.3936", "data_version": "table.1151583"}).encode("utf-8")
        raise AssertionError(url)

    monkeypatch.setattr(catalog, "_download", fake_download)
    result = catalog.check_for_update()
    assert result.reachable is True
    assert result.update_available is False
    assert result.provider == "정적 API"
