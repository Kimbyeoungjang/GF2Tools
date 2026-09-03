from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..atomic_io import atomic_write_json

MAX_DOLL_CATEGORIES = 64
MAX_DOLLS_PER_CATEGORY = 256
MAX_CATEGORY_NAME = 48


class DollCategoryStore:
    """Small user-owned grouping store for doll character keys.

    Categories intentionally live outside bulk user-data replacement.
    Importing a roster/snapshot therefore cannot erase personal grouping choices.
    """

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "doll_categories.json"

    @staticmethod
    def _clean_name(value: str) -> str:
        return " ".join(str(value or "").strip().split())[:MAX_CATEGORY_NAME]

    @staticmethod
    def _clean_keys(values: Iterable[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in values:
            key = str(raw or "").strip()[:96]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) >= MAX_DOLLS_PER_CATEGORY:
                break
        return out

    def load(self) -> dict[str, list[str]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        rows = raw.get("categories") if isinstance(raw, dict) else None
        if not isinstance(rows, dict):
            return {}
        result: dict[str, list[str]] = {}
        for name, keys in list(rows.items())[:MAX_DOLL_CATEGORIES]:
            clean = self._clean_name(str(name))
            if not clean or not isinstance(keys, list):
                continue
            cleaned = self._clean_keys(keys)
            if cleaned:
                result[clean] = cleaned
        return result

    def save(self, categories: dict[str, Iterable[str]]) -> Path:
        cleaned: dict[str, list[str]] = {}
        for raw_name, raw_keys in categories.items():
            name = self._clean_name(raw_name)
            if not name:
                continue
            keys = self._clean_keys(raw_keys)
            if keys:
                cleaned[name] = keys
            if len(cleaned) >= MAX_DOLL_CATEGORIES:
                break
        return atomic_write_json(
            self.path,
            {"schema": 1, "categories": cleaned},
            ensure_ascii=False,
            indent=2,
        )

    def names(self) -> list[str]:
        return sorted(self.load(), key=str.casefold)

    def keys(self, category: str) -> set[str]:
        name = self._clean_name(category)
        return set(self.load().get(name, [])) if name else set()

    def assign(self, category: str, character_keys: Iterable[str]) -> None:
        name = self._clean_name(category)
        if not name:
            raise ValueError("카테고리 이름을 입력해 주세요.")
        categories = self.load()
        if name not in categories and len(categories) >= MAX_DOLL_CATEGORIES:
            raise ValueError(f"인형 카테고리는 최대 {MAX_DOLL_CATEGORIES}개까지 만들 수 있습니다.")
        merged = self._clean_keys([*categories.get(name, []), *character_keys])
        categories[name] = merged
        self.save(categories)

    def remove(self, category: str, character_keys: Iterable[str]) -> None:
        name = self._clean_name(category)
        categories = self.load()
        if name not in categories:
            return
        removed = {str(key or "").strip() for key in character_keys}
        remaining = [key for key in categories[name] if key not in removed]
        if remaining:
            categories[name] = remaining
        else:
            categories.pop(name, None)
        self.save(categories)

    def delete(self, category: str) -> None:
        name = self._clean_name(category)
        categories = self.load()
        if categories.pop(name, None) is not None:
            self.save(categories)

    def categories_for(self, character_key: str) -> list[str]:
        key = str(character_key or "").strip()
        if not key:
            return []
        return sorted(
            [name for name, keys in self.load().items() if key in keys],
            key=str.casefold,
        )
