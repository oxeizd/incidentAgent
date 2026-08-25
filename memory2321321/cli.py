from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.memory.application import MemoryApplication
from app.memory.facade import MemoryFacade
from app.memory.settings import MemorySettings, default_memory_settings


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

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "health",
        help="Check database, vector tables and entity catalog.",
    )
    commands.add_parser(
        "cleanup",
        help="Remove expired, invalidated and failed search snapshots.",
    )
    commands.add_parser(
        "rebuild-catalog",
        help=(
            "Rebuild DB entity catalog from persisted incidents."
        ),
    )

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
    settings = _build_settings(args)
    application = MemoryApplication(settings)
    memory = MemoryFacade(application)

    await application.start()

    try:
        result = await _execute_command(
            memory=memory,
            args=args,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        await application.stop()


async def _execute_command(
    *,
    memory: MemoryFacade,
    args: argparse.Namespace,
) -> dict[str, object]:
    if args.command == "health":
        return await memory.healthcheck()

    if args.command == "cleanup":
        deleted = await memory.cleanup_expired_search_results()
        return {"deleted_search_results": deleted}

    if args.command == "rebuild-catalog":
        return await memory.rebuild_entity_catalog()

    if args.command == "backfill-vectors":
        return await _rebuild_vectors(
            memory=memory,
            entity=args.entity,
            batch_size=args.batch_size,
        )

    if args.command == "import-json":
        report = await memory.import_json_file(
            entity=args.entity,
            file_path=args.file,
            max_errors=args.max_errors,
        )
        return report.model_dump(mode="json")

    raise RuntimeError(f"Unsupported command: {args.command}")


async def _rebuild_vectors(
    *,
    memory: MemoryFacade,
    entity: str,
    batch_size: int,
) -> dict[str, object]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    return await memory.rebuild_vector_indexes(
        entity=entity,
        batch_size=batch_size,
    )


def _build_settings(args: argparse.Namespace) -> MemorySettings:
    current = default_memory_settings(args.project_root)

    database_path = args.database_path or current.database_path
    embedding_model_path = (
        args.embedding_model_path
        or current.embedding_model_path
    )
    vector_dimension = (
        args.vector_dimension
        or current.vector_dimension
    )

    return MemorySettings(
        database_path=database_path,
        schema_path=current.schema_path,
        migrations_path=current.migrations_path,
        embedding_model_path=embedding_model_path,
        vector_dimension=vector_dimension,
        search_preview_limit=current.search_preview_limit,
        cleanup_interval_seconds=current.cleanup_interval_seconds,
        import_index_batch_size=current.import_index_batch_size,
        semantic_default_limit=current.semantic_default_limit,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()