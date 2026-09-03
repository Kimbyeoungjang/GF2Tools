from __future__ import annotations

import ast
from pathlib import Path

from gfl2tool.services import remote_assets

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src" / "gfl2tool"

DENIED_IMPORT_ROOTS = {
    "mitmproxy", "UnityPy", "scapy", "pyshark", "socket", "psutil",
    "pymem", "frida", "win32process", "win32api",
}
DENIED_TOKENS = {
    "assetbundles_windows", "gf2_exilium_data", "streamingassets",
    "readprocessmemory", "openprocess", "createtoolhelp32snapshot",
    "npcap", "winpcap", "protobuf_wire",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_main_runtime_has_no_packet_game_installation_or_hidden_network_stack():
    violations: list[str] = []
    for path in sorted(RUNTIME.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        imports = _imports(path)
        denied_imports = sorted(imports & DENIED_IMPORT_ROOTS)
        if denied_imports:
            violations.append(f"{rel}: imports {denied_imports}")
        for token in sorted(DENIED_TOKENS):
            if token in lowered:
                violations.append(f"{rel}: contains {token}")
        if "ctypes" in imports and rel != "src/gfl2tool/qtui/tactic_overlay.py":
            violations.append(f"{rel}: ctypes outside overlay window integration")
        if "urllib" in imports or "requests" in imports or "httpx" in imports:
            approved = {
                "src/gfl2tool/services/remote_catalog.py",
                "src/gfl2tool/services/remote_assets.py",
                "src/gfl2tool/services/app_update.py",
            }
            if rel not in approved:
                violations.append(f"{rel}: network client outside program-data delivery boundary")
    assert violations == []


def test_main_runtime_contains_no_archived_helper_modules():
    assert not (RUNTIME / "services" / "protobuf_wire.py").exists()
    assert not (ROOT / "archive_tools").exists()
    assert not (ROOT / "schemas").exists()


def test_character_images_are_local_rest_cache_only(tmp_path):
    path = remote_assets.remote_asset_cache_path(tmp_path, 1052, kind="portrait")
    assert remote_assets.ensure_cache_path(path) is False
    source = Path(remote_assets.__file__).read_text(encoding="utf-8")
    assert "pages_base_url" in source
    assert "gfl2_exilium_data" not in source.casefold()
