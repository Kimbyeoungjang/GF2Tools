from __future__ import annotations

import re
from typing import Any

from .. import reference
from ..repository import Repository
from .remolding_recommendation import RemoldingRecommendationService


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


# Some remodel/upgrade forms share one ownership record in the imported account
# data while the planner must expose both playable forms as distinct Dolls.  Keep
# the relationship ID-based so display-name changes/localization cannot collapse
# the two identities again.
_LINKED_OWNERSHIP_GROUPS: tuple[tuple[int, ...], ...] = (
    (1008, 1075),  # 네메시스 / 네메시스·연광
)


def expand_linked_owned_doll_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return presentation rows with linked upgrade forms mirrored as owned.

    The repository remains faithful to the imported account payload (which may
    contain only one member of a linked group).  Presentation/planning layers get
    a synthetic row for the missing form so both IDs remain independently
    selectable without mutating or duplicating the user's stored ownership data.
    """

    actual = {
        int(row.get("doll_id") or 0): dict(row)
        for row in rows
        if int(row.get("doll_id") or 0) > 0
    }
    expanded = [dict(row) for row in rows]
    program = reference.program_dolls()
    for group in _LINKED_OWNERSHIP_GROUPS:
        owned = [did for did in group if did in actual]
        if not owned:
            continue
        source_id = owned[0]
        source = actual[source_id]
        for did in group:
            if did in actual:
                continue
            mirrored = dict(source)
            mirrored["doll_id"] = int(did)
            mirrored["name"] = str(
                (program.get(int(did)) or {}).get("name_ko")
                or reference.bundled_dolls().get(int(did))
                or f"인형 {did}"
            )
            # A stored illustration path belongs to the source Doll.  Clearing it
            # lets the normal REST-asset resolver select the target form's image.
            mirrored["illustration_path"] = None
            mirrored["_ownership_source_doll_id"] = int(source_id)
            mirrored["_linked_ownership"] = True
            expanded.append(mirrored)
    return expanded


class DollCharacterResolver:
    """Lightweight doll -> 리몰딩 추천 identity/level resolver.

    Character-card pages only need doll identity, favorites and imported levels.
    This resolver keeps that identity/level mapping independent from the heavier
    formation-wide remolding allocator, so roster presentation does not construct
    optimization state merely to resolve character metadata.

    ``master`` is optional and lazy by default.  Known dolls are resolved from
    bundled aliases/current imported names first, so normal roster refreshes do
    not parse ``runtime_master.json`` merely to draw cards.
    """

    def __init__(
        self,
        repo: Repository,
        *,
        recommendation: RemoldingRecommendationService | None = None,
        owned_doll_rows: dict[int, dict[str, Any]] | None = None,
        master: dict[str, Any] | None = None,
        master_loaded: bool | None = None,
    ):
        self.repo = repo
        self.recommendation = recommendation or RemoldingRecommendationService(repo)
        if owned_doll_rows is None:
            owned_doll_rows = {
                int(row["doll_id"]): dict(row)
                for row in repo.con.execute("SELECT doll_id,name,level,favorite FROM dolls")
            }
        self.owned_doll_rows = owned_doll_rows
        self._master = master or {}
        self._master_loaded = bool(master is not None) if master_loaded is None else bool(master_loaded)

        # Dummy calculator profiles must never hijack real doll-ID/name mapping.
        # Building this index only needs identity/display-name fields. Calling
        # get_character() for every roster entry deep-copied 60 character records
        # and expanded imagoform metadata just to obtain those two strings. Use the
        # already-loaded profile dictionaries directly instead.
        chars = [
            (str(key), self.recommendation.characters.get(key) or base)
            for key, base in self.recommendation.base_characters.items()
        ]
        self._recommendation_by_norm = {_norm(key): key for key, _char in chars}
        self._recommendation_by_ko: dict[str, str] = {}
        for key, char in chars:
            # Recommendation data and API display data are sourced from different
            # tables.  The visible Korean name can therefore differ (e.g. 토로롱
            # vs 토로로, 벨카 vs 비욜카).  phenomenonSourceName is the stable
            # bridge supplied by the remolding rules for those cases.
            for alias in (char.get("nameKR"), char.get("phenomenonSourceName")):
                text = str(alias or "").strip()
                if text:
                    self._recommendation_by_ko.setdefault(text, key)
        self._character_key_by_doll_id: dict[int, str | None] = {}
        self._favorite_character_key_cache: frozenset[str] | None = None
        self._character_level_cache: dict[str, int] | None = None

    def _match_name(self, value: str | None) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        if text in self._recommendation_by_ko:
            return self._recommendation_by_ko[text]
        normalized = _norm(text)
        if normalized in self._recommendation_by_norm:
            return self._recommendation_by_norm[normalized]
        for suffix in ("ssr", "sr", "r"):
            if normalized.endswith(suffix) and normalized[:-len(suffix)] in self._recommendation_by_norm:
                return self._recommendation_by_norm[normalized[:-len(suffix)]]
        return None

    def character_key_for_doll(self, doll_id: int) -> str | None:
        did = int(doll_id)
        if did in self._character_key_by_doll_id:
            return self._character_key_by_doll_id[did]

        # Fast/common path: bundled aliases and imported display names cover the
        # known roster.  Only unresolved/new dolls touch runtime_master.json.
        program_meta = reference.program_dolls().get(did, {})
        candidates = (
            reference.bundled_doll_display_names().get(did),
            reference.bundled_dolls().get(did),
            program_meta.get("name_ko"),
            program_meta.get("resource_name"),
            (self.owned_doll_rows.get(did) or {}).get("name"),
        )
        hit = None
        for candidate in candidates:
            hit = self._match_name(str(candidate or ""))
            if hit:
                break
        self._character_key_by_doll_id[did] = hit
        return hit


    def favorite_character_keys(self) -> set[str]:
        if self._favorite_character_key_cache is not None:
            return set(self._favorite_character_key_cache)
        keys: set[str] = set()
        for did, row in self.owned_doll_rows.items():
            if not bool(int(row.get("favorite") or 0)):
                continue
            key = self.character_key_for_doll(did)
            if key:
                keys.add(str(key))
        self._favorite_character_key_cache = frozenset(keys)
        return set(keys)

    def character_level_override_for_key(self, character_key: str) -> int | None:
        """Return the user-set calculation level override, if one exists."""
        key = str(character_key)
        try:
            char = self.recommendation.get_character(key)
            override = int(char.get("levelOverride") or 0)
        except (TypeError, ValueError):
            override = 0
        if override > 0:
            return max(1, min(60, override))
        return None

    def owned_character_level_for_key(self, character_key: str) -> int:
        """Resolve the imported/actual doll level without calculation overrides."""
        key = str(character_key)
        if key.startswith("dummy_"):
            return 60
        if self._character_level_cache is None:
            cache: dict[str, int] = {}
            for did, row in self.owned_doll_rows.items():
                try:
                    mapped = self.character_key_for_doll(did)
                    level = int(row.get("level") or 0)
                except (TypeError, ValueError, KeyError):
                    continue
                if mapped and level > 0:
                    cache[mapped] = max(cache.get(mapped, 0), max(1, min(60, level)))
            self._character_level_cache = cache
        return int(self._character_level_cache.get(key, 60))

    def calculation_level_for_key(
        self,
        character_key: str,
        global_override: int | None = 60,
    ) -> int:
        """Resolve remolding calculation level as individual override > global level.

        ``dolls.level`` is the Doll's actual character level and is intentionally
        not used as a remolding calculation fallback.  Remolding calculations use
        Lv.60 by default so imported roster data cannot silently change optimizer
        targets.
        """
        key = str(character_key)
        individual = self.character_level_override_for_key(key)
        if individual is not None:
            return individual
        try:
            level = 60 if global_override is None else int(global_override)
        except (TypeError, ValueError):
            level = 60
        return max(0, min(60, level))

    def character_level_for_key(self, character_key: str) -> int:
        """Return the Doll's imported/actual character level."""
        return self.owned_character_level_for_key(character_key)
