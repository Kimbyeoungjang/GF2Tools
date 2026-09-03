from __future__ import annotations

import json
from typing import Any

from .. import reference
from ..repository import Repository, utc_now


class FormationService:
    MAX_MEMBERS = 6

    def __init__(self, repo: Repository):
        self.repo = repo

    def _insert_plan_uncommitted(self, name: str, notes: str = "") -> int:
        name = name.strip()
        if not name:
            raise ValueError("제대 이름을 입력하세요.")
        if self.repo.con.execute("SELECT 1 FROM formation_plans WHERE name=?", (name,)).fetchone():
            raise ValueError("같은 이름의 제대가 이미 있습니다.")
        now = utc_now()
        cur = self.repo.con.execute(
            "INSERT INTO formation_plans(name,notes,created_at,updated_at) VALUES(?,?,?,?)",
            (name, notes.strip(), now, now),
        )
        return int(cur.lastrowid)

    def create(self, name: str, notes: str = "") -> int:
        with self.repo.transaction():
            return self._insert_plan_uncommitted(name, notes)

    def list(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.repo.con.execute(
                """SELECT p.*, COUNT(m.position) AS member_count
                   FROM formation_plans AS p
                   LEFT JOIN formation_members AS m ON m.plan_id=p.id
                   GROUP BY p.id
                   ORDER BY p.updated_at DESC,p.name"""
            )
        ]

    def delete(self, plan_id: int) -> None:
        with self.repo.transaction():
            self.repo.con.execute("DELETE FROM formation_plans WHERE id=?", (plan_id,))

    def rename(self, plan_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("제대 이름을 입력하세요.")
        if self.repo.con.execute("SELECT 1 FROM formation_plans WHERE name=? AND id<>?", (name, plan_id)).fetchone():
            raise ValueError("같은 이름의 제대가 이미 있습니다.")
        with self.repo.transaction():
            self.repo.con.execute(
                "UPDATE formation_plans SET name=?,updated_at=? WHERE id=?",
                (name, utc_now(), plan_id),
            )

    def get(self, plan_id: int) -> dict[str, Any]:
        plan = self.repo.con.execute("SELECT * FROM formation_plans WHERE id=?", (plan_id,)).fetchone()
        if not plan:
            raise ValueError("제대 계획을 찾을 수 없습니다.")
        members: list[dict[str, Any]] = []
        for row in self.repo.con.execute(
            "SELECT * FROM formation_members WHERE plan_id=? ORDER BY position", (plan_id,)
        ):
            raw = dict(row)
            members.append({
                "plan_id": int(raw["plan_id"]),
                "position": int(raw["position"]),
                "doll_id": int(raw["doll_id"]),
                "doll_name": raw.get("doll_name"),
                "remolding_uids": json.loads(raw.get("remolding_uids_json") or "[]"),
                "remolding_targets": json.loads(raw.get("remolding_targets_json") or "{}"),
            })
        return {**dict(plan), "members": members}

    def _validate_inventory_resources(self, remolding_uids: list[str]) -> None:
        for uid in remolding_uids:
            if not self.repo.exists("remoldings", "uid", uid):
                raise ValueError(f"보유하지 않은 리몰딩입니다: {uid}")

    def _validate_plan_uniqueness(
        self,
        plan_id: int,
        position: int,
        doll_id: int,
        remolding_uids: list[str],
    ) -> None:
        duplicate = self.repo.con.execute(
            "SELECT position FROM formation_members WHERE plan_id=? AND doll_id=? AND position<>?",
            (plan_id, doll_id, position),
        ).fetchone()
        if duplicate:
            raise ValueError(f"이 인형은 이미 {duplicate['position']}번 슬롯에 배치되어 있습니다.")

        remoldings = set(remolding_uids)
        if len(remoldings) != len(remolding_uids):
            raise ValueError("한 슬롯에 같은 리몰딩을 중복 배치할 수 없습니다.")
        for row in self.repo.con.execute(
            "SELECT position,remolding_uids_json FROM formation_members "
            "WHERE plan_id=? AND position<>?",
            (plan_id, position),
        ):
            other_pos = int(row["position"])
            if remoldings & set(json.loads(row["remolding_uids_json"] or "[]")):
                raise ValueError(f"같은 리몰딩이 {other_pos}번 슬롯에서 이미 사용 중입니다.")

    def _set_member_uncommitted(
        self,
        plan_id: int,
        position: int,
        doll_id: int,
        *,
        remolding_uids: list[str] | None = None,
        remolding_targets: dict[str, Any] | None = None,
    ) -> None:
        if position < 1 or position > self.MAX_MEMBERS:
            raise ValueError(f"제대 슬롯은 1~{self.MAX_MEMBERS} 범위입니다.")
        if not self.repo.con.execute("SELECT 1 FROM formation_plans WHERE id=?", (plan_id,)).fetchone():
            raise ValueError("제대 계획을 찾을 수 없습니다.")

        doll_row = self.repo.con.execute("SELECT name FROM dolls WHERE doll_id=?", (doll_id,)).fetchone()
        if doll_row is None:
            raise ValueError("선택한 인형이 현재 보유 데이터에 없습니다.")

        remolding_uids = [str(x) for x in (remolding_uids or [])]
        remolding_targets = self._normalize_remolding_targets(remolding_targets or {})
        self._validate_inventory_resources(remolding_uids)
        self._validate_plan_uniqueness(plan_id, position, doll_id, remolding_uids)

        display_name = (doll_row["name"] if doll_row["name"] else None) or reference.dolls().get(doll_id)
        self.repo.con.execute(
            """INSERT OR REPLACE INTO formation_members
               (plan_id,position,doll_id,doll_name,remolding_uids_json,remolding_targets_json)
               VALUES(?,?,?,?,?,?)""",
            (
                plan_id,
                position,
                doll_id,
                display_name,
                json.dumps(remolding_uids, ensure_ascii=False),
                json.dumps(remolding_targets, ensure_ascii=False),
            ),
        )
        self.repo.con.execute("UPDATE formation_plans SET updated_at=? WHERE id=?", (utc_now(), plan_id))

    def set_member(
        self,
        plan_id: int,
        position: int,
        doll_id: int,
        *,
        remolding_uids: list[str] | None = None,
        remolding_targets: dict[str, Any] | None = None,
    ) -> None:
        with self.repo.transaction():
            self._set_member_uncommitted(
                plan_id, position, doll_id,
                remolding_uids=remolding_uids,
                remolding_targets=remolding_targets,
            )


    def _normalize_remolding_targets(self, targets: dict[str, Any]) -> dict[str, dict[str, int]]:
        from .remolding_recommendation import RemoldingRecommendationService
        options = reference.remolding_options()
        normalized: dict[str, dict[str, int]] = {}
        for key, value in (targets or {}).items():
            option = options.get(str(key))
            if not option:
                continue
            spec = RemoldingRecommendationService.normalize_target_spec(value, int(option.get("maxLevel") or 0), int(option.get("weight") or 100))
            if spec:
                normalized[str(key)] = spec
        return normalized

    @staticmethod
    def _merge_optimized_members(
        plan: dict[str, Any],
        optimized_members: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        final_members: list[dict[str, Any]] = []
        for member in plan["members"]:
            pos = int(member["position"])
            suggestion = optimized_members.get(pos) or optimized_members.get(str(pos))
            if suggestion is None:
                raise ValueError(f"{pos}번 슬롯의 자동 배치 결과가 없습니다. 다시 계산하세요.")
            row = dict(member)
            row["remolding_uids"] = [str(x) for x in suggestion.get("remolding_uids", [])]
            final_members.append(row)
        return final_members

    def _validate_final_remolding_members(self, final_members: list[dict[str, Any]]) -> None:
        doll_seen: dict[int, int] = {}
        remolding_seen: dict[str, int] = {}
        for row in final_members:
            pos = int(row["position"])
            doll_id = int(row["doll_id"])
            if doll_id in doll_seen:
                raise ValueError(
                    f"같은 인형이 {doll_seen[doll_id]}번과 {pos}번 슬롯에 중복 배치되어 있습니다."
                )
            doll_seen[doll_id] = pos

            remoldings = [str(x) for x in row.get("remolding_uids", [])]
            self._validate_inventory_resources(remoldings)
            if len(remoldings) != len(set(remoldings)):
                raise ValueError(f"{pos}번 슬롯의 리몰딩 목록에 같은 항목이 중복되어 있습니다.")
            for uid in remoldings:
                previous = remolding_seen.get(uid)
                if previous is not None:
                    raise ValueError(
                        f"같은 리몰딩이 {previous}번과 {pos}번 슬롯에서 중복 사용됩니다."
                    )
                remolding_seen[uid] = pos

    def _write_remolding_members(
        self,
        plan_id: int,
        final_members: list[dict[str, Any]],
    ) -> None:
        with self.repo.transaction():
            for row in final_members:
                self.repo.con.execute(
                    """UPDATE formation_members
                       SET remolding_uids_json=?
                       WHERE plan_id=? AND position=?""",
                    (
                        json.dumps([str(x) for x in row.get("remolding_uids", [])], ensure_ascii=False),
                        plan_id,
                        int(row["position"]),
                    ),
                )
            self.repo.con.execute(
                "UPDATE formation_plans SET updated_at=? WHERE id=?",
                (utc_now(), plan_id),
            )

    def apply_remolding_plan(
        self,
        plan_id: int,
        optimized_members: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically apply a remolding optimizer preview to one formation."""
        plan = self.get(plan_id)
        if not plan["members"]:
            raise ValueError("적용할 제대 인형이 없습니다.")

        final_members = self._merge_optimized_members(plan, optimized_members)
        self._validate_final_remolding_members(final_members)
        self._write_remolding_members(plan_id, final_members)
        return self.get(plan_id)

    def remove_member(self, plan_id: int, position: int) -> None:
        with self.repo.transaction():
            self.repo.con.execute(
                "DELETE FROM formation_members WHERE plan_id=? AND position=?", (plan_id, position)
            )
            self.repo.con.execute("UPDATE formation_plans SET updated_at=? WHERE id=?", (utc_now(), plan_id))

    def list_game_formations(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        doll_names = {
            int(row["doll_id"]): str(row["name"])
            for row in self.repo.con.execute("SELECT doll_id,name FROM dolls WHERE name IS NOT NULL AND name<>''")
        }
        fallback_names = reference.dolls()
        for row in self.repo.con.execute("SELECT * FROM game_formations ORDER BY id"):
            members = json.loads(row["members_json"])
            names: list[str] = []
            for member in members:
                did = int(member.get("doll_id", 0) or 0)
                if did <= 0:
                    continue
                name = doll_names.get(did) or member.get("doll_name") or fallback_names.get(did) or str(did)
                names.append(str(name))
            out.append({**dict(row), "members": members, "member_names": names})
        return out

    def import_game_formation(self, game_formation_id: int, new_name: str | None = None) -> int:
        row = self.repo.con.execute("SELECT * FROM game_formations WHERE id=?", (game_formation_id,)).fetchone()
        if not row:
            raise ValueError("캡처된 게임 제대를 찾을 수 없습니다.")
        base_name = str(row["name"] or "게임 제대")
        name = (new_name or f"{base_name} 복사본").strip()
        candidate = name
        suffix = 2
        while self.repo.con.execute("SELECT 1 FROM formation_plans WHERE name=?", (candidate,)).fetchone():
            candidate = f"{name} ({suffix})"
            suffix += 1

        members = json.loads(row["members_json"])
        with self.repo.transaction():
            plan_id = self._insert_plan_uncommitted(candidate)
            for pos, member in enumerate(members, 1):
                if pos > self.MAX_MEMBERS:
                    break
                doll_id = int(member.get("doll_id", 0) or 0)
                if doll_id <= 0:
                    continue
                self._set_member_uncommitted(plan_id, pos, doll_id)
        return plan_id
