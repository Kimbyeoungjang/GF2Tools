from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_formation_refresh_commits_revision_only_after_successful_load():
    text = _text("src/gfl2tool/qtui/pages/formation.py")
    body = text.split("    def refresh(self) -> None:", 1)[1].split("    def _plan_selected", 1)[0]
    assert "rows = list(self.svc.list())" in body
    assert body.index("rows = list(self.svc.list())") < body.index("self._refresh_token = token")
    # Clearing the visible list must happen after the service read succeeds.
    assert body.index("rows = list(self.svc.list())") < body.index("self.plans.clear()")






def test_remolding_recommendation_detail_commits_render_token_after_synchronous_queries():
    text = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    body = text.split("    def _refresh_detail(self):", 1)[1].split("    def _start_owned_score", 1)[0]
    assert body.index("svc.get_character(key)") < body.index("self._detail_render_token = detail_token")
    assert body.index("self._render_recommendations(svc, key, factor_names)") < body.index("self._detail_render_token = detail_token")
    assert "except Exception" not in _text("src/gfl2tool/qtui/pages/remolding_optimizer.py").split("    def _prepare_entries(self):",1)[1].split("    def _service",1)[0]
