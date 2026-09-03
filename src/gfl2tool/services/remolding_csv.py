from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .. import reference
from ..atomic_io import atomic_text_writer
from ..models import RemoldingSlot
from ..repository import Repository

REMOLDING_CSV_FIELDS = ("uid", "stat1", "stat2", "stat3")



def decode_remolding_contents(content: bytes) -> list[RemoldingSlot]:
    """Decode the 3 x 3-byte remolding option codes used by logger CSV files.

    This is intentionally not a network-payload decoder.  The main app only
    receives bytes reconstructed from an already-exported CSV row.
    """
    filtered = bytes(byte for byte in content if byte != 0x01)
    index = reference.remolding_code_index()
    labels = reference.remoldings()
    slots: list[RemoldingSlot] = []
    for offset in range(0, min(len(filtered), 9), 3):
        chunk = filtered[offset:offset + 3]
        if len(chunk) != 3:
            break
        code = " ".join(f"{byte:02x}" for byte in chunk)
        meta = index.get(code, {})
        slots.append(
            RemoldingSlot(
                code=code,
                name=labels.get(code) if meta else None,
                option_key=meta.get("option_key"),
                variant=meta.get("variant"),
                factor_type=meta.get("factor_type"),
                element_type=meta.get("element_type"),
                level_contribution=int(meta.get("variant")) if meta.get("variant") else None,
            )
        )
    return slots

def default_remoldings_csv_name(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"gfl2logger_remoldings_{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"


def _logger_row(uid: str, raw_contents_hex: str) -> dict[str, str]:
    """Convert one current remolding row to gfl2logger interchange columns."""
    try:
        raw = bytes.fromhex(str(raw_contents_hex or ""))
    except ValueError:
        raw = b""
    hex_codes = [f"{byte:02x}" for byte in raw if byte != 0x01]
    stats = [
        " ".join(hex_codes[0:3]),
        " ".join(hex_codes[3:6]),
        " ".join(hex_codes[6:9]),
    ]
    clean_uid = str(uid or "")
    if clean_uid.startswith("U"):
        clean_uid = clean_uid[1:]
    return {
        "uid": "U" + clean_uid,
        "stat1": stats[0],
        "stat2": stats[1],
        "stat3": stats[2],
    }


def export_remoldings_csv(repo: Repository, path: str | Path) -> int:
    """Write owned remoldings in the original gfl2logger CSV interchange format."""
    target = Path(path)
    rows = repo.con.execute(
        "SELECT uid,raw_contents_hex FROM remoldings ORDER BY CAST(uid AS INTEGER),uid"
    ).fetchall()
    with atomic_text_writer(target, encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REMOLDING_CSV_FIELDS), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_logger_row(str(row["uid"]), str(row["raw_contents_hex"] or "")))
    return len(rows)
