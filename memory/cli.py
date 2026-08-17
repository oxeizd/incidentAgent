from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from memory.application import MemoryApplication
from memory.settings import default_memory_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory",
        description="Memory subsystem administration commands.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing the memory package.",
    )
    parser.add_argument(
        "--database-path",
        type=Path,
        default=None,
        help="Override database path from settings.",
    )
    parser.add_argument(
        "--embedding-model-path",
        type=str,
        default=None,
        help="Override embedding model path from settings.",
    )
    parser.add_argument(
        "--vector-dimension",
        type=int,
        default=None,
        help="Override vector dimension from settings.",
    )

    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("health", help="Check database and vector tables.")
    commands.add_parser("cleanup", help="Remove expired search snapshots.")

    backfill = commands.add_parser(
        "backfill-vectors",
        help="Rebuild vectors from persisted incidents and assignments.",
    )
    backfill.add_argument(
        "--entity",
        choices=("incidents", "assignments", "all"),
        default="all",
    )
    backfill.add_argument(
        "--batch-size",
        type=int,
        default=100,
    )

    import_command = commands.add_parser(
        "import-json",
        help="Import incidents or assignments from a JSON file.",
    )
    import_command.add_argument(
        "--entity",
        choices=("incidents", "assignments"),
        required=True,
    )
    import_command.add_argument(
        "--file",
        type=Path,
        required=True,
    )
    import_command.add_argument(
        "--max-errors",
        type=int,
        default=100,
    )

    return parser


async def run(args: argparse.Namespace) -> int:
    settings = default_memory_settings(args.project_root)

    if args.database_path is not None:
        settings = settings.__class__(
            database_path=args.database_path,
            schema_path=settings.schema_path,
            migrations_path=settings.migrations_path,
            embedding_model_path=(
                args.embedding_model_path
                or settings.embedding_model_path
            ),
            vector_dimension=(
                args.vector_dimension
                or settings.vector_dimension
            ),
            search_preview_limit=settings.search_preview_limit,
            cleanup_interval_seconds=settings.cleanup_interval_seconds,
            import_index_batch_size=settings.import_index_batch_size,
            semantic_default_limit=settings.semantic_default_limit,
        )
    elif (
        args.embedding_model_path is not None
        or args.vector_dimension is not None
    ):
        settings = settings.__class__(
            database_path=settings.database_path,
            schema_path=settings.schema_path,
            migrations_path=settings.migrations_path,
            embedding_model_path=(
                args.embedding_model_path
                or settings.embedding_model_path
            ),
            vector_dimension=(
                args.vector_dimension
                or settings.vector_dimension
            ),
            search_preview_limit=settings.search_preview_limit,
            cleanup_interval_seconds=settings.cleanup_interval_seconds,
            import_index_batch_size=settings.import_index_batch_size,
            semantic_default_limit=settings.semantic_default_limit,
        )

    app = MemoryApplication(settings)
    await app.start()

    try:
        if args.command == "health":
            print(json.dumps(
                await app.healthcheck(),
                ensure_ascii=False,
                indent=2,
            ))
            return 0

        if args.command == "cleanup":
            deleted = await app.search_results.cleanup_expired()
            print(json.dumps(
                {"deleted_search_results": deleted},
                ensure_ascii=False,
            ))
            return 0

        if args.command == "backfill-vectors":
            result: dict[str, int] = {}

            if args.entity in ("incidents", "all"):
                result["incidents_processed"] = (
                    await app.vector_backfill.backfill_incidents(
                        batch_size=args.batch_size
                    )
                )

            if args.entity in ("assignments", "all"):
                result["assignments_processed"] = (
                    await app.vector_backfill.backfill_assignments(
                        batch_size=args.batch_size
                    )
                )

            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "import-json":
            report = await app.imports.import_json_file(
                entity=args.entity,
                file_path=args.file,
                max_errors=args.max_errors,
            )
            print(report.model_dump_json(
                ensure_ascii=False,
                indent=2,
            ))
            return 0

        raise RuntimeError(f"Unsupported command: {args.command}")
    finally:
        await app.stop()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()