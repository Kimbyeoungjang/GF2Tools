from __future__ import annotations

import base64
import json
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .atomic_io import atomic_write_json

TACTIC_SCHEMA = 1
SHARE_PREFIX = "GFL2T:1:"
MAX_GRID_EDGE = 24
MIN_GRID_EDGE = 4
MAX_STEPS = 64
MAX_MARKERS_PER_STEP = 2048
MAX_SHARE_BYTES = 1_000_000
MAX_TACTICS = 512
MAX_TACTIC_UNITS = 32


def _new_id() -> str:
    return uuid4().hex



def _clean_str_list(value: Any, *, limit: int = 3, item_limit: int = 80) -> list[str]:
    rows = value if isinstance(value, list) else []
    out: list[str] = []
    for item in rows:
        text = str(item or "").strip()[:item_limit]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out




def _clean_skill_cycle(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else []
    out = [str(item or "").strip()[:240] for item in rows[:64]]
    while out and not out[-1]:
        out.pop()
    return out

def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


@dataclass
class TacticMarker:
    kind: str = "unit"
    row: int = 0
    col: int = 0
    label: str = ""
    width: int = 1
    height: int = 1
    to_row: int | None = None
    to_col: int | None = None
    edges: str = ""
    unit_key: str = ""
    caption: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, rows: int, cols: int) -> "TacticMarker | None":
        kind = str(raw.get("kind") or "unit")
        if kind not in {"unit", "summon", "custom", "boss", "arrow", "blocked", "cover"}:
            return None
        row = _bounded_int(raw.get("row"), 0, rows - 1, 0)
        col = _bounded_int(raw.get("col"), 0, cols - 1, 0)
        width = _bounded_int(raw.get("width"), 1, cols, 1)
        height = _bounded_int(raw.get("height"), 1, rows, 1)
        width = min(width, cols - col)
        height = min(height, rows - row)
        label = str(raw.get("label") or "")[:24]
        caption = str(raw.get("caption") or "")[:24]
        to_row: int | None = None
        to_col: int | None = None
        if kind == "arrow":
            to_row = _bounded_int(raw.get("to_row"), 0, rows - 1, row)
            to_col = _bounded_int(raw.get("to_col"), 0, cols - 1, col)
        edges = ""
        if kind == "cover":
            edges = "".join(edge for edge in "NESW" if edge in str(raw.get("edges") or "").upper())
            if not edges:
                return None
        return cls(
            kind=kind,
            row=row,
            col=col,
            label=label,
            width=width,
            height=height,
            to_row=to_row,
            to_col=to_col,
            edges=edges,
            unit_key=str(raw.get("unit_key") or "")[:64],
            caption=caption,
        )


@dataclass
class TacticUnit:
    unit_key: str = field(default_factory=_new_id)
    doll_id: int | None = None
    name: str = ""
    alias: str = ""
    rank: int = 0
    weapon: str = ""
    common_keys: list[str] = field(default_factory=list)
    unique_keys: list[str] = field(default_factory=list)
    expansion_level: int = 0
    skill_cycle: list[str] = field(default_factory=list)
    skill_cycle_source: str = ""
    formation_plan_id: int | None = None
    formation_position: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TacticUnit":
        key = str(raw.get("unit_key") or "").strip()[:64] or _new_id()
        doll_id = None
        try:
            if raw.get("doll_id") is not None:
                doll_id = int(raw.get("doll_id"))
        except (TypeError, ValueError):
            doll_id = None

        common_keys = _clean_str_list(raw.get("common_keys"))
        unique_keys = _clean_str_list(raw.get("unique_keys"))
        expansion_level = _bounded_int(raw.get("expansion_level"), 0, 2, 0)
        skill_cycle = _clean_skill_cycle(raw.get("skill_cycle"))
        formation_plan_id = None
        try:
            if raw.get("formation_plan_id") not in (None, ""):
                formation_plan_id = int(raw.get("formation_plan_id"))
        except (TypeError, ValueError):
            formation_plan_id = None

        return cls(
            unit_key=key,
            doll_id=doll_id,
            name=str(raw.get("name") or "")[:80],
            alias=str(raw.get("alias") or "")[:12],
            rank=_bounded_int(raw.get("rank"), 0, 6, 0),
            weapon=str(raw.get("weapon") or "")[:80],
            common_keys=common_keys,
            unique_keys=unique_keys,
            expansion_level=expansion_level,
            skill_cycle=skill_cycle,
            skill_cycle_source=str(raw.get("skill_cycle_source") or "")[:120],
            formation_plan_id=formation_plan_id,
            formation_position=_bounded_int(raw.get("formation_position"), 0, 6, 0),
        )

    def display_label(self) -> str:
        if self.alias.strip():
            return self.alias.strip()[:12]
        text = self.name.strip()
        if not text:
            return "?"
        tokens = [part for part in text.replace("-", " ").split() if part]
        if len(tokens) > 1:
            return "".join(token[0] for token in tokens if token)[:4]
        return text[0][:1]

    def expansion_label(self, *, include_prefix: bool = True) -> str:
        level = max(0, min(2, int(self.expansion_level)))
        if include_prefix:
            return "도약키 미장착" if level == 0 else f"도약키 {level}단계"
        return "미장착" if level == 0 else f"{level}단계"


