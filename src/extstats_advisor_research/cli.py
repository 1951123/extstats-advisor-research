"""Command-line entry point for the research harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis.audit import run_audit
from .datasets import census13
from .paper_baseline import run_paper_baseline
from .runner import run_census13


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="extstats-research")
    commands = parser.add_subparsers(dest="command", required=True)
    dataset = commands.add_parser("dataset")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    inspect = dataset_commands.add_parser("inspect")
    inspect.add_argument("dataset_id", choices=[census13.BENCHMARK_ID])
    inspect.add_argument("--data-root", type=Path)
    load = dataset_commands.add_parser("load")
    load.add_argument("dataset_id", choices=[census13.BENCHMARK_ID])
    load.add_argument("--dsn", required=True)
    load.add_argument("--data-root", type=Path)
    load.add_argument("--reset-disposable", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("dataset_id", choices=[census13.BENCHMARK_ID])
    run.add_argument("--production-dsn", required=True)
    run.add_argument("--planner-dsn", required=True)
    run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    run.add_argument("--sample-rows", type=int, default=10_000)
    run.add_argument("--sample-seed", type=int, default=42)
    run.add_argument("--statistics-target", type=int, default=100)
    run.add_argument("--candidate-limit", type=int, default=8)
    run.add_argument("--search-wall-clock-seconds", type=float, default=30.0)
    run.add_argument("--output-root", type=Path, default=Path("runs"))
    run.add_argument("--data-root", type=Path)
    run.add_argument("--reset-disposable", action="store_true")
    run.add_argument("--advisor-command", default="extstats-advisor")
    audit = commands.add_parser("audit")
    audit.add_argument("run_directory", type=Path)
    audit.add_argument("--planner-dsn", required=True)
    audit.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    audit.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    audit.add_argument("--output-directory", type=Path)
    paper_baseline = commands.add_parser("paper-baseline")
    paper_commands = paper_baseline.add_subparsers(dest="paper_command", required=True)
    postgres = paper_commands.add_parser("postgres")
    postgres.add_argument("dataset_id", choices=[census13.BENCHMARK_ID])
    postgres.add_argument("--dsn", required=True)
    postgres.add_argument("--data-root", type=Path)
    postgres.add_argument(
        "--canonical-run", type=Path, default=Path("runs/197e9b890ac58bc4fbcfb218")
    )
    postgres.add_argument(
        "--output-directory", type=Path, default=Path("paper-baselines/arecel-census13")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "dataset":
        if args.dataset_command == "inspect":
            print(json.dumps(census13.inspect(args.data_root), sort_keys=True, indent=2))
        else:
            from .postgres.loader import load_census13

            print(
                json.dumps(
                    load_census13(
                        args.dsn, data_root=args.data_root, reset_disposable=args.reset_disposable
                    ),
                    sort_keys=True,
                    indent=2,
                )
            )
        return 0
    if args.command == "audit":
        result = run_audit(
            args.run_directory,
            planner_dsn=args.planner_dsn,
            advisor_root=args.advisor_root,
            patched_postgres_root=args.patched_postgres_root,
            output_directory=args.output_directory,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    if args.command == "paper-baseline":
        result = run_paper_baseline(
            args.dsn,
            data_root=args.data_root,
            canonical_run=args.canonical_run,
            output_directory=args.output_directory,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    result = run_census13(
        production_dsn=args.production_dsn,
        planner_dsn=args.planner_dsn,
        advisor_root=args.advisor_root,
        patched_postgres_root=args.patched_postgres_root,
        output_root=args.output_root,
        sample_rows=args.sample_rows,
        sample_seed=args.sample_seed,
        statistics_target=args.statistics_target,
        candidate_limit=args.candidate_limit,
        search_wall_clock_seconds=args.search_wall_clock_seconds,
        data_root=args.data_root,
        reset_disposable=args.reset_disposable,
        advisor_command=args.advisor_command,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0
