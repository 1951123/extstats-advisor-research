"""Command-line entry point for the research harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis.audit import run_audit
from .arecel_truth import validate_truth_policy
from .baseline_gap import run_baseline_gap
from .datasets import census13, dmv11, forest10, get_dataset, power7
from .dmv_transfer import resolve_dmv_source_run, run_dmv_data_transfer
from .forest_baseline import run_dmv11_baseline, run_forest_baseline, run_power7_baseline
from .forest_transfer import run_forest_data_transfer
from .full_data_transfer import run_full_data_transfer
from .incremental_search_hardening import (
    DEFAULT_SOURCE_RUN,
    run_hardening_smoke,
)
from .incremental_search_hardening import (
    inspect_artifact as inspect_incremental_hardening,
)
from .incremental_search_hardening import (
    validate_artifact as validate_incremental_hardening,
)
from .native_analyze_stability_harness import (
    PROTOCOL_PATH as NATIVE_STABILITY_PROTOCOL_PATH,
)
from .native_analyze_stability_harness import (
    dry_run_plan as native_stability_dry_run_plan,
)
from .native_analyze_stability_harness import (
    load_and_validate_protocol as load_native_stability_protocol,
)
from .native_analyze_stability_harness import (
    validate_invocation_artifact as validate_native_stability_invocation,
)
from .native_analyze_stability_harness import (
    validate_readiness_v2 as validate_native_stability_readiness_v2,
)
from .oid_order_sensitivity import (
    PREFLIGHT_PATH as OID_PREFLIGHT_PATH,
)
from .oid_order_sensitivity import (
    build_preflight as build_oid_preflight,
)
from .oid_order_sensitivity import (
    run_formal as run_oid_formal,
)
from .oid_order_sensitivity import (
    validate_preflight as validate_oid_preflight,
)
from .oid_order_sensitivity import (
    validate_protocol as validate_oid_protocol,
)
from .oid_order_sensitivity import (
    validate_result_artifact as validate_oid_result,
)
from .paper_baseline import run_paper_baseline
from .paper_spec import (
    DEFAULT_SPEC_PATH,
    TOP_K_PROTOCOL_PATH,
    load_paper_spec,
    load_top_k_screening_protocol,
    validate_paper_spec,
    validate_top_k_screening_protocol,
)
from .postgres_lab import (
    build_role,
    destroy_role,
    doctor,
    env_exports,
    init_role,
    inspect,
    recreate_role,
    reinit_role,
    roles_for,
    start_role,
    status_role,
    stop_role,
)
from .power_transfer import run_power_data_transfer
from .provenance import read_json, semantic_digest, write_json
from .rq1_canary import (
    inspect_rq1_artifact,
    run_census13_canary,
    validate_rq1_file,
)
from .rq1_matched import RQ1_DATASETS, run_rq1_matched
from .rq1_rebind import canonicalize_census13, validate_rebound_artifact
from .rq1_summary import (
    build_cross_dataset_summary,
    default_source_paths,
    validate_cross_dataset_summary,
)
from .rq1_workload_generalization import (
    PROTOCOL_PATH as RQ1B_PROTOCOL_PATH,
)
from .rq1_workload_generalization import (
    PROTOCOL_V2_PATH as RQ1B_PROTOCOL_V2_PATH,
)
from .rq1_workload_generalization import (
    SOURCE_AUDIT_PATH as RQ1B_SOURCE_AUDIT_PATH,
)
from .rq1_workload_generalization import (
    SOURCE_AUDIT_V2_PATH as RQ1B_SOURCE_AUDIT_V2_PATH,
)
from .rq1_workload_generalization import (
    STRICT_UNSEEN_PATH as RQ1B_STRICT_UNSEEN_PATH,
)
from .rq1_workload_generalization import (
    TRUTH_POLICY_PATH as RQ1B_TRUTH_POLICY_PATH,
)
from .rq1_workload_generalization import (
    build_source_audit_v2 as build_rq1b_source_audit_v2,
)
from .rq1_workload_generalization import (
    build_strict_unseen_membership as build_rq1b_strict_unseen,
)
from .rq1_workload_generalization import (
    validate_protocol as validate_rq1b_protocol,
)
from .rq1_workload_generalization import (
    validate_protocol_v2 as validate_rq1b_protocol_v2,
)
from .rq1_workload_generalization import (
    validate_source_audit as validate_rq1b_source_audit,
)
from .rq1_workload_generalization import (
    validate_source_audit_v2 as validate_rq1b_source_audit_v2,
)
from .rq1_workload_generalization import (
    validate_strict_unseen_membership as validate_rq1b_strict_unseen,
)
from .rq1_workload_generalization import (
    validate_truth_policy as validate_rq1b_truth_policy,
)
from .rq1_workload_generalization import (
    write_source_audit as write_rq1b_source_audit,
)
from .rq1_workload_generalization_live import (
    CENSUS13_SPEC,
    DMV11_SPEC,
    FOREST10_SPEC,
    POWER7_SPEC,
    run_census13_rq1b_formal,
    run_dmv11_rq1b_formal,
    run_forest10_rq1b_formal,
    run_power7_rq1b_formal,
    validate_rq1b_preflight,
    validate_rq1b_result,
    write_census13_rq1b_preflight,
    write_dmv11_rq1b_preflight,
    write_forest10_rq1b_preflight,
    write_power7_rq1b_preflight,
)
from .rq1_workload_generalization_live import (
    validate_design_artifact as validate_rq1b_design_artifact_generic,
)
from .rq1b_synthesis import (
    build_cross_dataset_synthesis,
    validate_cross_dataset_synthesis,
)
from .rq2_transfer import (
    RQ2_DATASETS,
    inspect_rq2_artifact,
    preflight_rq2,
    run_rq2_child,
    validate_rq2_artifact,
    validate_rq2_cross_dataset,
)
from .rq3_build_sanity import (
    inspect_build_sanity_artifact,
    run_build_sanity,
    validate_build_sanity_file,
)
from .rq3_fidelity import (
    inspect_fidelity_artifact,
    run_synthetic_fidelity,
    validate_fidelity_artifact,
)
from .rq4_ablation import (
    current_research_commit,
    validate_rq4_artifact,
    write_synthetic_rq4_artifact,
)
from .rq4_baseline_resolution import validate_resolution_artifact
from .rq4_determinism import validate_design_determinism_smoke
from .rq4_formal import run_forest10_fixed_k, validate_forest10_fixed_k_artifact
from .rq4_formal_common import (
    DATASETS as RQ4_V2_DATASETS,
)
from .rq4_formal_common import (
    build_preflight as build_rq4_v2_preflight,
)
from .rq4_formal_common import (
    run_formal_rq4_v2,
    validate_v2_design_artifact,
    validate_v2_determinism,
    validate_v2_summary,
)
from .rq4_ks_sensitivity import (
    FORMAL_DATASETS as RQ4_KS_DATASETS,
)
from .rq4_ks_sensitivity import (
    build_live_smoke_artifact,
    build_smoke_artifact,
    run_formal_ks_sensitivity,
    validate_formal_ks_sensitivity,
    validate_live_smoke_artifact,
    validate_smoke_artifact,
)
from .rq4_ks_sensitivity import (
    build_preflight as build_rq4_ks_preflight,
)
from .rq4_ks_sensitivity import (
    validate_preflight as validate_rq4_ks_preflight,
)
from .rq4_ks_summary import (
    default_summary_path as default_rq4_ks_summary_path,
)
from .rq4_ks_summary import (
    validate_cross_dataset_summary as validate_rq4_ks_summary,
)
from .rq4_ks_summary import (
    write_cross_dataset_summary,
)
from .rq4_physical import validate_shared_stock_realization
from .rq4_postgres import inspect_real_backend_smoke, validate_real_backend_smoke
from .rq5_cost_inventory import (
    default_inventory_path,
    default_inventory_v2_path,
    default_inventory_v3_path,
    validate_inventory,
    write_inventory,
    write_inventory_v2,
    write_inventory_v3,
)
from .rq5_production_exact_truth_cost import (
    PREFLIGHT_FORMAT as PRODUCTION_EXACT_TRUTH_PREFLIGHT_FORMAT,
)
from .rq5_production_exact_truth_cost import (
    PROTOCOL_FORMAT as PRODUCTION_EXACT_TRUTH_PROTOCOL_FORMAT,
)
from .rq5_production_exact_truth_cost import (
    build_preflight as build_production_exact_truth_preflight,
)
from .rq5_production_exact_truth_cost import (
    default_preflight_path as default_production_exact_truth_preflight_path,
)
from .rq5_production_exact_truth_cost import (
    run_canary as run_production_exact_truth_canary,
)
from .rq5_production_exact_truth_cost import (
    validate_artifact as validate_production_exact_truth_artifact,
)
from .rq5_production_exact_truth_cost import (
    validate_preflight_file as validate_production_exact_truth_preflight_file,
)
from .rq5_production_exact_truth_cost import (
    validate_protocol as validate_production_exact_truth_protocol,
)
from .rq5_snapshot_footprint import (
    DATASETS as SNAPSHOT_FOOTPRINT_DATASETS,
)
from .rq5_snapshot_footprint import (
    build_preflight as build_snapshot_footprint_preflight,
)
from .rq5_snapshot_footprint import (
    default_formal_path as default_snapshot_footprint_path,
)
from .rq5_snapshot_footprint import (
    default_preflight_path as default_snapshot_footprint_preflight_path,
)
from .rq5_snapshot_footprint import (
    run_snapshot_footprint,
    summarize_snapshot_footprint,
)
from .rq5_snapshot_footprint import (
    validate_formal_artifact as validate_snapshot_footprint_artifact,
)
from .rq5_snapshot_footprint import (
    validate_preflight as validate_snapshot_footprint_preflight,
)
from .rq5_snapshot_footprint import (
    validate_protocol as validate_snapshot_footprint_protocol,
)
from .rq5_snapshot_footprint import (
    validate_raw_artifact as validate_snapshot_footprint_raw,
)
from .rq5_static_deployment_cost import (
    build_preflight as build_static_deployment_preflight,
)
from .rq5_static_deployment_cost import (
    default_formal_path as default_static_deployment_path,
)
from .rq5_static_deployment_cost import (
    default_preflight_path as default_static_deployment_preflight_path,
)
from .rq5_static_deployment_cost import (
    run_static_deployment,
    summarize_static_deployment,
)
from .rq5_static_deployment_cost import (
    validate_formal_artifact as validate_static_deployment_artifact,
)
from .rq5_static_deployment_cost import (
    validate_preflight as validate_static_deployment_preflight,
)
from .rq5_static_deployment_cost import (
    validate_protocol as validate_static_deployment_protocol,
)
from .runner import run_census13, run_dmv11, run_forest10, run_power7
from .screening_k12 import run_screening_k12
from .screening_k16 import run_screening_k16
from .search_budget import run_search_budget_calibration
from .singleton_equivalence import (
    inspect_artifact as inspect_singleton_equivalence,
)
from .singleton_equivalence import (
    inspect_historical_incremental_artifact,
    run_equivalence,
    run_historical_incremental_equivalence,
    validate_historical_incremental_artifact,
)
from .singleton_equivalence import (
    validate_artifact as validate_singleton_equivalence,
)
from .singleton_equivalence import (
    write_preflight as write_singleton_equivalence_preflight,
)
from .system_freeze import DEFAULT_SYSTEM_FREEZE_PATH, load_system_freeze, validate_system_freeze
from .system_freeze_v2 import (
    DEFAULT_FREEZE_PATH as DEFAULT_SYSTEM_FREEZE_V2_PATH,
)
from .system_freeze_v2 import (
    DEFAULT_READINESS_PATH as DEFAULT_SYSTEM_FREEZE_V2_READINESS_PATH,
)
from .system_freeze_v2 import (
    load_system_freeze_v2,
    validate_readiness_evidence,
    validate_system_freeze_v2,
)
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
    run.add_argument(
        "--truth-source",
        choices=["authoritative-arecel", "production-exact"],
        default="authoritative-arecel",
        help="bind one explicit truth source; authoritative-arecel is the confirmatory default",
    )
    run.add_argument("--authoritative-observations", type=Path)
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
    postgres_lab = commands.add_parser(
        "postgres-lab", help="manage disposable user-owned stock and patched PostgreSQL labs"
    )
    postgres_lab_commands = postgres_lab.add_subparsers(dest="postgres_lab_command", required=True)
    for lab_command in (
        "doctor",
        "build",
        "init",
        "start",
        "stop",
        "status",
        "destroy",
        "recreate",
        "reinit",
        "inspect",
    ):
        lab_parser = postgres_lab_commands.add_parser(lab_command)
        lab_parser.add_argument("--role", choices=["stock", "patched", "all"], default="all")
        if lab_command in {"build", "recreate"}:
            lab_parser.add_argument("--jobs", type=int)
    lab_env = postgres_lab_commands.add_parser(
        "env", help="print local socket environment exports without credentials"
    )
    lab_env.add_argument("--role", choices=["stock", "patched", "all"], default="all")
    rq1g_live = commands.add_parser(
        "rq1-generalization",
        help="RQ1b held-out workload generalization formal commands",
    )
    rq1g_live_commands = rq1g_live.add_subparsers(dest="rq1g_live_command", required=True)
    for dataset_name in ("power7", "forest10", "dmv11", "census13"):
        dataset_parser = rq1g_live_commands.add_parser(dataset_name)
        dataset_commands = dataset_parser.add_subparsers(dest="rq1g_dataset_command", required=True)
        preflight = dataset_commands.add_parser("preflight-create")
        preflight.add_argument("--output", type=Path)
        run = dataset_commands.add_parser("run")
        run.add_argument("--stock-dsn", required=True)
        run.add_argument("--planner-dsn", required=True)
        run.add_argument("--preflight", type=Path, required=True)
        run.add_argument("--data-root", type=Path)
        run.add_argument("--runtime-root", type=Path)
        run.add_argument(
            "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
        )
        run.add_argument(
            "--patched-postgres-root",
            type=Path,
            default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
        )
        run.add_argument(
            "--stock-postgres-root",
            type=Path,
            default=Path("/home/wqts/projects/postgresql-src"),
        )
        run.add_argument("--advisor-command", default="extstats-advisor")
    oid_order = commands.add_parser(
        "oid-order-sensitivity",
        help="preregister, run, or validate the independent OID-order sensitivity ablation",
    )
    oid_commands = oid_order.add_subparsers(dest="oid_command", required=True)
    oid_preflight = oid_commands.add_parser("preflight-create")
    oid_preflight.add_argument("--data-root", type=Path, required=True)
    oid_preflight.add_argument("--output", type=Path, default=OID_PREFLIGHT_PATH)
    oid_run = oid_commands.add_parser("run")
    oid_run.add_argument("--stock-dsn", required=True)
    oid_run.add_argument("--planner-dsn", required=True)
    oid_run.add_argument("--preflight", type=Path, default=OID_PREFLIGHT_PATH)
    oid_run.add_argument("--data-root", type=Path, required=True)
    oid_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    oid_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    oid_run.add_argument(
        "--stock-postgres-root", type=Path, default=Path("/home/wqts/projects/postgresql-src")
    )
    oid_run.add_argument("--invocation-id", required=True)
    oid_run.add_argument(
        "--failure-output",
        type=Path,
        default=Path(
            "experiments/oid-order-sensitivity-v1/failed-attempts/attempt-002/failure-v1.json"
        ),
    )
    oid_validate = oid_commands.add_parser("validate")
    oid_validate.add_argument("kind", choices=("protocol", "preflight", "result"))
    oid_validate.add_argument("artifact", type=Path)
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
        "run",
        aliases=["create"],
        help="run the three-configuration synthetic mechanism-fidelity fixture",
    )
    fidelity_run.add_argument("--dsn", required=True)
    fidelity_run.add_argument("--output", type=Path, required=True)
    fidelity_run.add_argument(
        "--formal",
        action="store_true",
        help="write a formal RQ3 result bound to system-freeze-v2",
    )
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
    paper_spec = validate_commands.add_parser(
        "paper-spec", help="validate paper-experiment-v1 semantic invariants"
    )
    paper_spec.add_argument("spec", type=Path, nargs="?", default=DEFAULT_SPEC_PATH)
    top_k_protocol = validate_commands.add_parser(
        "top-k-screening-protocol", help="validate the preregistered RQ4 K_s protocol"
    )
    top_k_protocol.add_argument("path", type=Path, nargs="?", default=TOP_K_PROTOCOL_PATH)
    truth_policy = validate_commands.add_parser(
        "benchmark-truth-policy",
        help="validate the audited AreCEL truth policy and equivalence gates",
    )
    truth_policy.add_argument(
        "path", type=Path, nargs="?", default=Path("paper/benchmark-truth-policy-v1.json")
    )
    build_sanity = validate_commands.add_parser(
        "build-sanity", help="run or validate the patched-versus-stock RQ3 sanity artifact"
    )
    build_sanity_commands = build_sanity.add_subparsers(dest="build_sanity_command", required=True)
    build_sanity_run = build_sanity_commands.add_parser("run", aliases=["create"])
    build_sanity_run.add_argument("--stock-dsn", required=True)
    build_sanity_run.add_argument("--patched-dsn", required=True)
    build_sanity_run.add_argument("--output", type=Path, required=True)
    build_sanity_run.add_argument(
        "--formal",
        action="store_true",
        help="write a formal RQ3 result bound to system-freeze-v2",
    )
    build_sanity_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    build_sanity_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    build_sanity_validate = build_sanity_commands.add_parser("validate")
    build_sanity_validate.add_argument("artifact", type=Path)
    build_sanity_inspect = build_sanity_commands.add_parser("inspect")
    build_sanity_inspect.add_argument("artifact", type=Path)
    system_freeze = validate_commands.add_parser(
        "system-freeze", help="validate the versioned frozen system-under-test contract"
    )
    system_freeze.add_argument("path", type=Path, nargs="?", default=DEFAULT_SYSTEM_FREEZE_PATH)
    system_freeze_v2 = validate_commands.add_parser(
        "system-freeze-v2", help="validate the scoped System Freeze v2 contract"
    )
    system_freeze_v2.add_argument(
        "path", type=Path, nargs="?", default=DEFAULT_SYSTEM_FREEZE_V2_PATH
    )
    readiness = validate_commands.add_parser(
        "system-freeze-v2-readiness",
        help="validate the committed System Freeze v2 readiness evidence",
    )
    readiness.add_argument(
        "path", type=Path, nargs="?", default=DEFAULT_SYSTEM_FREEZE_V2_READINESS_PATH
    )
    rq1 = validate_commands.add_parser(
        "rq1-canary", help="run or validate the Census13 RQ1 matched-comparison artifact"
    )
    rq1_commands = rq1.add_subparsers(dest="rq1_command", required=True)
    rq1_run = rq1_commands.add_parser("run", aliases=["create"])
    rq1_run.add_argument("--stock-dsn", required=True)
    rq1_run.add_argument("--patched-dsn", required=True)
    rq1_run.add_argument("--output", type=Path, required=True)
    rq1_run.add_argument("--data-root", type=Path)
    rq1_run.add_argument("--truth-artifact", type=Path)
    rq1_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq1_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    rq1_run.add_argument("--advisor-command", default="extstats-advisor")
    rq1_validate = rq1_commands.add_parser("validate")
    rq1_validate.add_argument("artifact", type=Path)
    rq1_inspect = rq1_commands.add_parser("inspect")
    rq1_inspect.add_argument("artifact", type=Path)
    matched = validate_commands.add_parser(
        "rq1-matched", help="run or validate a dataset-generic RQ1 matched comparison"
    )
    matched_commands = matched.add_subparsers(dest="matched_command", required=True)
    matched_run = matched_commands.add_parser("run", aliases=["create"])
    matched_run.add_argument("--dataset", dest="dataset_id", choices=RQ1_DATASETS, required=True)
    matched_run.add_argument("--stock-dsn", required=True)
    matched_run.add_argument("--patched-dsn", required=True)
    matched_run.add_argument("--output", type=Path, required=True)
    matched_run.add_argument("--data-root", type=Path)
    matched_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    matched_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    matched_run.add_argument("--advisor-command", default="extstats-advisor")
    matched_validate = matched_commands.add_parser("validate")
    matched_validate.add_argument("artifact", type=Path)
    matched_inspect = matched_commands.add_parser("inspect")
    matched_inspect.add_argument("artifact", type=Path)
    summary = validate_commands.add_parser(
        "rq1-summary", help="build or validate the four-dataset RQ1 summary"
    )
    summary_commands = summary.add_subparsers(dest="summary_command", required=True)
    summary_create = summary_commands.add_parser("create", aliases=["run"])
    summary_create.add_argument("--output", type=Path, required=True)
    summary_validate = summary_commands.add_parser("validate")
    summary_validate.add_argument("artifact", type=Path)
    synthesis = validate_commands.add_parser(
        "rq1b-synthesis", help="build or validate the offline four-dataset RQ1b synthesis"
    )
    synthesis_commands = synthesis.add_subparsers(dest="synthesis_command", required=True)
    synthesis_create = synthesis_commands.add_parser("create", aliases=["run"])
    synthesis_create.add_argument("--output", type=Path, required=True)
    synthesis_validate = synthesis_commands.add_parser("validate")
    synthesis_validate.add_argument("artifact", type=Path)
    rq1b = validate_commands.add_parser(
        "rq1-workload-generalization",
        help="validate the offline RQ1b split, truth-policy, and source-audit contracts",
    )
    rq1b_commands = rq1b.add_subparsers(dest="rq1b_command", required=True)
    rq1b_protocol = rq1b_commands.add_parser("protocol")
    rq1b_protocol.add_argument("path", type=Path, nargs="?", default=RQ1B_PROTOCOL_PATH)
    rq1b_protocol_v2 = rq1b_commands.add_parser("protocol-v2")
    rq1b_protocol_v2.add_argument("path", type=Path, nargs="?", default=RQ1B_PROTOCOL_V2_PATH)
    rq1b_truth = rq1b_commands.add_parser("truth-policy")
    rq1b_truth.add_argument("path", type=Path, nargs="?", default=RQ1B_TRUTH_POLICY_PATH)
    rq1b_source = rq1b_commands.add_parser("source-audit")
    rq1b_source_commands = rq1b_source.add_subparsers(dest="rq1b_source_command", required=True)
    rq1b_source_create = rq1b_source_commands.add_parser("create")
    rq1b_source_create.add_argument("--output", type=Path, default=None)
    rq1b_source_create.add_argument("--data-root", type=Path, default=None)
    rq1b_source_validate = rq1b_source_commands.add_parser("validate")
    rq1b_source_validate.add_argument(
        "artifact", type=Path, default=RQ1B_SOURCE_AUDIT_PATH, nargs="?"
    )
    rq1b_source_v2 = rq1b_commands.add_parser("source-audit-v2")
    rq1b_source_v2_commands = rq1b_source_v2.add_subparsers(
        dest="rq1b_source_v2_command", required=True
    )
    rq1b_source_v2_create = rq1b_source_v2_commands.add_parser("create")
    rq1b_source_v2_create.add_argument("--output", type=Path, default=None)
    rq1b_source_v2_create.add_argument("--data-root", type=Path, default=None)
    rq1b_source_v2_validate = rq1b_source_v2_commands.add_parser("validate")
    rq1b_source_v2_validate.add_argument(
        "artifact", type=Path, default=RQ1B_SOURCE_AUDIT_V2_PATH, nargs="?"
    )
    rq1b_strict = rq1b_commands.add_parser("strict-unseen")
    rq1b_strict_commands = rq1b_strict.add_subparsers(dest="rq1b_strict_command", required=True)
    rq1b_strict_create = rq1b_strict_commands.add_parser("create")
    rq1b_strict_create.add_argument("--output", type=Path, default=None)
    rq1b_strict_create.add_argument("--data-root", type=Path, default=None)
    rq1b_strict_validate = rq1b_strict_commands.add_parser("validate")
    rq1b_strict_validate.add_argument(
        "artifact", type=Path, default=RQ1B_STRICT_UNSEEN_PATH, nargs="?"
    )
    rq1g = validate_commands.add_parser(
        "rq1-generalization",
        help="validate offline dataset-specific RQ1b design/preflight/result readiness artifacts",
    )
    rq1g_commands = rq1g.add_subparsers(dest="rq1g_command", required=True)
    rq1g_design = rq1g_commands.add_parser("design")
    rq1g_design.add_argument("artifact", type=Path)
    rq1g_design.add_argument(
        "--dataset", choices=["power7", "forest10", "dmv11", "census13"], default="power7"
    )
    rq1g_preflight = rq1g_commands.add_parser("preflight")
    rq1g_preflight.add_argument("artifact", type=Path)
    rq1g_preflight.add_argument(
        "--dataset", choices=["power7", "forest10", "dmv11", "census13"], default="power7"
    )
    rq1g_result = rq1g_commands.add_parser("result")
    rq1g_result.add_argument("artifact", type=Path)
    rq1g_result.add_argument(
        "--dataset", choices=["power7", "forest10", "dmv11", "census13"], default="power7"
    )
    rq2 = validate_commands.add_parser(
        "rq2-transfer", help="run or validate one formal RQ2 transfer child"
    )
    rq2_commands = rq2.add_subparsers(dest="rq2_command", required=True)
    rq2_preflight = rq2_commands.add_parser("preflight")
    rq2_preflight.add_argument("--dataset", choices=RQ2_DATASETS, required=True)
    rq2_run = rq2_commands.add_parser("run", aliases=["create"])
    rq2_run.add_argument("--dataset", choices=RQ2_DATASETS, required=True)
    rq2_run.add_argument("--stock-dsn", required=True)
    rq2_run.add_argument("--patched-dsn", required=True)
    rq2_run.add_argument("--output", type=Path, required=True)
    rq2_run.add_argument("--data-root", type=Path)
    rq2_run.add_argument("--runtime-root", type=Path)
    rq2_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq2_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    rq2_run.add_argument(
        "--stock-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src"),
    )
    rq2_run.add_argument("--advisor-command", default="extstats-advisor")
    rq2_validate = rq2_commands.add_parser("validate")
    rq2_validate.add_argument("artifact", type=Path)
    rq2_inspect = rq2_commands.add_parser("inspect")
    rq2_inspect.add_argument("artifact", type=Path)
    rq2_summary = validate_commands.add_parser(
        "rq2-summary", help="validate or derive the RQ2 cross-dataset summary"
    )
    rq2_summary_commands = rq2_summary.add_subparsers(dest="rq2_summary_command", required=True)
    rq2_summary_create = rq2_summary_commands.add_parser("create", aliases=["run"])
    rq2_summary_create.add_argument("artifacts", type=Path, nargs="+")
    rq2_summary_create.add_argument("--output", type=Path, required=True)
    rq2_summary_validate = rq2_summary_commands.add_parser("validate")
    rq2_summary_validate.add_argument("artifacts", type=Path, nargs="+")
    rq5_cost = validate_commands.add_parser(
        "rq5-cost", help="inventory or validate existing RQ5 cost evidence offline"
    )
    rq5_cost_commands = rq5_cost.add_subparsers(dest="rq5_cost_command", required=True)
    rq5_inventory = rq5_cost_commands.add_parser(
        "inventory", help="derive the existing-trace RQ5 cost inventory from tracked JSON"
    )
    rq5_inventory.add_argument("--output", type=Path, default=None)
    rq5_inventory_v2 = rq5_cost_commands.add_parser(
        "inventory-v2", help="derive the static-deployment-aware RQ5 cost inventory offline"
    )
    rq5_inventory_v2.add_argument("--output", type=Path, default=None)
    rq5_inventory_v3 = rq5_cost_commands.add_parser(
        "inventory-v3", help="derive the snapshot-footprint-aware RQ5 cost inventory offline"
    )
    rq5_inventory_v3.add_argument("--output", type=Path, default=None)
    rq5_validate = rq5_cost_commands.add_parser("validate")
    rq5_validate.add_argument("artifact", type=Path)
    rq5_static = rq5_cost_commands.add_parser(
        "static-deployment", help="protocol and stock-only RQ5 static deployment cost"
    )
    rq5_static_commands = rq5_static.add_subparsers(dest="rq5_static_command", required=True)
    rq5_static_preflight = rq5_static_commands.add_parser("preflight")
    rq5_static_preflight.add_argument("--output", type=Path, default=None)
    rq5_static_preflight.add_argument(
        "--stock-postgres-root", type=Path, default=Path("/home/wqts/projects/postgresql-src")
    )
    rq5_static_run = rq5_static_commands.add_parser("run")
    rq5_static_run.add_argument("--dataset", choices=RQ2_DATASETS, required=True)
    rq5_static_run.add_argument("--stock-dsn", required=True)
    rq5_static_run.add_argument("--output", type=Path, required=True)
    rq5_static_run.add_argument("--preflight", type=Path, default=None)
    rq5_static_run.add_argument("--data-root", type=Path, default=None)
    rq5_static_run.add_argument(
        "--stock-postgres-root", type=Path, default=Path("/home/wqts/projects/postgresql-src")
    )
    rq5_static_summarize = rq5_static_commands.add_parser("summarize")
    rq5_static_summarize.add_argument("--output", type=Path, default=None)
    rq5_static_summarize.add_argument("--preflight", type=Path, default=None)
    rq5_static_validate = rq5_static_commands.add_parser("validate")
    rq5_static_validate.add_argument("artifact", type=Path)
    rq5_snapshot = rq5_cost_commands.add_parser(
        "snapshot-footprint", help="measure or validate sealed AdvisorSnapshot footprint"
    )
    rq5_snapshot_commands = rq5_snapshot.add_subparsers(dest="rq5_snapshot_command", required=True)
    rq5_snapshot_preflight = rq5_snapshot_commands.add_parser("preflight")
    rq5_snapshot_preflight.add_argument("--output", type=Path, default=None)
    rq5_snapshot_preflight.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq5_snapshot_preflight.add_argument(
        "--stock-postgres-root", type=Path, default=Path("/home/wqts/projects/postgresql-src")
    )
    rq5_snapshot_preflight.add_argument("--data-root", type=Path, default=None)
    rq5_snapshot_run = rq5_snapshot_commands.add_parser("run")
    rq5_snapshot_run.add_argument("--dataset", choices=SNAPSHOT_FOOTPRINT_DATASETS, required=True)
    rq5_snapshot_run.add_argument("--stock-dsn", required=True)
    rq5_snapshot_run.add_argument("--output", type=Path, required=True)
    rq5_snapshot_run.add_argument("--preflight", type=Path, required=True)
    rq5_snapshot_run.add_argument("--advisor-root", type=Path, required=True)
    rq5_snapshot_run.add_argument("--stock-postgres-root", type=Path, required=True)
    rq5_snapshot_run.add_argument("--advisor-command", default="extstats-advisor")
    rq5_snapshot_run.add_argument("--data-root", type=Path, default=None)
    rq5_snapshot_summarize = rq5_snapshot_commands.add_parser("summarize")
    rq5_snapshot_summarize.add_argument("--preflight", type=Path, required=True)
    rq5_snapshot_summarize.add_argument("--output", type=Path, default=None)
    rq5_snapshot_validate = rq5_snapshot_commands.add_parser("validate")
    rq5_snapshot_validate.add_argument("artifact", type=Path)
    rq5_truth = rq5_cost_commands.add_parser(
        "production-exact-truth-cost",
        help="preregister or validate the Census13 production-exact truth-cost canary",
    )
    rq5_truth_commands = rq5_truth.add_subparsers(dest="rq5_truth_command", required=True)
    rq5_truth_preflight = rq5_truth_commands.add_parser("preflight")
    rq5_truth_preflight.add_argument("--output", type=Path, default=None)
    rq5_truth_preflight.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq5_truth_preflight.add_argument(
        "--stock-postgres-root", type=Path, default=Path("/home/wqts/projects/postgresql-src")
    )
    rq5_truth_preflight.add_argument("--data-root", type=Path, default=None)
    rq5_truth_run = rq5_truth_commands.add_parser("run")
    rq5_truth_run.add_argument("--stock-dsn", required=True)
    rq5_truth_run.add_argument("--output", type=Path, required=True)
    rq5_truth_run.add_argument("--preflight", type=Path, required=True)
    rq5_truth_run.add_argument("--advisor-root", type=Path, required=True)
    rq5_truth_run.add_argument("--stock-postgres-root", type=Path, required=True)
    rq5_truth_run.add_argument("--data-root", type=Path, default=None)
    rq5_truth_validate = rq5_truth_commands.add_parser("validate")
    rq5_truth_validate.add_argument("artifact", type=Path)
    rq4 = validate_commands.add_parser(
        "rq4-ablation", help="run or validate the RQ4 selection/evaluation harness"
    )
    rq4_commands = rq4.add_subparsers(dest="rq4_command", required=True)
    rq4_synthetic = rq4_commands.add_parser("synthetic", aliases=["run", "create"])
    rq4_synthetic.add_argument("--output", type=Path, required=True)
    rq4_synthetic.add_argument("--system-freeze", type=Path, default=DEFAULT_SYSTEM_FREEZE_PATH)
    rq4_validate = rq4_commands.add_parser("validate")
    rq4_validate.add_argument("artifact", type=Path)
    rq4_resolution = validate_commands.add_parser(
        "rq4-baseline-resolution", help="validate the resolved RQ4 baseline-definition contract"
    )
    rq4_resolution_commands = rq4_resolution.add_subparsers(
        dest="rq4_resolution_command", required=True
    )
    rq4_resolution_validate = rq4_resolution_commands.add_parser("validate")
    rq4_resolution_validate.add_argument("artifact", type=Path)
    rq4_smoke = validate_commands.add_parser(
        "rq4-real-backend-smoke", help="validate or inspect a real PostgreSQL RQ4 smoke artifact"
    )
    rq4_smoke_commands = rq4_smoke.add_subparsers(dest="rq4_smoke_command", required=True)
    rq4_smoke_validate = rq4_smoke_commands.add_parser("validate")
    rq4_smoke_validate.add_argument("artifact", type=Path)
    rq4_smoke_inspect = rq4_smoke_commands.add_parser("inspect")
    rq4_smoke_inspect.add_argument("artifact", type=Path)
    rq4_determinism = validate_commands.add_parser(
        "rq4-design-determinism", help="validate deterministic patched-planner replay evidence"
    )
    rq4_determinism_commands = rq4_determinism.add_subparsers(
        dest="rq4_determinism_command", required=True
    )
    rq4_determinism_validate = rq4_determinism_commands.add_parser("validate")
    rq4_determinism_validate.add_argument("artifact", type=Path)
    rq4_physical = validate_commands.add_parser(
        "rq4-stock-physical", help="validate controlled stock RQ4 realization evidence"
    )
    rq4_physical_commands = rq4_physical.add_subparsers(dest="rq4_physical_command", required=True)
    rq4_physical_validate = rq4_physical_commands.add_parser("validate")
    rq4_physical_validate.add_argument("artifact", type=Path)
    native_stability = validate_commands.add_parser(
        "native-analyze-stability",
        help="offline plan and validation for Native ANALYZE Stability v1",
    )
    native_stability_commands = native_stability.add_subparsers(
        dest="native_stability_command", required=True
    )
    native_stability_dry_run = native_stability_commands.add_parser(
        "dry-run", help="construct an offline execution plan without opening PostgreSQL"
    )
    native_stability_dry_run.add_argument(
        "--protocol", type=Path, default=Path(NATIVE_STABILITY_PROTOCOL_PATH)
    )
    native_stability_dry_run.add_argument("--invocation-id", required=True)
    native_stability_dry_run.add_argument("--output-root", type=Path, required=True)
    native_stability_validate = native_stability_commands.add_parser(
        "validate", help="validate an offline Native ANALYZE invocation artifact"
    )
    native_stability_validate.add_argument("artifact", type=Path)
    native_stability_validate.add_argument("--protocol-digest", default=None)
    rq4_formal = validate_commands.add_parser(
        "rq4-forest10-fixed-k", help="run or validate the Forest10 formal RQ4 fixed-k canary"
    )
    rq4_formal_commands = rq4_formal.add_subparsers(dest="rq4_formal_command", required=True)
    rq4_formal_run = rq4_formal_commands.add_parser("run", aliases=["create"])
    rq4_formal_run.add_argument("--stock-dsn", required=True)
    rq4_formal_run.add_argument("--patched-dsn", required=True)
    rq4_formal_run.add_argument("--output", type=Path, required=True)
    rq4_formal_run.add_argument("--data-root", type=Path)
    rq4_formal_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq4_formal_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    rq4_formal_validate = rq4_formal_commands.add_parser("validate")
    rq4_formal_validate.add_argument("artifact", type=Path)
    rq4_v2 = validate_commands.add_parser(
        "rq4-fixed-k-v2", help="run or validate the generic Census13/Power7/DMV11 RQ4 v2 campaign"
    )
    rq4_v2_commands = rq4_v2.add_subparsers(dest="rq4_v2_command", required=True)
    rq4_v2_preflight = rq4_v2_commands.add_parser("preflight")
    rq4_v2_preflight.add_argument("--dataset", choices=RQ4_V2_DATASETS, required=True)
    rq4_v2_run = rq4_v2_commands.add_parser("run", aliases=["create"])
    rq4_v2_run.add_argument("--dataset", choices=RQ4_V2_DATASETS, required=True)
    rq4_v2_run.add_argument("--stock-dsn", required=True)
    rq4_v2_run.add_argument("--patched-dsn", required=True)
    rq4_v2_run.add_argument("--output", type=Path, required=True)
    rq4_v2_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq4_v2_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    rq4_v2_run.add_argument(
        "--stock-postgres-root", type=Path, default=Path("/home/wqts/projects/postgresql-src")
    )
    rq4_v2_run.add_argument("--system-freeze", type=Path, default=DEFAULT_SYSTEM_FREEZE_V2_PATH)
    rq4_v2_validate = rq4_v2_commands.add_parser("validate")
    rq4_v2_validate.add_argument("artifact", type=Path)
    rq4_ks = validate_commands.add_parser(
        "rq4-ks-sensitivity", help="preregister and validate RQ4 screening-width sensitivity"
    )
    rq4_ks_commands = rq4_ks.add_subparsers(dest="rq4_ks_command", required=True)
    rq4_ks_preflight = rq4_ks_commands.add_parser("preflight")
    rq4_ks_preflight.add_argument("--dataset", choices=RQ4_KS_DATASETS, required=True)
    rq4_ks_preflight.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq4_ks_smoke = rq4_ks_commands.add_parser("smoke")
    rq4_ks_smoke.add_argument("--output", type=Path, required=True)
    rq4_ks_smoke.add_argument("--candidate-count", type=int, default=6)
    rq4_ks_smoke.add_argument(
        "--patched-dsn",
        help="optional disposable patched PostgreSQL DSN for the bounded live smoke",
    )
    rq4_ks_live = rq4_ks_commands.add_parser(
        "live-smoke", help="run the distinct frozen-v2 bounded patched-sandbox smoke"
    )
    rq4_ks_live.add_argument("--patched-dsn", required=True)
    rq4_ks_live.add_argument("--output", type=Path, required=True)
    rq4_ks_live.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq4_ks_live.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    rq4_ks_run = rq4_ks_commands.add_parser(
        "run", aliases=["create"], help="run one formal patched-sandbox K_s child"
    )
    rq4_ks_run.add_argument("--dataset", choices=RQ4_KS_DATASETS, required=True)
    rq4_ks_run.add_argument("--patched-dsn", required=True)
    rq4_ks_run.add_argument("--output", type=Path, required=True)
    rq4_ks_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    rq4_ks_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    rq4_ks_validate = rq4_ks_commands.add_parser("validate")
    rq4_ks_validate.add_argument("artifact", type=Path)
    rq4_ks_summarize = rq4_ks_commands.add_parser(
        "summarize", help="aggregate the three completed K_s sensitivity children offline"
    )
    rq4_ks_summarize.add_argument("--output", type=Path, default=None)
    rebind = validate_commands.add_parser(
        "rq1-rebind", help="canonicalize the Census13 RQ1 artifact onto audited external truth"
    )
    rebind_commands = rebind.add_subparsers(dest="rebind_command", required=True)
    rebind_run = rebind_commands.add_parser("run", aliases=["create"])
    rebind_run.add_argument("--historical-artifact", type=Path, required=True)
    rebind_run.add_argument("--observations", type=Path, required=True)
    rebind_run.add_argument("--equivalence-artifact", type=Path, required=True)
    rebind_run.add_argument("--policy", type=Path, required=True)
    rebind_run.add_argument("--output", type=Path, required=True)
    rebind_run.add_argument("--system-freeze", type=Path, default=DEFAULT_SYSTEM_FREEZE_PATH)
    rebind_validate = rebind_commands.add_parser("validate")
    rebind_validate.add_argument("artifact", type=Path)
    singleton = validate_commands.add_parser(
        "singleton-equivalence",
        help="audit incidence-incremental singleton profiling against the old reference",
    )
    singleton_commands = singleton.add_subparsers(dest="singleton_command", required=True)
    singleton_preflight = singleton_commands.add_parser("preflight")
    singleton_preflight.add_argument("--output", type=Path, required=True)
    singleton_preflight.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    singleton_run = singleton_commands.add_parser("run", aliases=["create"])
    singleton_run.add_argument("--dataset", required=True)
    singleton_run.add_argument("--source-run", type=Path, required=True)
    singleton_run.add_argument("--patched-dsn", required=True)
    singleton_run.add_argument("--output", type=Path, required=True)
    singleton_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    singleton_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    singleton_validate = singleton_commands.add_parser("validate")
    singleton_validate.add_argument("artifact", type=Path)
    singleton_inspect = singleton_commands.add_parser("inspect")
    singleton_inspect.add_argument("artifact", type=Path)
    historical = singleton_commands.add_parser(
        "historical-incremental",
        help="run or validate current incremental profiling against historical semantic oracles",
    )
    historical_commands = historical.add_subparsers(
        dest="historical_incremental_command", required=True
    )
    historical_run = historical_commands.add_parser("run", aliases=["create"])
    historical_run.add_argument("--dataset", required=True)
    historical_run.add_argument("--source-run", type=Path, required=True)
    historical_run.add_argument("--patched-dsn", required=True)
    historical_run.add_argument("--output", type=Path, required=True)
    historical_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    historical_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    historical_validate = historical_commands.add_parser("validate")
    historical_validate.add_argument("artifact", type=Path)
    historical_inspect = historical_commands.add_parser("inspect")
    historical_inspect.add_argument("artifact", type=Path)
    hardening = validate_commands.add_parser(
        "incremental-search-hardening",
        help="run or validate bounded live v2 incremental-search hardening evidence",
    )
    hardening_commands = hardening.add_subparsers(
        dest="incremental_hardening_command", required=True
    )
    hardening_run = hardening_commands.add_parser("run", aliases=["create"])
    hardening_run.add_argument("--patched-dsn", required=True)
    hardening_run.add_argument("--output", type=Path, required=True)
    hardening_run.add_argument("--source-run", type=Path, default=DEFAULT_SOURCE_RUN)
    hardening_run.add_argument(
        "--advisor-root", type=Path, default=Path("/home/wqts/projects/extstats-advisor")
    )
    hardening_run.add_argument(
        "--patched-postgres-root",
        type=Path,
        default=Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    hardening_validate = hardening_commands.add_parser("validate")
    hardening_validate.add_argument("artifact", type=Path)
    hardening_inspect = hardening_commands.add_parser("inspect")
    hardening_inspect.add_argument("artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "oid-order-sensitivity":
        root = Path(__file__).resolve().parents[2]
        from .pins import verify_research_repository

        if args.oid_command == "run":
            preflight_path = args.preflight
            if not preflight_path.is_absolute():
                preflight_path = root / preflight_path
            producer_sha = verify_research_repository(
                root, allowed_untracked_paths=(preflight_path,)
            )["research_commit_sha"]
        else:
            producer_sha = verify_research_repository(root)["research_commit_sha"]
        if args.oid_command == "preflight-create":
            output = args.output
            value = build_oid_preflight(
                root=root, data_root=args.data_root, producer_sha=producer_sha
            )
            write_json(output, value)
            print(
                json.dumps(
                    {
                        "status": "written",
                        "output": str(output),
                        "semantic_digest": value["semantic_digest"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.oid_command == "run":
            result = run_oid_formal(
                root=root,
                preflight_path=args.preflight,
                data_root=args.data_root,
                stock_dsn=args.stock_dsn,
                planner_dsn=args.planner_dsn,
                advisor_root=args.advisor_root,
                patched_postgres_root=args.patched_postgres_root,
                stock_postgres_root=args.stock_postgres_root,
                producer_sha=producer_sha,
                invocation_id=args.invocation_id,
                failure_output=args.failure_output,
            )
            print(json.dumps(result, sort_keys=True, indent=2, default=str))
            return 0
        if args.oid_command == "validate":
            value = read_json(args.artifact)
            if args.kind == "protocol":
                result = validate_oid_protocol(value, root=root)
            elif args.kind == "preflight":
                result = validate_oid_preflight(value, root=root)
            else:
                result = validate_oid_result(args.artifact, root=root)
            print(json.dumps(result, sort_keys=True))
            return 0
    if args.command == "rq1-generalization":
        root = Path(__file__).resolve().parents[2]
        if args.rq1g_dataset_command == "preflight-create":
            if args.rq1g_live_command == "power7":
                value = write_power7_rq1b_preflight(research_root=root, output=args.output)
            elif args.rq1g_live_command == "forest10":
                value = write_forest10_rq1b_preflight(research_root=root, output=args.output)
            elif args.rq1g_live_command == "dmv11":
                value = write_dmv11_rq1b_preflight(research_root=root, output=args.output)
            elif args.rq1g_live_command == "census13":
                value = write_census13_rq1b_preflight(research_root=root, output=args.output)
            else:  # pragma: no cover - argparse constrains the dataset names
                raise ValueError(f"unsupported RQ1b dataset: {args.rq1g_live_command}")
            result = {
                "status": "written",
                "format_version": value["format_version"],
                "semantic_digest": value["semantic_digest"],
                "output": str(
                    args.output
                    or root
                    / f"experiments/arecel-{args.rq1g_live_command}/"
                    "rq1-workload-generalization-v1/rq1b-preflight-v1.json"
                ),
            }
        elif args.rq1g_dataset_command == "run":
            if args.rq1g_live_command == "power7":
                runner = run_power7_rq1b_formal
            elif args.rq1g_live_command == "forest10":
                runner = run_forest10_rq1b_formal
            elif args.rq1g_live_command == "dmv11":
                runner = run_dmv11_rq1b_formal
            else:
                runner = run_census13_rq1b_formal
            result = runner(
                research_root=root,
                preflight_path=args.preflight,
                stock_dsn=args.stock_dsn,
                planner_dsn=args.planner_dsn,
                advisor_root=args.advisor_root,
                patched_postgres_root=args.patched_postgres_root,
                stock_postgres_root=args.stock_postgres_root,
                data_root=args.data_root,
                advisor_command=args.advisor_command,
                runtime_root=args.runtime_root,
            )
        else:
            raise ValueError(
                f"unsupported RQ1b {args.rq1g_live_command} command: {args.rq1g_dataset_command}"
            )
        print(json.dumps(result, sort_keys=True, indent=2, default=str))
        return 0
    if args.command == "postgres-lab":
        command = args.postgres_lab_command
        if command == "env":
            print(env_exports(args.role), end="")
            return 0
        if command == "doctor":
            result = doctor(args.role)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0 if result["ok"] else 1
        if command == "build":
            result = {role: build_role(role, jobs=args.jobs) for role in roles_for(args.role)}
        elif command == "init":
            result = {role: init_role(role) for role in roles_for(args.role)}
        elif command == "start":
            result = {role: start_role(role) for role in roles_for(args.role)}
        elif command == "stop":
            result = {role: stop_role(role) for role in roles_for(args.role)}
        elif command == "status":
            result = {role: status_role(role) for role in roles_for(args.role)}
        elif command == "destroy":
            result = {role: destroy_role(role) for role in roles_for(args.role)}
        elif command == "recreate":
            result = {role: recreate_role(role, jobs=args.jobs) for role in roles_for(args.role)}
        elif command == "reinit":
            result = {role: reinit_role(role) for role in roles_for(args.role)}
        elif command == "inspect":
            result = inspect(args.role)
        else:
            raise ValueError(f"unsupported postgres-lab command: {command}")
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
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
        if args.validate_command == "paper-spec":
            result = validate_paper_spec(load_paper_spec(args.spec))
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "top-k-screening-protocol":
            result = validate_top_k_screening_protocol(load_top_k_screening_protocol(args.path))
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "benchmark-truth-policy":
            result = validate_truth_policy(
                json.loads(args.path.read_text(encoding="utf-8")),
                research_root=Path(__file__).resolve().parents[2],
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "system-freeze":
            result = validate_system_freeze(load_system_freeze(args.path))
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "system-freeze-v2":
            result = validate_system_freeze_v2(load_system_freeze_v2(args.path))
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "system-freeze-v2-readiness":
            result = validate_readiness_evidence(args.path)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq1-canary":
            if args.rq1_command in {"run", "create"}:
                result = run_census13_canary(
                    stock_dsn=args.stock_dsn,
                    patched_dsn=args.patched_dsn,
                    output=args.output,
                    data_root=args.data_root,
                    truth_artifact=args.truth_artifact,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                    advisor_command=args.advisor_command,
                )
            elif args.rq1_command == "validate":
                result = validate_rq1_file(args.artifact)
            else:
                result = inspect_rq1_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq1-matched":
            if args.matched_command in {"run", "create"}:
                result = run_rq1_matched(
                    dataset_id=args.dataset_id,
                    stock_dsn=args.stock_dsn,
                    patched_dsn=args.patched_dsn,
                    output=args.output,
                    data_root=args.data_root,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                    advisor_command=args.advisor_command,
                )
            elif args.matched_command == "validate":
                result = validate_rq1_file(args.artifact)
            else:
                result = inspect_rq1_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq1-summary":
            root = Path(__file__).resolve().parents[2]
            if args.summary_command in {"create", "run"}:
                result = build_cross_dataset_summary(default_source_paths(root), args.output)
            else:
                result = validate_cross_dataset_summary(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq1b-synthesis":
            root = Path(__file__).resolve().parents[2]
            if args.synthesis_command in {"create", "run"}:
                value = build_cross_dataset_synthesis(root, args.output)
                result = {
                    "status": "written",
                    "format_version": value["format_version"],
                    "semantic_digest": value["semantic_digest"],
                    "output": str(args.output),
                }
            else:
                result = validate_cross_dataset_synthesis(args.artifact, root)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq1-workload-generalization":
            root = Path(__file__).resolve().parents[2]
            if args.rq1b_command == "protocol":
                result = validate_rq1b_protocol(args.path)
            elif args.rq1b_command == "protocol-v2":
                result = validate_rq1b_protocol_v2(args.path)
            elif args.rq1b_command == "truth-policy":
                result = validate_rq1b_truth_policy(args.path, root)
            elif getattr(args, "rq1b_source_command", None) == "create":
                value = write_rq1b_source_audit(root, output=args.output, data_root=args.data_root)
                result = {
                    "status": "written",
                    "format_version": value["format_version"],
                    "semantic_digest": value["semantic_digest"],
                    "output": str(args.output or (root / RQ1B_SOURCE_AUDIT_PATH)),
                }
            elif args.rq1b_command == "source-audit-v2" and args.rq1b_source_v2_command == "create":
                output = args.output or (root / RQ1B_SOURCE_AUDIT_V2_PATH)
                value = build_rq1b_source_audit_v2(root, data_root=args.data_root)
                write_json(output, value)
                result = {
                    "status": "written",
                    "format_version": value["format_version"],
                    "semantic_digest": value["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq1b_command == "source-audit-v2":
                result = validate_rq1b_source_audit_v2(args.artifact, root)
            elif args.rq1b_command == "strict-unseen" and args.rq1b_strict_command == "create":
                output = args.output or (root / RQ1B_STRICT_UNSEEN_PATH)
                value = build_rq1b_strict_unseen(root, data_root=args.data_root)
                write_json(output, value)
                result = {
                    "status": "written",
                    "format_version": value["format_version"],
                    "semantic_digest": value["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq1b_command == "strict-unseen":
                result = validate_rq1b_strict_unseen(args.artifact, root)
            else:
                result = validate_rq1b_source_audit(args.artifact, root)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq1-generalization":
            root = Path(__file__).resolve().parents[2]
            spec = {
                "power7": POWER7_SPEC,
                "forest10": FOREST10_SPEC,
                "dmv11": DMV11_SPEC,
                "census13": CENSUS13_SPEC,
            }[args.dataset]
            if args.rq1g_command == "design":
                result = validate_rq1b_design_artifact_generic(read_json(args.artifact), _spec=spec)
            elif args.rq1g_command == "preflight":
                result = validate_rq1b_preflight(
                    read_json(args.artifact), research_root=root, spec=spec
                )
            else:
                result = validate_rq1b_result(
                    read_json(args.artifact), research_root=root, spec=spec
                )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq2-transfer":
            if args.rq2_command == "preflight":
                result = preflight_rq2(args.dataset)
            elif args.rq2_command in {"run", "create"}:
                result = run_rq2_child(
                    args.dataset,
                    stock_dsn=args.stock_dsn,
                    patched_dsn=args.patched_dsn,
                    output=args.output,
                    data_root=args.data_root,
                    runtime_root=args.runtime_root,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                    stock_postgres_root=args.stock_postgres_root,
                    advisor_command=args.advisor_command,
                )
            elif args.rq2_command == "validate":
                result = validate_rq2_artifact(args.artifact)
            else:
                result = inspect_rq2_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq2-summary":
            result = validate_rq2_cross_dataset(args.artifacts)
            if args.rq2_summary_command in {"create", "run"}:
                result["semantic_digest"] = semantic_digest(result)
                write_json(args.output, result)
                result = {"status": "written", "output": str(args.output), **result}
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq5-cost":
            root = Path(__file__).resolve().parents[2]
            if args.rq5_cost_command == "inventory":
                output = args.output or default_inventory_path(root)
                artifact = write_inventory(root, output)
                result = {
                    "status": "written",
                    "format_version": artifact["format_version"],
                    "experiment_id": artifact["experiment_id"],
                    "semantic_digest": artifact["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq5_cost_command == "inventory-v2":
                output = args.output or default_inventory_v2_path(root)
                artifact = write_inventory_v2(root, output)
                result = {
                    "status": "written",
                    "format_version": artifact["format_version"],
                    "experiment_id": artifact["experiment_id"],
                    "semantic_digest": artifact["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq5_cost_command == "inventory-v3":
                output = args.output or default_inventory_v3_path(root)
                artifact = write_inventory_v3(root, output)
                result = {
                    "status": "written",
                    "format_version": artifact["format_version"],
                    "experiment_id": artifact["experiment_id"],
                    "semantic_digest": artifact["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq5_cost_command == "validate":
                result = validate_inventory(args.artifact, root)
            elif args.rq5_cost_command == "snapshot-footprint":
                if args.rq5_snapshot_command == "preflight":
                    output = args.output or default_snapshot_footprint_preflight_path(root)
                    artifact = build_snapshot_footprint_preflight(
                        root,
                        advisor_root=args.advisor_root,
                        stock_postgres_root=args.stock_postgres_root,
                        output=output,
                        data_root=args.data_root,
                    )
                    result = {
                        "status": "written",
                        "format_version": artifact["format_version"],
                        "semantic_digest": artifact["semantic_digest"],
                        "output": str(output),
                    }
                elif args.rq5_snapshot_command == "run":
                    artifact = run_snapshot_footprint(
                        args.dataset,
                        research_root=root,
                        stock_dsn=args.stock_dsn,
                        output=args.output,
                        preflight=args.preflight,
                        advisor_root=args.advisor_root,
                        stock_postgres_root=args.stock_postgres_root,
                        advisor_command=args.advisor_command,
                        data_root=args.data_root,
                    )
                    result = {
                        "status": artifact["status"],
                        "format_version": artifact["format_version"],
                        "dataset_id": artifact["dataset_id"],
                        "semantic_digest": artifact["semantic_digest"],
                        "output": str(args.output),
                    }
                elif args.rq5_snapshot_command == "summarize":
                    output = args.output or default_snapshot_footprint_path(root)
                    artifact = summarize_snapshot_footprint(
                        root, output=output, preflight=args.preflight
                    )
                    result = {
                        "status": "written",
                        "format_version": artifact["format_version"],
                        "semantic_digest": artifact["semantic_digest"],
                        "output": str(output),
                    }
                elif args.rq5_snapshot_command == "validate":
                    value = read_json(args.artifact)
                    if value.get("format_version") == "rq5-snapshot-footprint-protocol-v1":
                        result = validate_snapshot_footprint_protocol(args.artifact)
                    elif value.get("format_version") == "rq5-snapshot-footprint-preflight-v1":
                        result = validate_snapshot_footprint_preflight(args.artifact, root)
                    elif value.get("format_version") == "rq5-snapshot-footprint-dataset-v1":
                        result = validate_snapshot_footprint_raw(value)
                else:
                    result = validate_snapshot_footprint_artifact(args.artifact, root)
            elif args.rq5_cost_command == "production-exact-truth-cost":
                if args.rq5_truth_command == "preflight":
                    output = args.output or default_production_exact_truth_preflight_path(root)
                    artifact = build_production_exact_truth_preflight(
                        root,
                        advisor_root=args.advisor_root,
                        stock_postgres_root=args.stock_postgres_root,
                        output=output,
                        data_root=args.data_root,
                    )
                    result = {
                        "status": "written",
                        "format_version": artifact["format_version"],
                        "semantic_digest": artifact["semantic_digest"],
                        "output": str(output),
                    }
                elif args.rq5_truth_command == "run":
                    artifact = run_production_exact_truth_canary(
                        research_root=root,
                        stock_dsn=args.stock_dsn,
                        preflight=args.preflight,
                        output=args.output,
                        advisor_root=args.advisor_root,
                        stock_postgres_root=args.stock_postgres_root,
                        data_root=args.data_root,
                    )
                    result = {
                        "status": artifact["status"],
                        "format_version": artifact["format_version"],
                        "semantic_digest": artifact["semantic_digest"],
                        "output": str(args.output),
                    }
                else:
                    value = read_json(args.artifact)
                    if value.get("format_version") == PRODUCTION_EXACT_TRUTH_PROTOCOL_FORMAT:
                        result = validate_production_exact_truth_protocol(args.artifact)
                    elif value.get("format_version") == PRODUCTION_EXACT_TRUTH_PREFLIGHT_FORMAT:
                        result = validate_production_exact_truth_preflight_file(args.artifact, root)
                    else:
                        result = validate_production_exact_truth_artifact(value)
            elif args.rq5_static_command == "preflight":
                output = args.output or default_static_deployment_preflight_path(root)
                artifact = build_static_deployment_preflight(
                    root, stock_postgres_root=args.stock_postgres_root, output=output
                )
                result = {
                    "status": "written",
                    "format_version": artifact["format_version"],
                    "semantic_digest": artifact["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq5_static_command == "run":
                result = run_static_deployment(
                    args.dataset,
                    research_root=root,
                    stock_dsn=args.stock_dsn,
                    output=args.output,
                    stock_postgres_root=args.stock_postgres_root,
                    preflight=args.preflight,
                    data_root=args.data_root,
                )
                result = {
                    "status": result["status"],
                    "format_version": result["format_version"],
                    "dataset_id": result["dataset_id"],
                    "semantic_digest": result["semantic_digest"],
                    "output": str(args.output),
                }
            elif args.rq5_static_command == "summarize":
                output = args.output or default_static_deployment_path(root)
                artifact = summarize_static_deployment(
                    root, output=output, preflight=args.preflight
                )
                result = {
                    "status": "written",
                    "format_version": artifact["format_version"],
                    "semantic_digest": artifact["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq5_static_command == "validate":
                name = args.artifact.name
                if name.endswith("rq5-static-deployment-cost-protocol-v1.json"):
                    result = validate_static_deployment_protocol(args.artifact)
                elif name.endswith("rq5-static-deployment-cost-preflight-v1.json"):
                    result = validate_static_deployment_preflight(args.artifact, root)
                elif name.endswith("rq5-static-deployment-cost-v1.json"):
                    result = validate_static_deployment_artifact(args.artifact, root)
                else:
                    result = validate_static_deployment_artifact(args.artifact, root)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-ablation":
            if args.rq4_command in {"synthetic", "run", "create"}:
                root = Path(__file__).resolve().parents[2]
                result = write_synthetic_rq4_artifact(
                    args.output,
                    system_freeze_path=args.system_freeze,
                    research_commit_sha=current_research_commit(root),
                )
                result = {
                    "status": result["status"],
                    "format_version": result["format_version"],
                    "experiment_id": result["experiment_id"],
                    "semantic_digest": result["semantic_digest"],
                    "output": str(args.output),
                }
            else:
                result = validate_rq4_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-baseline-resolution":
            result = validate_resolution_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-real-backend-smoke":
            if args.rq4_smoke_command == "validate":
                result = validate_real_backend_smoke(args.artifact)
            else:
                result = inspect_real_backend_smoke(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-design-determinism":
            result = validate_design_determinism_smoke(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-stock-physical":
            result = validate_shared_stock_realization(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "native-analyze-stability":
            root = Path(__file__).resolve().parents[2]
            if args.native_stability_command == "dry-run":
                protocol_path = (
                    args.protocol if args.protocol.is_absolute() else root / args.protocol
                )
                protocol = (
                    load_native_stability_protocol(root)
                    if protocol_path == root / NATIVE_STABILITY_PROTOCOL_PATH
                    else read_json(protocol_path)
                )
                result = native_stability_dry_run_plan(
                    protocol, invocation_id=args.invocation_id, output_root=args.output_root
                )
            else:
                artifact = read_json(args.artifact)
                if artifact.get("format_version") == "native-analyze-stability-readiness-review-v2":
                    result = validate_native_stability_readiness_v2(artifact, root)
                else:
                    result = validate_native_stability_invocation(
                        args.artifact, expected_protocol_digest=args.protocol_digest
                    )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-fixed-k-v2":
            root = Path(__file__).resolve().parents[2]
            if args.rq4_v2_command == "preflight":
                result = build_rq4_v2_preflight(
                    args.dataset, root, Path("/home/wqts/projects/extstats-advisor")
                )
            elif args.rq4_v2_command in {"run", "create"}:
                result = run_formal_rq4_v2(
                    args.dataset,
                    stock_dsn=args.stock_dsn,
                    patched_dsn=args.patched_dsn,
                    output=args.output,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                    stock_postgres_root=args.stock_postgres_root,
                    research_root=root,
                    system_freeze_path=args.system_freeze,
                )
                result = {
                    "status": result["status"],
                    "format_version": result["format_version"],
                    "experiment_id": result["experiment_id"],
                    "semantic_digest": result["semantic_digest"],
                    "output": str(args.output),
                }
            else:
                name = args.artifact.name
                if name.endswith("rq4-design-evaluation-v2.json"):
                    result = validate_v2_design_artifact(args.artifact)
                elif name.endswith("rq4-design-determinism-v2.json.gz"):
                    result = validate_v2_determinism(args.artifact)
                else:
                    result = validate_v2_summary(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-ks-sensitivity":
            root = Path(__file__).resolve().parents[2]
            if args.rq4_ks_command == "preflight":
                result = build_rq4_ks_preflight(args.dataset, root, args.advisor_root)
            elif args.rq4_ks_command == "summarize":
                output = args.output or default_rq4_ks_summary_path(root)
                artifact = write_cross_dataset_summary(root, output)
                result = {
                    "status": "written",
                    "format_version": artifact["format_version"],
                    "semantic_digest": artifact["semantic_digest"],
                    "output": str(output),
                }
            elif args.rq4_ks_command == "smoke":
                result = build_smoke_artifact(
                    research_root=root,
                    producer_research_sha=current_research_commit(root),
                    candidate_count=args.candidate_count,
                    patched_dsn=args.patched_dsn,
                )
                write_json(args.output, result)
                result = {
                    "status": "written",
                    "format_version": result["format_version"],
                    "semantic_digest": result["semantic_digest"],
                    "output": str(args.output),
                }
            elif args.rq4_ks_command == "live-smoke":
                result = build_live_smoke_artifact(
                    research_root=root,
                    producer_research_sha=current_research_commit(root),
                    patched_dsn=args.patched_dsn,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                )
                write_json(args.output, result)
                result = {
                    "status": "written",
                    "format_version": result["format_version"],
                    "semantic_digest": result["semantic_digest"],
                    "output": str(args.output),
                }
            elif args.rq4_ks_command in {"run", "create"}:
                result = run_formal_ks_sensitivity(
                    dataset_id=args.dataset,
                    research_root=root,
                    patched_dsn=args.patched_dsn,
                    output=args.output,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                )
                result = {
                    "status": result["status"],
                    "format_version": result["format_version"],
                    "experiment_id": result["experiment_id"],
                    "semantic_digest": result["semantic_digest"],
                    "output": str(args.output),
                }
            else:
                if args.artifact.name.endswith("rq4-ks-sensitivity-cross-dataset-summary-v1.json"):
                    result = validate_rq4_ks_summary(args.artifact, research_root=root)
                elif args.artifact.name.endswith("rq4-ks-sensitivity-preflight-v1.json"):
                    result = validate_rq4_ks_preflight(args.artifact)
                elif args.artifact.name.endswith("rq4-ks-sensitivity-live-smoke-v1.json"):
                    result = validate_live_smoke_artifact(args.artifact)
                elif args.artifact.name.endswith("rq4-ks-sensitivity-v1.json"):
                    result = validate_formal_ks_sensitivity(args.artifact, research_root=root)
                else:
                    result = validate_smoke_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq4-forest10-fixed-k":
            if args.rq4_formal_command in {"run", "create"}:
                result = run_forest10_fixed_k(
                    stock_dsn=args.stock_dsn,
                    patched_dsn=args.patched_dsn,
                    output=args.output,
                    data_root=args.data_root,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                )
                result = {
                    "status": result["status"],
                    "format_version": result["format_version"],
                    "experiment_id": result["experiment_id"],
                    "semantic_digest": result["semantic_digest"],
                    "output": str(args.output),
                }
            else:
                result = validate_forest10_fixed_k_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "rq1-rebind":
            if args.rebind_command in {"run", "create"}:
                result = canonicalize_census13(
                    historical_artifact=args.historical_artifact,
                    observations_path=args.observations,
                    equivalence_artifact=args.equivalence_artifact,
                    policy_path=args.policy,
                    system_freeze_path=args.system_freeze,
                    output=args.output,
                )
            else:
                result = validate_rebound_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "incremental-search-hardening":
            if args.incremental_hardening_command in {"run", "create"}:
                result = run_hardening_smoke(
                    advisor_root=args.advisor_root,
                    patched_dsn=args.patched_dsn,
                    patched_postgres_root=args.patched_postgres_root,
                    output=args.output,
                    source_run=args.source_run,
                )
            elif args.incremental_hardening_command == "validate":
                result = validate_incremental_hardening(args.artifact)
            else:
                result = inspect_incremental_hardening(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "singleton-equivalence":
            if args.singleton_command == "preflight":
                result = write_singleton_equivalence_preflight(
                    args.output, advisor_root=args.advisor_root
                )
            elif args.singleton_command in {"run", "create"}:
                result = run_equivalence(
                    args.dataset,
                    args.source_run,
                    patched_dsn=args.patched_dsn,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                    output=args.output,
                )
            elif args.singleton_command == "validate":
                result = validate_singleton_equivalence(args.artifact)
            elif args.singleton_command == "historical-incremental":
                if args.historical_incremental_command in {"run", "create"}:
                    result = run_historical_incremental_equivalence(
                        args.dataset,
                        args.source_run,
                        patched_dsn=args.patched_dsn,
                        advisor_root=args.advisor_root,
                        patched_postgres_root=args.patched_postgres_root,
                        output=args.output,
                    )
                elif args.historical_incremental_command == "validate":
                    result = validate_historical_incremental_artifact(args.artifact)
                else:
                    result = inspect_historical_incremental_artifact(args.artifact)
            else:
                result = inspect_singleton_equivalence(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "build-sanity":
            if args.build_sanity_command in {"run", "create"}:
                result = run_build_sanity(
                    stock_dsn=args.stock_dsn,
                    patched_dsn=args.patched_dsn,
                    output=args.output,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                    formal_experiment=args.formal,
                )
            elif args.build_sanity_command == "validate":
                result = validate_build_sanity_file(args.artifact)
            else:
                result = inspect_build_sanity_artifact(args.artifact)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if args.validate_command == "hypothetical-fidelity":
            if args.fidelity_command in {"run", "create"}:
                result = run_synthetic_fidelity(
                    dsn=args.dsn,
                    output=args.output,
                    advisor_root=args.advisor_root,
                    patched_postgres_root=args.patched_postgres_root,
                    formal_experiment=args.formal,
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
    run_kwargs = {
        "production_dsn": args.production_dsn,
        "planner_dsn": args.planner_dsn,
        "advisor_root": args.advisor_root,
        "patched_postgres_root": args.patched_postgres_root,
        "output_root": args.output_root,
        "sample_rows": args.sample_rows,
        "sample_seed": args.sample_seed,
        "statistics_target": args.statistics_target,
        "candidate_limit": args.candidate_limit,
        "search_wall_clock_seconds": search_wall_clock_seconds,
        "data_root": args.data_root,
        "reset_disposable": args.reset_disposable,
        "advisor_command": args.advisor_command,
    }
    if args.dataset_id == census13.BENCHMARK_ID:
        run_kwargs.update(
            truth_source=args.truth_source,
            authoritative_observations=args.authoritative_observations,
        )
    result = runner(**run_kwargs)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0