@dataclass
class TacticStep:
    name: str = "T1"
    note: str = ""
    cycle: str = ""
    cycle_auto: bool = False
    markers: list[TacticMarker] = field(default_factory=list)
    rows: int | None = None
    cols: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, rows: int, cols: int, index: int) -> "TacticStep":
        step_rows = _bounded_int(raw.get("rows"), MIN_GRID_EDGE, MAX_GRID_EDGE, rows)
        step_cols = _bounded_int(raw.get("cols"), MIN_GRID_EDGE, MAX_GRID_EDGE, cols)
        markers: list[TacticMarker] = []
        marker_rows = raw.get("markers")
        if not isinstance(marker_rows, list):
            marker_rows = []
        for item in marker_rows[:MAX_MARKERS_PER_STEP]:
            if not isinstance(item, dict):
                continue
            marker = TacticMarker.from_dict(item, rows=step_rows, cols=step_cols)
            if marker is not None:
                markers.append(marker)
        return cls(
            name=str(raw.get("name") or f"T{index + 1}")[:32],
            note=str(raw.get("note") or "")[:4000],
            cycle=str(raw.get("cycle") or "")[:2000],
            cycle_auto=bool(raw.get("cycle_auto", False)),
            markers=markers,
            rows=step_rows,
            cols=step_cols,
        )


@dataclass
class Tactic:
    tactic_id: str = field(default_factory=_new_id)
    title: str = "새 택틱"
    category: str = ""
    rows: int = 12
    cols: int = 12
    steps: list[TacticStep] = field(default_factory=lambda: [TacticStep()])
    units: list[TacticUnit] = field(default_factory=list)
    show_previous: bool = False
    schema: int = TACTIC_SCHEMA

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_id:
            payload.pop("tactic_id", None)
        return payload

    def grid_size(self, step_index: int) -> tuple[int, int]:
        if not self.steps:
            return self.rows, self.cols
        index = max(0, min(int(step_index), len(self.steps) - 1))
        step = self.steps[index]
        return int(step.rows or self.rows), int(step.cols or self.cols)

    def unit_by_key(self, unit_key: str) -> TacticUnit | None:
        key = str(unit_key or "")
        return next((unit for unit in self.units if unit.unit_key == key), None)

    def marker_label(self, marker: TacticMarker) -> str:
        if marker.kind != "unit":
            return marker.label
        unit = self.unit_by_key(marker.unit_key) if marker.unit_key else None
        return unit.display_label() if unit is not None else (marker.label or "?")

    def clone(self, *, title_suffix: str = " 복사") -> "Tactic":
        payload = self.to_dict(include_id=False)
        payload["title"] = f"{self.title}{title_suffix}"[:100]
        return Tactic.from_dict(payload, preserve_id=False)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, preserve_id: bool = True) -> "Tactic":
        try:
            schema = int(raw.get("schema"))
        except (TypeError, ValueError):
            schema = -1
        if schema != TACTIC_SCHEMA:
            raise ValueError(f"지원하지 않는 택틱 형식입니다: schema={schema}")
        rows = _bounded_int(raw.get("rows"), MIN_GRID_EDGE, MAX_GRID_EDGE, 12)
        cols = _bounded_int(raw.get("cols"), MIN_GRID_EDGE, MAX_GRID_EDGE, 12)
        steps: list[TacticStep] = []
        step_rows = raw.get("steps")
        if not isinstance(step_rows, list):
            step_rows = []
        for index, item in enumerate(step_rows[:MAX_STEPS]):
            if isinstance(item, dict):
                steps.append(TacticStep.from_dict(item, rows=rows, cols=cols, index=index))
        if not steps:
            steps = [TacticStep()]
        tactic_id = str(raw.get("tactic_id") or "").strip() if preserve_id else ""
        if not tactic_id or len(tactic_id) > 64:
            tactic_id = _new_id()
        units_raw = raw.get("units") if isinstance(raw.get("units"), list) else []
        units: list[TacticUnit] = []
        seen_unit_keys: set[str] = set()
        for item in units_raw[:MAX_TACTIC_UNITS]:
            if not isinstance(item, dict):
                continue
            unit = TacticUnit.from_dict(item)
            if unit.unit_key in seen_unit_keys:
                unit.unit_key = _new_id()
            seen_unit_keys.add(unit.unit_key)
            units.append(unit)
        return cls(
            tactic_id=tactic_id,
            title=str(raw.get("title") or "새 택틱")[:100],
            category=str(raw.get("category") or "")[:80],
            rows=rows,
            cols=cols,
            steps=steps,
            units=units,
            show_previous=bool(raw.get("show_previous", False)),
            schema=TACTIC_SCHEMA,
        )


