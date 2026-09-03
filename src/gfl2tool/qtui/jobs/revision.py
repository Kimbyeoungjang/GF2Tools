from __future__ import annotations

from collections.abc import Sequence

RevisionToken = tuple[int, int]


def _token(value: Sequence[int] | RevisionToken) -> RevisionToken:
    return int(value[0]), int(value[1])


def result_is_current(
    request_token: Sequence[int] | RevisionToken,
    worker_start: Sequence[int] | RevisionToken,
    worker_end: Sequence[int] | RevisionToken,
    current_request_connection_token: Sequence[int] | RevisionToken,
) -> bool:
    """Validate a worker result without comparing tokens across connections.

    ``PRAGMA data_version`` is meaningful only relative to the *same* SQLite
    connection and ``Connection.total_changes`` is explicitly connection-local.
    Therefore a worker token must never be compared to the GUI repository token.

    The worker start/end pair detects commits by other connections while the job
    was running.  Separately, the request token recorded on the GUI connection is
    compared with the current token from that *same* GUI connection, catching
    edits that happened while/after the calculation before the result is used.
    """
    return (
        _token(worker_start) == _token(worker_end)
        and _token(request_token) == _token(current_request_connection_token)
    )
