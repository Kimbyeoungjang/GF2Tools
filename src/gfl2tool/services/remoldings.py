from __future__ import annotations

import json
from typing import Any

from .. import reference
from ..repository import Repository, utc_now


class RemoldingPatternService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def create(self, name: str, doll_id: int | None = None, notes: str = "", character_key: str | None = None) -> int:
        now = utc_now()
        doll_name = reference.dolls().get(doll_id) if doll_id is not None else None
        if character_key:
            character = reference.remolding_characters().get(character_key)
            if not character:
                raise ValueError(f"unknown 리몰딩 추천 character: {character_key}")
            doll_name = character["nameKR"]
        cur = self.repo.con.execute(
            "INSERT INTO remolding_patterns(name,doll_id,doll_name,character_key,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (name,doll_id,doll_name,character_key,notes,now,now),
        )
        self.repo.con.commit()
        return int(cur.lastrowid)

    def list(self) -> list[dict[str, Any]]:
        return self.repo.rows("remolding_patterns", order_by="name")

    def get(self, pattern_id: int) -> dict[str, Any]:
        p = self.repo.con.execute("SELECT * FROM remolding_patterns WHERE id=?", (pattern_id,)).fetchone()
        if not p:
            raise ValueError("remolding pattern not found")
        slots = [dict(r) for r in self.repo.con.execute(
            "SELECT * FROM remolding_pattern_slots WHERE pattern_id=? ORDER BY slot_index", (pattern_id,)
        )]
        return {**dict(p), "slots": slots}

    def delete(self, pattern_id: int) -> None:
        self.repo.con.execute("DELETE FROM remolding_patterns WHERE id=?", (pattern_id,))
        self.repo.con.commit()

    def set_slot(
        self,
        pattern_id: int,
        slot_index: int,
        *,
        code: str | None = None,
        name: str | None = None,
        option_key: str | None = None,
        source_uid: str | None = None,
    ) -> None:
        if slot_index < 1:
            raise ValueError("slot_index must be >= 1")
        code_index = reference.remolding_code_index()
        options = reference.remolding_options()

        resolved_option_key: str | None = option_key
        resolved_name: str | None = name
        if option_key:
            option = options.get(option_key)
            if not option:
                raise ValueError(f"unknown remolding option: {option_key}")
            code = str(option["codes"][0]).lower()
            resolved_name = option["nameKR"]
        elif code:
            code = code.lower().strip()
            meta = code_index.get(code)
            if not meta:
                raise ValueError(f"unknown remolding code: {code}")
            resolved_option_key = meta["option_key"]
            resolved_name = reference.remoldings().get(code)
        elif name:
            # Accept the logical option name or the concrete code-variant display name.
            logical = next((o for o in options.values() if o["nameKR"] == name), None)
            if logical:
                resolved_option_key = logical["key"]
                code = logical["codes"][0]
                resolved_name = logical["nameKR"]
            else:
                inverse = {v: k for k, v in reference.remoldings().items()}
                if name not in inverse:
                    raise ValueError(f"unknown remolding name: {name}")
                code = inverse[name]
                meta = code_index[code]
                resolved_option_key = meta["option_key"]
                resolved_name = name
        else:
            raise ValueError("code, name or option_key is required")

        if source_uid:
            row = self.repo.con.execute("SELECT slots_json FROM remoldings WHERE uid=?", (source_uid,)).fetchone()
            if not row:
                raise ValueError(f"remolding uid {source_uid} is not in inventory")
            owned_keys = set()
            for slot in json.loads(row["slots_json"]):
                owned_key = slot.get("option_key") or code_index.get(str(slot.get("code", "")).lower(), {}).get("option_key")
                if owned_key:
                    owned_keys.add(owned_key)
            if resolved_option_key not in owned_keys:
                raise ValueError(f"remolding {source_uid} does not contain {resolved_option_key}")

        with self.repo.transaction():
            self.repo.con.execute(
                "INSERT OR REPLACE INTO remolding_pattern_slots(pattern_id,slot_index,code,name,source_remolding_uid,option_key) VALUES(?,?,?,?,?,?)",
                (pattern_id,slot_index,code,resolved_name,source_uid,resolved_option_key),
            )
            self.repo.con.execute("UPDATE remolding_patterns SET updated_at=? WHERE id=?", (utc_now(),pattern_id))

    def matches(self, pattern_id: int) -> list[dict[str, Any]]:
        code_index = reference.remolding_code_index()
        desired = set()
        for r in self.repo.con.execute("SELECT option_key,code FROM remolding_pattern_slots WHERE pattern_id=?", (pattern_id,)):
            key = r["option_key"] or code_index.get(str(r["code"]).lower(), {}).get("option_key")
            if key:
                desired.add(key)
        out = []
        for r in self.repo.con.execute("SELECT * FROM remoldings"):
            slots = json.loads(r["slots_json"])
            have = set()
            for s in slots:
                key = s.get("option_key") or code_index.get(str(s.get("code", "")).lower(), {}).get("option_key")
                if key:
                    have.add(key)
            hits = desired & have
            out.append({"uid":r["uid"],"score":len(hits),"matched_option_keys":sorted(hits),"slots":slots})
        return sorted(out,key=lambda x:(-x["score"],x["uid"]))
