from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TextIO


def _flush_and_sync(handle: TextIO, *, durable: bool) -> None:
    handle.flush()
    if durable:
        os.fsync(handle.fileno())


def _fsync_parent(path: Path) -> None:
    """Best-effort directory sync after an atomic replace.

    POSIX filesystems need the parent directory entry flushed for the rename to
    survive an abrupt power loss. Windows does not expose a portable directory
    fsync through Python, so failures here are deliberately ignored.
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextmanager
def atomic_text_writer(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
    durable: bool = True,
) -> Iterator[TextIO]:
    """Yield a same-directory temporary text file and replace *path* on success.

    The destination is never truncated until the temporary file has been fully
    flushed (and fsynced when ``durable`` is true). An exception removes only
    the temporary file, leaving an existing destination byte-for-byte intact.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    handle: TextIO | None = None
    try:
        handle = os.fdopen(fd, "w", encoding=encoding, newline=newline)
        fd = -1
        yield handle
        _flush_and_sync(handle, durable=durable)
        handle.close()
        handle = None
        os.replace(tmp, target)
        if durable:
            _fsync_parent(target)
    except Exception:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise



def atomic_write_bytes(path: str | Path, data: bytes, *, durable: bool = True) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        os.replace(tmp, target)
        if durable:
            _fsync_parent(target)
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def atomic_write_json(
    path: str | Path,
    payload: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    encoding: str = "utf-8",
    durable: bool = True,
) -> Path:
    target = Path(path)
    with atomic_text_writer(target, encoding=encoding, durable=durable) as handle:
        json.dump(payload, handle, ensure_ascii=ensure_ascii, indent=indent)
    return target
