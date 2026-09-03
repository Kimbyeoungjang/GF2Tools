from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Doll:
    doll_id: int
    name: str | None
    level: int
    rank: int
    illustration_path: str | None = None


@dataclass(slots=True)
class RemoldingSlot:
    code: str
    name: str | None
    option_key: str | None = None
    variant: int | None = None
    factor_type: str | None = None
    element_type: str | None = None
    level_contribution: int | None = None


@dataclass(slots=True)
class Remolding:
    uid: str
    remolding_id: int
    raw_contents_hex: str
    slots: list[RemoldingSlot] = field(default_factory=list)


@dataclass(slots=True)
class FormationMember:
    doll_id: int
    doll_name: str | None


@dataclass(slots=True)
class GameFormation:
    name: str
    members: list[FormationMember] = field(default_factory=list)

