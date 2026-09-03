from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from . import reference
from .repository import Repository
from .services.formations import FormationService
from .services.remoldings import RemoldingPatternService
from .services.remolding_recommendation import RemoldingRecommendationService
from .snapshot import export_snapshot, import_snapshot

DEFAULT_DB = Path("data/gfl2.db")
DEFAULT_SNAPSHOT = Path("data/latest.json")


def _csv_str(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gfl2tool",
        description="GFL2 offline planner and data interchange tools",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("gui")
    sub.add_parser("summary")

    list_parser = sub.add_parser("list")
    list_parser.add_argument(
        "kind",
        choices=[
            "dolls",
            "remoldings",
            "game_formations",
            "formation_plans",
            "formation_members",
            "remolding_patterns",
            "remolding_pattern_slots",
        ],
    )

    export = sub.add_parser("export")
    export.add_argument("path", nargs="?", default=str(DEFAULT_SNAPSHOT))

    import_parser = sub.add_parser("import")
    import_parser.add_argument("path")
    import_parser.add_argument("--merge", action="store_true")

    csv_export = sub.add_parser("user-csv-export")
    csv_export.add_argument("path", help="ZIP path")
    csv_import = sub.add_parser("user-csv-import")
    csv_import.add_argument("path", help="ZIP path")
    csv_import.add_argument("--merge", action="store_true")

    user_export = sub.add_parser("user-data-export")
    user_export.add_argument("path", help="ZIP path")
    user_import = sub.add_parser("user-data-import")
    user_import.add_argument("path", help="ZIP path")
    user_import.add_argument("--merge", action="store_true")

    reference_export = sub.add_parser("reference-export")
    reference_export.add_argument("path", help="ZIP path")
    reference_import = sub.add_parser("reference-import")
    reference_import.add_argument("path", help="ZIP path")

    _add_formation_commands(sub)
    _add_remolding_pattern_commands(sub)
    _add_remolding_optimizer_commands(sub)
    return parser


def _add_formation_commands(sub) -> None:
    formation = sub.add_parser("formation")
    actions = formation.add_subparsers(dest="action", required=True)

    create = actions.add_parser("create")
    create.add_argument("name")
    create.add_argument("--notes", default="")
    actions.add_parser("list")

    show = actions.add_parser("show")
    show.add_argument("id", type=int)

    delete = actions.add_parser("delete")
    delete.add_argument("id", type=int)

    set_member = actions.add_parser("set-member")
    set_member.add_argument("id", type=int)
    set_member.add_argument("position", type=int)
    set_member.add_argument("doll_id", type=int)
    set_member.add_argument("--remoldings", help="comma-separated owned remolding UIDs")

    remove_member = actions.add_parser("remove-member")
    remove_member.add_argument("id", type=int)
    remove_member.add_argument("position", type=int)

    import_game = actions.add_parser("import-game")
    import_game.add_argument("game_formation_id", type=int)
    import_game.add_argument("--name")


def _add_remolding_pattern_commands(sub) -> None:
    remolding = sub.add_parser("remolding-pattern")
    actions = remolding.add_subparsers(dest="action", required=True)

    create = actions.add_parser("create")
    create.add_argument("name")
    create.add_argument("--doll-id", type=int)
    create.add_argument("--character-key")
    create.add_argument("--notes", default="")
    actions.add_parser("list")

    show = actions.add_parser("show")
    show.add_argument("id", type=int)

    delete = actions.add_parser("delete")
    delete.add_argument("id", type=int)

    set_slot = actions.add_parser("set-slot")
    set_slot.add_argument("id", type=int)
    set_slot.add_argument("slot", type=int)
    target = set_slot.add_mutually_exclusive_group(required=True)
    target.add_argument("--code")
    target.add_argument("--name")
    target.add_argument("--option-key")
    set_slot.add_argument("--source-uid")

    matches = actions.add_parser("matches")
    matches.add_argument("id", type=int)


def _add_remolding_optimizer_commands(sub) -> None:
    optimizer = sub.add_parser("remolding-optimize")
    actions = optimizer.add_subparsers(dest="action", required=True)
    actions.add_parser("characters")

    recommend = actions.add_parser("recommend")
    recommend.add_argument("character_key")
    recommend.add_argument("--factor", choices=["sentinel", "vanguard", "bulwark", "support"])

    owned = actions.add_parser("owned")
    owned.add_argument("character_key")


def _handle_formation(repo: Repository, args) -> None:
    service = FormationService(repo)
    if args.action == "create":
        _print({"id": service.create(args.name, args.notes)})
    elif args.action == "list":
        _print(service.list())
    elif args.action == "show":
        _print(service.get(args.id))
    elif args.action == "delete":
        service.delete(args.id)
        _print({"deleted": args.id})
    elif args.action == "set-member":
        service.set_member(args.id, args.position, args.doll_id, remolding_uids=_csv_str(args.remoldings))
        _print(service.get(args.id))
    elif args.action == "remove-member":
        service.remove_member(args.id, args.position)
        _print(service.get(args.id))
    elif args.action == "import-game":
        _print({"id": service.import_game_formation(args.game_formation_id, args.name)})


def _handle_remolding_pattern(repo: Repository, args) -> None:
    service = RemoldingPatternService(repo)
    if args.action == "create":
        _print({"id": service.create(args.name, args.doll_id, args.notes, args.character_key)})
    elif args.action == "list":
        _print(service.list())
    elif args.action == "show":
        _print(service.get(args.id))
    elif args.action == "delete":
        service.delete(args.id)
        _print({"deleted": args.id})
    elif args.action == "set-slot":
        service.set_slot(
            args.id, args.slot, code=args.code, name=args.name,
            option_key=args.option_key, source_uid=args.source_uid,
        )
        _print(service.get(args.id))
    elif args.action == "matches":
        _print(service.matches(args.id))


def _handle_remolding_optimizer(repo: Repository, args) -> None:
    service = RemoldingRecommendationService(repo)
    if args.action == "characters":
        _print(service.list_characters())
    elif args.action == "recommend":
        _print(service.recommendations(args.character_key, args.factor))
    elif args.action == "owned":
        _print(service.score_owned_remoldings(args.character_key))


def _handle_repository_command(repo: Repository, args) -> None:
    if args.cmd == "init":
        _print({"database": str(repo.path), "schema": "ready"})
    elif args.cmd == "summary":
        _print(repo.inventory_summary())
    elif args.cmd == "list":
        _print(repo.rows(args.kind))
    elif args.cmd == "export":
        _print({"snapshot": str(export_snapshot(repo, args.path))})
    elif args.cmd == "import":
        import_snapshot(repo, args.path, replace=not args.merge)
        _print({"imported": args.path})
    elif args.cmd == "formation":
        _handle_formation(repo, args)
    elif args.cmd == "remolding-pattern":
        _handle_remolding_pattern(repo, args)
    elif args.cmd == "remolding-optimize":
        _handle_remolding_optimizer(repo, args)
    elif args.cmd == "user-csv-export":
        from .services.data_exchange import export_user_csv_bundle
        _print({"path": str(export_user_csv_bundle(repo, args.path))})
    elif args.cmd == "user-csv-import":
        from .services.data_exchange import import_user_csv_bundle
        _print(import_user_csv_bundle(repo, args.path, replace=not args.merge))
    elif args.cmd == "user-data-export":
        from .services.data_exchange import export_all_user_data
        _print({"path": str(export_all_user_data(repo, args.path))})
    elif args.cmd == "user-data-import":
        from .services.data_exchange import import_all_user_data
        _print({"datasets": sorted(import_all_user_data(repo, args.path, replace=not args.merge))})
    elif args.cmd == "reference-export":
        from .services.data_exchange import export_all_reference_data
        _print({"path": str(export_all_reference_data(repo.path.parent, args.path))})
    elif args.cmd == "reference-import":
        from .services.data_exchange import import_all_reference_data
        _print({"datasets": sorted(import_all_reference_data(repo.path.parent, args.path))})


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "gui":
        from .qtgui import main as gui_main
        gui_main(args.db)
        return

    with Repository(args.db) as repo:
        reference.configure_override_root(repo.path.parent)
        _handle_repository_command(repo, args)


if __name__ == "__main__":
    main()
