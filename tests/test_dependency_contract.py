from pathlib import Path
import tomllib


def test_core_project_dependencies_match_bootstrap_image_feature_contract():
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    joined = "\n".join(dependencies).lower()
    assert "protobuf" not in joined
    assert "pyside6-essentials" in joined
    assert "unitypy" not in joined
    assert "pillow" in joined
    bootstrap = (root / "bootstrap.py").read_text(encoding="utf-8").lower()
    assert "unitypy" not in bootstrap
    assert "mitmproxy" not in bootstrap
    assert "pillow" in bootstrap
