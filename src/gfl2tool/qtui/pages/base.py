from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget


class DeferredRefreshPage(QWidget):
    """Common page lifecycle with paint-first, coalesced activation refreshes.

    MainWindow switches the stacked widget before activating a page.  Scheduling
    the first refresh for the next event-loop turn lets Qt paint the navigation
    state immediately and coalesces repeated activation requests into one DB
    refresh.  Heavy pages can override ``on_deactivated`` to cancel work that is
    no longer useful while hidden.
    """

    refreshFailed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_active = False
        self._refresh_queued = False

    @property
    def page_active(self) -> bool:
        return self._page_active

    def set_active(self, active: bool) -> None:
        active = bool(active)
        changed = active != self._page_active
        self._page_active = active
        if active:
            if changed:
                self.on_activated()
            self.request_refresh()
        elif changed:
            self.on_deactivated()

    def request_refresh(self) -> None:
        if not self._page_active or self._refresh_queued:
            return
        self._refresh_queued = True
        QTimer.singleShot(0, self._dispatch_refresh)

    def _dispatch_refresh(self) -> None:
        self._refresh_queued = False
        if not self._page_active:
            return
        try:
            self.refresh()
        except Exception as exc:
            self.refreshFailed.emit(str(exc))

    def on_activated(self) -> None:
        pass

    def on_deactivated(self) -> None:
        pass

    def close_block_reason(self) -> str:
        """Return a user-facing reason when closing would interrupt durable work."""
        return ""

    def prepare_close(self) -> None:
        """Stop transient page activity before the shared application teardown."""
        self.set_active(False)

    def invalidate_cache(self) -> None:
        """Discard page-local presentation caches after shared data changes.

        Pages with revision caches override this method and then call
        ``request_refresh``. Keeping the contract on the base class prevents the
        main window from reaching into page-private attributes.
        """
        self.request_refresh()

    def refresh(self) -> None:
        raise NotImplementedError
