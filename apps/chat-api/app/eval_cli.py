from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from app.evals import (
    build_runtime,
    close_runtime,
    format_results_text,
    insert_eval_runs,
    replay_conversation,
    resolve_eval_target,
    serialise_results,
)
from app.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ollive", description="OlliveLogs developer CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    eval_parser = subparsers.add_parser(
        "eval",
        help="Replay a persisted conversation against another model and diff the results.",
    )
    eval_parser.add_argument("--conversation-id", required=True, help="Conversation UUID to replay.")
    eval_parser.add_argument("--against", required=True, help="Target model, e.g. gpt-4.1 or openai/gpt-4.1.")
    eval_parser.add_argument("--provider", help="Override the target provider if it cannot be inferred.")
    eval_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the text summary.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "eval":
        asyncio.run(_run_eval(args))


async def _run_eval(args: argparse.Namespace) -> None:
    settings = get_settings()
    against = resolve_eval_target(
        args.against,
        provider_override=args.provider,
        default_provider=settings.default_provider,
    )
    postgres_pool, http_client, provider_registry, clickhouse_client = await build_runtime(settings)
    try:
        results = await replay_conversation(
            postgres_pool=postgres_pool,
            provider_registry=provider_registry,
            settings=settings,
            conversation_id=UUID(args.conversation_id),
            against=against,
        )
        await insert_eval_runs(clickhouse_client, results)
    finally:
        await close_runtime(postgres_pool, http_client, clickhouse_client)

    if args.json:
        print(json.dumps(serialise_results(results), default=str, indent=2))
        return
    print(format_results_text(results))


if __name__ == "__main__":
    main()
