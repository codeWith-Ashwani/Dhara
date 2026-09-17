from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from dhara.ingestion import IngestionService
from dhara.replay import replay_file
from dhara.repository import ObservationRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dhara", description="D.H.A.R.A. prototype tools")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="replay a provider-envelope JSONL file")
    replay.add_argument("path")
    replay.add_argument("--database", default="var/dhara.db")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "replay":
        repository = ObservationRepository(args.database)
        try:
            summary = replay_file(args.path, IngestionService(repository))
            print(json.dumps(summary.to_dict(), indent=2))
        finally:
            repository.close()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
