from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from dhara.auth import OperatorAuthenticator, OperatorRole
from dhara.ingestion import IngestionService
from dhara.replay import replay_file
from dhara.repository import ObservationRepository
from dhara.sensor_training import train_and_evaluate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dhara", description="D.H.A.R.A. prototype tools")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="replay a provider-envelope JSONL file")
    replay.add_argument("path")
    replay.add_argument("--database", default="var/dhara.db")
    train = commands.add_parser("train-loop-a", help="train and evaluate the sensor ensemble")
    train.add_argument("dataset")
    train.add_argument("--model", default="var/models/loop_a.joblib")
    train.add_argument("--report", default="var/reports/loop_a_evaluation.json")
    train.add_argument("--version", default="loop-a-sprint2")
    train.add_argument(
        "--data-classification",
        choices=("synthetic", "verified"),
        default="synthetic",
    )
    token = commands.add_parser(
        "issue-operator-token",
        help="issue a short-lived shadow-console token from the configured gateway secret",
    )
    token.add_argument("subject")
    token.add_argument("--role", choices=tuple(OperatorRole), default="viewer")
    token.add_argument("--minutes", type=int, default=30)
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
    if args.command == "train-loop-a":
        report = train_and_evaluate(
            args.dataset,
            args.model,
            version=args.version,
            report_path=args.report,
            data_classification=args.data_classification,
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "issue-operator-token":
        authenticator = OperatorAuthenticator.from_environment()
        if authenticator.ephemeral:
            raise RuntimeError(
                "DHARA_OPERATOR_HMAC_SECRET must be configured before issuing tokens"
            )
        token = authenticator.issue_token(
            subject=args.subject,
            role=OperatorRole(args.role),
            issued_at=datetime.now(UTC),
            lifetime=timedelta(minutes=args.minutes),
        )
        print(token)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
