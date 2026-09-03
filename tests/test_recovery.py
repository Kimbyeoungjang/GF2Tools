from __future__ import annotations

import json
import os
import time
from pathlib import Path

from gfl2tool.repository import Repository


















def test_job_revision_validation_never_compares_tokens_across_connections(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.qtui.jobs.revision import result_is_current

    db = tmp_path / "revision.db"
    main = Repository(db)
    try:
        # Give the GUI connection local changes so its total_changes is
        # intentionally different from a fresh worker connection.
        main.replace_dolls([Doll(1, "A", 60, 1)])
        request = main.state_token()
        with Repository(db) as worker:
            worker_start = worker.state_token()
            worker_end = worker.state_token()
            assert worker_end != request, "cross-connection state_token equality is not a valid contract"
            assert result_is_current(request, worker_start, worker_end, main.state_token()) is True

        # A local GUI edit after dispatch invalidates the request even though the
        # already-finished worker connection itself was stable.
        main.replace_dolls([Doll(1, "A", 60, 1), Doll(2, "B", 60, 1)])
        assert result_is_current(request, worker_start, worker_end, main.state_token()) is False
    finally:
        main.close()


def test_job_revision_validation_detects_external_commit_during_worker(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.qtui.jobs.revision import result_is_current

    db = tmp_path / "worker-revision.db"
    main = Repository(db)
    worker = Repository(db)
    try:
        request = main.state_token()
        worker_start = worker.state_token()
        with Repository(db) as external:
            external.replace_dolls([Doll(99, "external", 60, 1)])
        worker_end = worker.state_token()
        assert worker_start != worker_end
        assert result_is_current(request, worker_start, worker_end, main.state_token()) is False
    finally:
        worker.close(); main.close()


def test_qt_job_sources_do_not_compare_worker_token_to_gui_token_directly():
    root = Path(__file__).resolve().parents[1]
    sources = [
        root / "src/gfl2tool/qtui/pages/remolding_optimizer.py",
        root / "src/gfl2tool/qtui/dialogs/formation_optimize.py",
        root / "src/gfl2tool/qtui/dialogs/remolding_subset.py",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "tuple(end) != self.repo.state_token()" not in text
    formation = (root / "src/gfl2tool/qtui/dialogs/formation_optimize.py").read_text(encoding="utf-8")
    assert "_result_request_token" in formation
    assert "result_is_current" in formation
