from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import uuid
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_json

CHECKLIST_SCHEMA = 1
CHECKLIST_FILENAME = "checklist.json"
CATEGORIES = ("daily", "weekly", "monthly")
CATEGORY_LABELS = {
    "daily": "일간",
    "weekly": "주간",
    "monthly": "월간",
}

_DEFAULT_LABELS = {
    "daily": (
        "교역실 일일 보급 상자",
        "활동층 요리 / 드링크바",
        "변경추진 - 결정채집",
        "정보조각 소모",
        "서클 과업",
        "이벤트 티켓 소모",
        "의뢰 / 이벤트 / 일지 보상 수령",
        "흙먼지 / 개척원정",
    ),
    "weekly": (
        "교역실 주간 깜짝 보급 상자",
        "변경추진 - 한정 현상수배",
        "위상충돌",
    ),
    "monthly": (
        "교역실 / 위시리스트 월간 물품 구매",
    ),
}


def default_items() -> dict[str, list[dict[str, Any]]]:
    return {
        category: [
            {"id": f"default-{category}-{index + 1}", "label": label, "checked": False}
            for index, label in enumerate(_DEFAULT_LABELS[category])
        ]
        for category in CATEGORIES
    }


def new_item(label: str = "새 항목") -> dict[str, Any]:
    return {"id": f"user-{uuid.uuid4().hex}", "label": str(label or "새 항목").strip() or "새 항목", "checked": False}


def _period_tokens(today: date) -> dict[str, str]:
    monday = today - timedelta(days=today.weekday())
    return {
        "daily": today.isoformat(),
        "weekly": monday.isoformat(),
        "monthly": f"{today.year:04d}-{today.month:02d}",
    }


class ChecklistStore:
    """Persistent daily/weekly/monthly checklist with calendar-bound resets."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / CHECKLIST_FILENAME

    def _normalized(self, raw: object, *, today: date) -> tuple[dict[str, Any], bool]:
        changed = False
        defaults = default_items()
        if not isinstance(raw, dict) or int(raw.get("schema") or 0) != CHECKLIST_SCHEMA:
            raw = {}
            changed = True

        source_items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
        items: dict[str, list[dict[str, Any]]] = {}
        for category in CATEGORIES:
            rows = source_items.get(category) if isinstance(source_items, dict) else None
            if not isinstance(rows, list):
                rows = defaults[category]
                changed = True
            normalized_rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    changed = True
                    continue
                label = str(row.get("label") or "").strip()
                if not label:
                    changed = True
                    continue
                item_id = str(row.get("id") or "").strip()
                if not item_id or item_id in seen:
                    item_id = f"user-{uuid.uuid4().hex}"
                    changed = True
                seen.add(item_id)
                normalized_rows.append({"id": item_id, "label": label[:200], "checked": bool(row.get("checked"))})
            items[category] = normalized_rows

        periods = raw.get("periods") if isinstance(raw.get("periods"), dict) else {}
        expected = _period_tokens(today)
        normalized_periods: dict[str, str] = {}
        for category in CATEGORIES:
            previous = str(periods.get(category) or "") if isinstance(periods, dict) else ""
            current = expected[category]
            normalized_periods[category] = current
            if previous != current:
                if any(bool(item.get("checked")) for item in items[category]):
                    for item in items[category]:
                        item["checked"] = False
                changed = True

        return {
            "schema": CHECKLIST_SCHEMA,
            "periods": normalized_periods,
            "items": items,
        }, changed

    def load(self, *, today: date | None = None) -> dict[str, Any]:
        today = today or date.today()
        try:
            import json
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        except (OSError, ValueError):
            raw = {}
        payload, changed = self._normalized(raw, today=today)
        if changed or not self.path.is_file():
            self.save(payload)
        return deepcopy(payload)

    def save(self, payload: dict[str, Any], *, durable: bool = True) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, payload, ensure_ascii=False, indent=2, durable=durable)

    def set_checked(self, category: str, item_id: str, checked: bool, *, today: date | None = None) -> None:
        if category not in CATEGORIES:
            raise KeyError(category)
        payload = self.load(today=today)
        for item in payload["items"][category]:
            if str(item.get("id")) == str(item_id):
                item["checked"] = bool(checked)
                self.save(payload)
                return
        raise KeyError(item_id)

    def replace_items(self, items: dict[str, list[dict[str, Any]]], *, today: date | None = None) -> None:
        payload = self.load(today=today)
        normalized_source = {category: list(items.get(category) or []) for category in CATEGORIES}
        candidate = {**payload, "items": normalized_source}
        normalized, _changed = self._normalized(candidate, today=today or date.today())
        self.save(normalized)

    def reset_defaults(self, *, today: date | None = None) -> None:
        today = today or date.today()
        self.save({
            "schema": CHECKLIST_SCHEMA,
            "periods": _period_tokens(today),
            "items": default_items(),
        })

    def completion(self, *, today: date | None = None) -> dict[str, tuple[int, int]]:
        payload = self.load(today=today)
        out: dict[str, tuple[int, int]] = {}
        for category in CATEGORIES:
            rows = payload["items"][category]
            out[category] = (sum(1 for row in rows if row.get("checked")), len(rows))
        return out