def encode_tactic_share(tactic: Tactic) -> str:
    raw = json.dumps(
        tactic.to_dict(include_id=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > MAX_SHARE_BYTES:
        raise ValueError("택틱 공유 데이터가 허용 크기를 초과합니다.")
    compressed = zlib.compress(raw, level=9)
    token = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    return SHARE_PREFIX + token


def decode_tactic_share(code: str) -> Tactic:
    text = str(code or "").strip()
    if not text.startswith(SHARE_PREFIX):
        raise ValueError("지원하지 않는 택틱 공유 코드입니다.")
    token = text[len(SHARE_PREFIX):]
    if len(token) > MAX_SHARE_BYTES * 2:
        raise ValueError("택틱 공유 코드가 허용 크기를 초과합니다.")
    token += "=" * (-len(token) % 4)
    try:
        compressed = base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_SHARE_BYTES + 1)
        remaining = MAX_SHARE_BYTES + 1 - len(raw)
        if remaining > 0:
            raw += decoder.flush(remaining)
    except (ValueError, zlib.error) as exc:
        raise ValueError("택틱 공유 코드를 해석하지 못했습니다.") from exc
    if len(raw) > MAX_SHARE_BYTES or decoder.unconsumed_tail:
        raise ValueError("택틱 공유 데이터가 허용 크기를 초과합니다.")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("택틱 공유 데이터가 완전한 단일 압축 스트림이 아닙니다.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("택틱 공유 데이터의 JSON 형식이 올바르지 않습니다.") from exc
    if not isinstance(payload, dict):
        raise ValueError("택틱 공유 데이터 형식이 올바르지 않습니다.")
    return Tactic.from_dict(payload, preserve_id=False)


class TacticStore:
    """Small local JSON store kept next to the application's SQLite data."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir) / "tactics"
        self.library_path = self.root / "library.json"
        self.overlay_state_path = self.root / "overlay_state.json"

    def load(self) -> list[Tactic]:
        if not self.library_path.is_file():
            return []
        try:
            payload = json.loads(self.library_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"택틱 라이브러리를 읽지 못했습니다: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != TACTIC_SCHEMA:
            raise RuntimeError("현재 버전에서 생성한 택틱 라이브러리만 사용할 수 있습니다.")
        rows = payload.get("tactics")
        if not isinstance(rows, list):
            raise RuntimeError("택틱 라이브러리 형식이 올바르지 않습니다.")
        out: list[Tactic] = []
        seen: set[str] = set()
        for item in rows[:MAX_TACTICS]:
            if not isinstance(item, dict):
                continue
            tactic = Tactic.from_dict(item, preserve_id=True)
            if tactic.tactic_id in seen:
                tactic.tactic_id = _new_id()
            seen.add(tactic.tactic_id)
            out.append(tactic)
        return out

    def save(self, tactics: list[Tactic]) -> Path:
        if len(tactics) > MAX_TACTICS:
            raise ValueError(f"택틱은 최대 {MAX_TACTICS}개까지 저장할 수 있습니다.")
        for tactic in tactics:
            if len(tactic.units) > MAX_TACTIC_UNITS:
                raise ValueError(f"택틱 사용 인형은 최대 {MAX_TACTIC_UNITS}명까지 저장할 수 있습니다.")
            if len(tactic.steps) > MAX_STEPS:
                raise ValueError(f"택틱 단계는 최대 {MAX_STEPS}개까지 저장할 수 있습니다.")
            if any(len(step.markers) > MAX_MARKERS_PER_STEP for step in tactic.steps):
                raise ValueError(f"단계별 요소는 최대 {MAX_MARKERS_PER_STEP}개까지 저장할 수 있습니다.")
        payload = {
            "schema": TACTIC_SCHEMA,
            "tactics": [tactic.to_dict(include_id=True) for tactic in tactics],
        }
        return atomic_write_json(self.library_path, payload, ensure_ascii=False, indent=2)

    def load_overlay_states(self) -> dict[str, dict[str, Any]]:
        if not self.overlay_state_path.is_file():
            return {}
        try:
            payload = json.loads(self.overlay_state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema") != TACTIC_SCHEMA:
            return {}
        states = payload.get("states")
        if not isinstance(states, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in states.items()
            if isinstance(value, dict)
        }

    def save_overlay_states(self, states: dict[str, dict[str, Any]]) -> Path:
        payload = {"schema": TACTIC_SCHEMA, "states": states}
        return atomic_write_json(self.overlay_state_path, payload, ensure_ascii=False, indent=2)
