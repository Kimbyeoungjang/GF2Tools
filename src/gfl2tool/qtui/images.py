from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QThreadPool, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QImageReader, QPixmap

from ..services.remote_assets import ensure_cache_path
from .workers import run_worker


class PortraitLoader(QObject):
    """Shared asynchronous portrait decoder and GUI-thread pixmap cache.

    Roster delegates request portraits only when a card is actually painted.
    QImage decoding stays on a small private thread pool while QPixmap creation
    remains on the GUI thread and is shared across every page/dialog.  This keeps
    cold-cache scrolling light and avoids storing duplicate scaled pixmaps in
    each roster model.
    """

    imageReady = Signal(str, object)
    imageFailed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_items: int = 96,
        max_pixmaps: int = 256,
        decode_edge: int = 768,
    ):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.max_items = max(24, int(max_items))
        self.max_pixmaps = max(64, int(max_pixmaps))
        self.decode_edge = max(256, int(decode_edge))
        self._cache: OrderedDict[str, QImage] = OrderedDict()
        self._pixmaps: OrderedDict[tuple[str, int, int, int], QPixmap] = OrderedDict()
        self._pending: set[str] = set()
        self._failed: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _file_revision(key: str) -> tuple[int, int]:
        try:
            stat = Path(key).stat()
            return int(stat.st_mtime_ns), int(stat.st_size)
        except OSError:
            return 0, 0

    def get(self, path: str | Path | None) -> QImage | None:
        if not path:
            return None
        key = str(path)
        image = self._cache.get(key)
        if image is not None:
            self._cache.move_to_end(key)
        return image

    def pixmap(self, path: str | Path | None, size: QSize, *, dpr: float | None = None) -> QPixmap | None:
        """Return a shared scaled pixmap, or queue a decode and return ``None``."""
        if not path:
            return None
        key = str(path)
        width, height = max(1, int(size.width())), max(1, int(size.height()))
        if dpr is None:
            screen = QGuiApplication.primaryScreen()
            dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
        dpr = max(1.0, float(dpr))
        physical_width = max(1, int(round(width * dpr)))
        physical_height = max(1, int(round(height * dpr)))
        # Physical dimensions alone are not a safe cache key: e.g. 100 logical
        # px at 150% DPI and 150 logical px at 100% DPI both decode to 150px,
        # but the QPixmap device-pixel ratio must differ. Reusing the former on
        # the latter makes Qt rescale it again and visibly softens portraits.
        dpr_key = max(100, int(round(dpr * 100)))
        cache_key = (key, physical_width, physical_height, dpr_key)
        pix = self._pixmaps.get(cache_key)
        if pix is not None:
            self._pixmaps.move_to_end(cache_key)
            return pix
        image = self.get(key)
        if image is None:
            self.request(key)
            return None
        pix = QPixmap.fromImage(image).scaled(
            physical_width,
            physical_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pix.setDevicePixelRatio(dpr)
        self._pixmaps[cache_key] = pix
        self._pixmaps.move_to_end(cache_key)
        while len(self._pixmaps) > self.max_pixmaps:
            self._pixmaps.popitem(last=False)
        return pix

    def request(self, path: str | Path | None) -> None:
        if not path:
            return
        key = str(path)
        if key in self._cache:
            self._cache.move_to_end(key)
            # Cached consumers can call get()/pixmap() synchronously.  Re-emitting
            # here would repaint every connected roster whenever another page asks
            # for an already-decoded portrait.
            return
        if key in self._pending:
            return
        revision = self._file_revision(key)
        if self._failed.get(key) == revision:
            return
        self._pending.add(key)

        def work():
            loaded_revision = revision
            if loaded_revision == (0, 0) and ensure_cache_path(key):
                loaded_revision = self._file_revision(key)
            reader = QImageReader(key)
            reader.setAutoTransform(True)
            source_size = reader.size()
            if source_size.isValid() and max(source_size.width(), source_size.height()) > self.decode_edge:
                reader.setScaledSize(
                    source_size.scaled(
                        QSize(self.decode_edge, self.decode_edge),
                        Qt.AspectRatioMode.KeepAspectRatio,
                    )
                )
            image = reader.read()
            # Some image plugins ignore setScaledSize. Keep the memory ceiling
            # even for those formats, but normally this branch is skipped.
            if not image.isNull() and max(image.width(), image.height()) > self.decode_edge:
                image = image.scaled(
                    self.decode_edge,
                    self.decode_edge,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            return key, loaded_revision, image

        run_worker(
            self.pool, work, on_result=self._ready,
            on_error=lambda _error, k=key, r=revision:self._failed_ready(k,r),
        )

    def invalidate(self, path: str | Path | None = None) -> None:
        """Drop stale decoded/scaled data after an image is replaced on disk."""
        if path is None:
            self._cache.clear()
            self._pixmaps.clear()
            self._failed.clear()
            return
        key = str(path)
        self._cache.pop(key, None)
        self._failed.pop(key, None)
        for cache_key in [k for k in self._pixmaps if k[0] == key]:
            self._pixmaps.pop(cache_key, None)

    def _failed_ready(self, key: str, revision: tuple[int, int]) -> None:
        self._pending.discard(key)
        self._failed[key] = revision
        self.imageFailed.emit(key)

    def _ready(self, payload) -> None:
        key, revision, image = payload
        self._pending.discard(key)
        # Extraction may replace a portrait while a decode is still running.
        # Never publish/cache pixels from the superseded file revision. Queue the
        # new revision instead; this also prevents a rare one-refresh stale image.
        if self._file_revision(key) != tuple(revision):
            self.invalidate(key)
            self.request(key)
            return
        if image is None or image.isNull():
            self._failed[key] = revision
            return
        self._failed.pop(key, None)
        # Replacing an image at the same path invalidates all scaled variants.
        for cache_key in [k for k in self._pixmaps if k[0] == key]:
            self._pixmaps.pop(cache_key, None)
        self._cache[key] = image
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_items:
            old_key, _old = self._cache.popitem(last=False)
            for cache_key in [k for k in self._pixmaps if k[0] == old_key]:
                self._pixmaps.pop(cache_key, None)
        self.imageReady.emit(key, image)
