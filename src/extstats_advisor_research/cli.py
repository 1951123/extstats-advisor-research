"""Command-line entry point for the research harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis.audit import run_audit
from .baseline_gap import run_baseline_gap
from .datasets import census13, dmv11, forest10, get_dataset, power7
from .dmv_transfer import resolve_dmv_source_run, run_dmv_data_transfer
from .forest_baseline import run_dmv11_baseline, run_forest_baseline, run_power7_baseline
from .forest_transfer import run_forest_data_transfer
from .full_data_transfer import run_full_data_transfer
from .paper_baseline import run_paper_baseline
from .power_transfer import run_power_data_transfer
from .rq3_fidelity import (
    inspect_fidelity_artifact,
    run_synthetic_fidelity,
    validate_fidelity_artifact,
)
from .runner import run_census13, run_dmv11, run_forest10, run_power7
from .screening_k12 import run_screening_k12
from .screening_k16 import run_screening_k16
from .search_budget import run_search_budget_calibration
from .type_coercion import run_type_coercion


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="extstats-research")
    commands = parser.add_subparsers(dest="command", required=True)
    dataset = commands.add_parser("dataset")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    inspect = dataset_commands.add_parser("inspect")
    inspect.add_argument(
        "dataset_id",
        choices=[
            census13.BENCHMARK_ID,
            dmv11.BENCHMARK_ID,
            forest10.BENCHMARK_ID,
            power7.BENCHMARK_ID,
        ],
    )
    inspect.add_argument("--data-root", type=Path)
    load = dataset_commands.add_parser("load")
    load.add_argument(
        "dataset_id",
        choices=[
            census13.BENCHMARK_ID,
            dmv11.BENCHMARK_ID,
            forest10.BENCHMARK_ID,
            power7.BENCHMARK_ID,
        ],
    )
    load.add_argument("--dsn", required=True)
    load.add_argument("--data-root", type=Path)
    load.add_argument("--reset-disposable", action="store_true")
    run = commands.add_parser("run")
    run.add_argument(
        "dataset_id",
        choices=[
            census13.BENCHMARK_ID,
            dmv11.BENCHMARK_ID,
            forest10.BENCHMARK_ID,
            power7.BENCHMARK_ID,
        ],
    )
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
    run.add_argument("--search-wall-clock-seconds", type=float)
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
    postgres.add_argument(
        "dataset_id",
        choices=[
            census13.BENCHMARK_ID,
            dmv11.BENCHMARK_ID,
            forest10.BENCHMARK_ID,
            power7.BENCHMARK_ID,
        ],
    )
    postgres.add_argument("--dsn", required=True)
    postgres.add_argument("--data-root", type=Path)
    postgres.add_argument(
        "--canonical-run", type=Path, default=Path("runs/197e9b890ac58bc4fbcfb218")
    )
    postgres.add_argument("--output-directory", type=Path)
    gap = commands.add_parser("baseline-gap")
    gap.add_argument("dataset_id", choices=[census13.BENCHMARK_ID])
    gap.add_argument("--stock-dsn", required=True)
    gap.add_argument("--planner-dsn", required=True)
    gap.add_argument("--canonical-run", type=Path, default=Path("runs/197e9b890ac58bc4fbcfb218"))
    gap.add_argument(
        "--paper-baseline-directory", type=Path, default=Path("paper-baselines/arecel-census13")
    )
    gap.add_argument(
        "--output-directory", type=Path, default=Path("diagnostics/arecel-census13-baseline-gap")
    )
    gap.add_argument("--data-root", type=Path)
    gap.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    gap.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    diagnose = commands.add_parser("diagnose")
    diagnose_commands = diagnose.add_subparsers(dest="diagnose_command", required=True)
    coercion = diagnose_commands.add_parser("type-coercion")
    coercion.add_argument("dataset_id", choices=[census13.BENCHMARK_ID])
    coercion.add_argument("--dsn", required=True)
    coercion.add_argument("--data-root", type=Path)
    coercion.add_argument(
        "--canonical-run", type=Path, default=Path("runs/197e9b890ac58bc4fbcfb218")
    )
    coercion.add_argument(
        "--baseline-gap-artifact",
        type=Path,
        default=Path("diagnostics/arecel-census13-baseline-gap/baseline-gap-v1.json"),
    )
    coercion.add_argument(
        "--output-directory", type=Path, default=Path("diagnostics/arecel-census13-type-coercion")
    )
    screening = diagnose_commands.add_parser("screening-k12")
    screening.add_argument("source_run", type=Path)
    screening.add_argument("--planner-dsn", required=True)
    screening.add_argument(
        "--output-directory",
        type=Path,
        default=Path("experiments/arecel-census13/screening-k12"),
    )
    screening.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    screening.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    screening.add_argument("--advisor-command", default="extstats-advisor")
    screening16 = diagnose_commands.add_parser("screening-k16")
    screening16.add_argument("source_run", type=Path)
    screening16.add_argument("--planner-dsn", required=True)
    screening16.add_argument(
        "--output-directory",
        type=Path,
        default=Path("experiments/arecel-census13/screening-k16"),
    )
    screening16.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    screening16.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    screening16.add_argument("--advisor-command", default="extstats-advisor")
    calibrate = commands.add_parser("calibrate")
    calibrate_commands = calibrate.add_subparsers(dest="calibrate_command", required=True)
    search_budget = calibrate_commands.add_parser("search-budget")
    search_budget.add_argument("source_run", type=Path)
    search_budget.add_argument("--planner-dsn", required=True)
    search_budget.add_argument("--budgets", type=int, nargs="+", default=[60, 120, 180])
    search_budget.add_argument(
        "--output-directory",
        type=Path,
        default=Path("experiments/arecel-census13/search-budget-k8"),
    )
    search_budget.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    search_budget.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    search_budget.add_argument("--advisor-command", default="extstats-advisor")
    validate = commands.add_parser("validate")
    validate_commands = validate.add_subparsers(dest="validate_command", required=True)
    transfer = validate_commands.add_parser("full-data-transfer")
    transfer.add_argument("source_run", type=Path)
    transfer.add_argument("budget_directory", type=Path, nargs="?")
    transfer.add_argument("--production-dsn", required=True)
    transfer.add_argument("--planner-dsn")
    transfer.add_argument("--output-directory", type=Path)
    transfer.add_argument("--data-root", type=Path)
    transfer.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    transfer.add_argument("--advisor-command", default="extstats-advisor")
    fidelity = validate_commands.add_parser("hypothetical-fidelity")
    fidelity_commands = fidelity.add_subparsers(dest="fidelity_command", required=True)
    fidelity_run = fidelity_commands.add_parser(
        "run", aliases=["create"], help="run the small synthetic MCV+FD fidelity fixture"
    )
    fidelity_run.add_argument("--dsn", required=True)
    fidelity_run.add_argument("--output", type=Path, required=True)
    fidelity_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    fidelity_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    fidelity_validate = fidelity_commands.add_parser(
        "validate", help="validate an existing rq3-fidelity-v1 artifact"
    )
    fidelity_validate.add_argument("artifact", type=Path)
    fidelity_inspect = fidelity_commands.add_parser(
        "inspect", help="print a compact summary of an existing fidelity artifact"
    )
    fidelity_inspect.add_argument("artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "dataset":
        dataset = get_dataset(args.dataset_id)
        if args.dataset_command == "inspect":
            print(json.dumps(dataset.inspect(args.data_root), sort_keys=True, indent=2))
        else:
            from .postgres.loader import load_census13, load_dmv11, load_forest10, load_power7

            loader = {
                dmv11.BENCHMARK_ID: load_dmv11,
                forest10.BENCHMARK_ID: load_forest10,
                power7.BENCHMARK_ID: load_power7,
            }.get(args.dataset_id, load_census13)

            print(
                json.dumps(
                    loader(
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
        if args.dataset_id == dmv11.BENCHMARK_ID:
            result = run_dmv11_baseline(
                args.dsn,
                data_root=args.data_root,
                output_directory=args.output_directory or Path("paper-baselines/arecel-dmv11"),
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.dataset_id == forest10.BENCHMARK_ID:
            result = run_forest_baseline(
                args.dsn,
                data_root=args.data_root,
                output_directory=args.output_directory or Path("paper-baselines/arecel-forest10"),
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.dataset_id == power7.BENCHMARK_ID:
            result = run_power7_baseline(
                args.dsn,
                data_root=args.data_root,
                output_directory=args.output_directory or Path("paper-baselines/arecel-power7"),
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        result = run_paper_baseline(
            args.dsn,
            data_root=args.data_root,
            canonical_run=args.canonical_run,
            output_directory=args.output_directory or Path("paper-baselines/arecel-census13"),
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    if args.command == "baseline-gap":
        result = run_baseline_gap(
            stock_dsn=args.stock_dsn,
            planner_dsn=args.planner_dsn,
            canonical_run=args.canonical_run,
            paper_baseline_directory=args.paper_baseline_directory,
            output_directory=args.output_directory,
            data_root=args.data_root,
            advisor_root=args.advisor_root,
            patched_postgres_root=args.patched_postgres_root,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    if args.command == "diagnose":
        if args.diagnose_command == "screening-k12":
            result = run_screening_k12(
                args.source_run,
                args.planner_dsn,
                output_directory=args.output_directory,
                advisor_root=args.advisor_root,
                patched_postgres_root=args.patched_postgres_root,
                advisor_command=args.advisor_command,
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.diagnose_command == "screening-k16":
            result = run_screening_k16(
                args.source_run,
                args.planner_dsn,
                output_directory=args.output_directory,
                advisor_root=args.advisor_root,
                patched_postgres_root=args.patched_postgres_root,
                advisor_command=args.advisor_command,
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        result = run_type_coercion(
            dsn=args.dsn,
            canonical_run=args.canonical_run,
            baseline_gap_artifact=args.baseline_gap_artifact,
            output_directory=args.output_directory,
            data_root=args.data_root,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    if args.command == "calibrate":
        result = run_search_budget_calibration(
            source_run=args.source_run,
            planner_dsn=args.planner_dsn,
            output_directory=args.output_directory,
            budgets=args.budgets,
            advisor_root=args.advisor_root,
            patched_postgres_root=args.patched_postgres_root,
            advisor_command=args.advisor_command,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    if args.command == "validate":
        if args.validate_command == "hypothetical-fidelity":
            if args.fidelity_command in {"run", "create"}:
                result = run_synthetic_fidelity(
                    dsn=args.dsn,
                    output=args.output,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                )
            elif args.fidelity_command == "validate":
                artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
                result = {
                    "status": "valid",
                    "artifact": str(args.artifact.resolve()),
                    "summary": validate_fidelity_artifact(artifact),
                    "semantic_digest": artifact["semantic_digest"],
                }
            else:
                result = inspect_fidelity_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command != "full-data-transfer":
            raise ValueError(f"unsupported validation command: {args.validate_command}")
        resolved_source_run = args.source_run
        if not (resolved_source_run / "manifest.json").is_file():
            try:
                resolved_source_run = resolve_dmv_source_run(resolved_source_run)
            except FileNotFoundError:
                pass
        manifest_path = resolved_source_run / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {}
        if manifest.get("benchmark_id") == dmv11.BENCHMARK_ID:
            if args.budget_directory is not None:
                raise ValueError("DMV11 full-data transfer does not take a budget directory")
            if args.planner_dsn is not None:
                raise ValueError("DMV11 full-data transfer does not take a planner DSN")
            result = run_dmv_data_transfer(
                resolved_source_run,
                args.production_dsn,
                output_directory=args.output_directory,
                advisor_command=args.advisor_command,
                advisor_root=args.advisor_root,
                data_root=args.data_root,
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if manifest.get("benchmark_id") == forest10.BENCHMARK_ID:
            if args.budget_directory is not None:
                raise ValueError("Forest10 full-data transfer does not take a budget directory")
            result = run_forest_data_transfer(
                resolved_source_run,
                args.production_dsn,
                output_directory=args.output_directory,
                advisor_command=args.advisor_command,
                data_root=args.data_root,
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if manifest.get("benchmark_id") == "arecel-power7":
            if args.budget_directory is not None:
                raise ValueError("Power7 full-data transfer does not take a budget directory")
            if args.planner_dsn is not None:
                raise ValueError("Power7 full-data transfer does not take a planner DSN")
            result = run_power_data_transfer(
                resolved_source_run,
                args.production_dsn,
                output_directory=args.output_directory,
                advisor_command=args.advisor_command,
                advisor_root=args.advisor_root,
                data_root=args.data_root,
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.budget_directory is None:
            raise ValueError("Census13 full-data transfer requires a budget directory")
        result = run_full_data_transfer(
            resolved_source_run,
            args.budget_directory,
            args.production_dsn,
            planner_dsn=args.planner_dsn,
            output_directory=args.output_directory,
            advisor_command=args.advisor_command,
            data_root=args.data_root,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    if args.dataset_id == dmv11.BENCHMARK_ID:
        runner = run_dmv11
        search_wall_clock_seconds = (
            300.0 if args.search_wall_clock_seconds is None else args.search_wall_clock_seconds
        )
    elif args.dataset_id == power7.BENCHMARK_ID:
        runner = run_power7
        search_wall_clock_seconds = (
            300.0 if args.search_wall_clock_seconds is None else args.search_wall_clock_seconds
        )
    elif args.dataset_id == forest10.BENCHMARK_ID:
        runner = run_forest10
        search_wall_clock_seconds = (
            30.0 if args.search_wall_clock_seconds is None else args.search_wall_clock_seconds
        )
    else:
        runner = run_census13
        search_wall_clock_seconds = (
            30.0 if args.search_wall_clock_seconds is None else args.search_wall_clock_seconds
        )
    result = runner(
        production_dsn=args.production_dsn,
        planner_dsn=args.planner_dsn,
        advisor_root=args.advisor_root,
        patched_postgres_root=args.patched_postgres_root,
        output_root=args.output_root,
        sample_rows=args.sample_rows,
        sample_seed=args.sample_seed,
        statistics_target=args.statistics_target,
        candidate_limit=args.candidate_limit,
        search_wall_clock_seconds=search_wall_clock_seconds,
        data_root=args.data_root,
        reset_disposable=args.reset_disposable,
        advisor_command=args.advisor_command,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0
