from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIDGETS = ROOT / "src" / "gfl2tool" / "qtui" / "widgets.py"
QTUI = ROOT / "src" / "gfl2tool" / "qtui"


def _helper_keywords(name: str) -> tuple[set[str], bool]:
    tree = ast.parse(WIDGETS.read_text(encoding="utf-8"), filename=str(WIDGETS))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            args = node.args
            accepted = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
            return accepted, args.kwarg is not None
    raise AssertionError(f"helper not found: {name}")


def test_common_qt_helper_calls_only_use_supported_keywords() -> None:
    helpers = ("configure_tree_widget", "configure_table_view")
    contracts = {name: _helper_keywords(name) for name in helpers}
    failures: list[str] = []

    for path in sorted(QTUI.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name not in contracts:
                continue
            accepted, has_var_kwargs = contracts[name]
            if has_var_kwargs:
                continue
            for keyword in node.keywords:
                if keyword.arg is not None and keyword.arg not in accepted:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        f"{name}() got unsupported keyword {keyword.arg!r}"
                    )

    assert not failures, "\n".join(failures)
