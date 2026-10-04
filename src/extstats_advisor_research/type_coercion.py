"""Stock-PostgreSQL diagnostic for Census numeric literal coercion."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_REPOSITORY, FROZEN_ADVISOR_SHA, FROZEN_RESEARCH_REPOSITORY
from .baseline_gap import diagnostic_query_sql, relation_sql, summary_for
from .datasets import census13
from .paper_baseline import PAPER_COLUMNS, _frozen_advisor_qerror, _load_source_queries
from .provenance import reject_credentials, semantic_digest, sha256_file, write_json

FORMAT_VERSION = "arecel-census13-type-coercion-v1"
NUMERIC_COLUMNS = frozenset(
    {"age", "education_num", "capital_gain", "capital_loss", "hours_per_week"}
)
TAIL_INDICES = (5803, 7775, 8202, 7718, 7098, 8766, 5549, 4272, 2620, 1940)
_LITERAL = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_COLUMN_PREDICATE = re.compile(
    r'"(?P<column>[^"]+)"\s+(?P<operator>BETWEEN|<=|>=|=|<|>)\s+', re.IGNORECASE
)

PAPER_NUMERIC_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name, "DOUBLE PRECISION" if name in NUMERIC_COLUMNS else "VARCHAR(64)")
    for name, _ in PAPER_COLUMNS
)
ADVISOR_NUMERIC_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name, "BIGINT" if name in NUMERIC_COLUMNS else "VARCHAR(64)") for name, _ in PAPER_COLUMNS
)
HYBRID_H1_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name, "DOUBLE PRECISION" if name in NUMERIC_COLUMNS else "TEXT") for name, _ in PAPER_COLUMNS
)
HYBRID_H2_COLUMNS = ADVISOR_NUMERIC_COLUMNS


def classify_numeric_literal(literal: str) -> str:
    token = literal.strip()
    if "e" in token.lower():
        return "scientific"
    if "." in token:
        return "decimal"
    return "integer"


def numeric_predicates(sql: str) -> list[dict[str, str]]:
    where = sql.split(" WHERE ", 1)[1].rstrip("; ")
    matches = list(_COLUMN_PREDICATE.finditer(where))
    result: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        column = match.group("column")
        if column not in NUMERIC_COLUMNS:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(where)
        clause = where[match.start() : end].strip()
        clause = re.sub(r"\s+AND\s*$", "", clause, flags=re.IGNORECASE)
        values = re.findall(_LITERAL, clause[match.end() - match.start() :])
        values = values[:2] if match.group("operator").upper() == "BETWEEN" else values[:1]
        for literal in values:
            result.append(
                {
                    "column": column,
                    "operator": match.group("operator").upper(),
                    "literal": literal,
                    "lexical_form": classify_numeric_literal(literal),
                    "clause": clause,
                }
            )
    return result


def workload_numeric_summary(queries: list[dict[str, Any]]) -> dict[str, Any]:
    predicates = [item for query in queries for item in numeric_predicates(query["sql"])]
    counts = {
        label: sum(item["lexical_form"] == label for item in predicates)
        for label in ("integer", "decimal", "scientific")
    }
    decimal_queries = sum(
        any(item["lexical_form"] == "decimal" for item in numeric_predicates(query["sql"]))
        for query in queries
    )
    return {
        "query_count": len(queries),
        "numeric_predicate_instances": len(predicates),
        "lexical_form_counts": counts,
        "queries_containing_decimal_numeric_predicate": decimal_queries,
        "decimal_query_fraction": decimal_queries / len(queries),
        "decimal_instance_fraction": counts["decimal"] / len(predicates),
    }


def schema_contract(columns: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"name": name, "postgres_type": type_} for name, type_ in columns]


def _git_sha(repository: Path) -> str:
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise ValueError("type-coercion diagnostic requires a clean research working tree")
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _load_queries(data_root: Path) -> list[dict[str, Any]]:
    queries, _ = _load_source_queries(data_root)
    if len(queries) != 10_000:
        raise ValueError("expected the audited 10,000-query Census13 test workload")
    return queries


def _copy_relation(
    connection: Any,
    csv_path: Path,
    relation: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    definitions = ", ".join(f'"{name}" {type_}' for name, type_ in columns)
    names = ", ".join(f'"{name}"' for name, _ in columns)
    connection.execute(f"CREATE TABLE {relation} ({definitions})")
    with (
        connection.cursor().copy(
            f"COPY {relation} ({names}) FROM STDIN WITH (FORMAT csv, HEADER true)"
        ) as copy,
        csv_path.open("rb") as stream,
    ):
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            copy.write(block)


def _prepare_relation(
    connection: Any,
    relation: str,
    columns: tuple[tuple[str, str], ...],
    csv_path: Path,
) -> dict[str, Any]:
    _copy_relation(connection, csv_path, relation, columns)
    connection.execute("SELECT setseed(%s)", (1.0 / 123,))
    for name, _ in columns:
        connection.execute(f'ALTER TABLE {relation} ALTER COLUMN "{name}" SET STATISTICS 100')
    connection.execute(f"ANALYZE {relation}")
    count = int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
    if count != census13.EXPECTED_ROWS:
        raise ValueError(f"{relation} contains {count} rows, expected {census13.EXPECTED_ROWS}")
    extstats = int(
        connection.execute(
            "SELECT count(*) FROM pg_catalog.pg_statistic_ext WHERE stxrelid = %s::regclass",
            (relation,),
        ).fetchone()[0]
    )
    if extstats != 0:
        raise ValueError(f"{relation} unexpectedly has physical extended statistics")
    return {
        "relation": relation,
        "statistics_target": 100,
        "analyze_count": 1,
        "row_count": count,
        "physical_extended_statistics_count": extstats,
        "schema_contract": schema_contract(columns),
    }


def _filter_expressions(document: Any) -> list[str]:
    filters: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("Filter"), str):
                filters.append(value["Filter"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return filters


def _exact_and_estimate(
    connection: Any, sql: str, truth: int | None = None
) -> tuple[int, int, float, dict[str, Any]]:
    exact = int(
        connection.execute(
            "SELECT count(*) FROM (" + sql.rstrip("; ") + ") AS diagnostic_rows"
        ).fetchone()[0]
    )
    if truth is not None and exact != truth:
        raise ValueError(f"truth mismatch: observed={exact}, canonical={truth}, sql={sql}")
    plan = connection.execute("EXPLAIN (VERBOSE, FORMAT JSON) " + sql).fetchone()[0]
    estimate = int(plan[0]["Plan"]["Plan Rows"])
    loss, _ = _frozen_advisor_qerror(estimate, exact)
    return exact, estimate, loss, {"filters": _filter_expressions(plan), "plan": plan}


def _stats_sanity(connection: Any, relation: str) -> list[dict[str, Any]]:
    names = sorted(NUMERIC_COLUMNS)
    table_name = relation.split(".", 1)[1].strip('"')
    rows = connection.execute(
        "SELECT attname, null_frac, n_distinct, most_common_vals IS NOT NULL, "
        "coalesce(array_length(most_common_vals, 1), 0), histogram_bounds IS NOT NULL, "
        "coalesce(array_length(histogram_bounds, 1), 0) FROM pg_catalog.pg_stats "
        "WHERE schemaname = 'public' AND tablename = %s AND attname = ANY(%s) "
        "ORDER BY attname",
        (table_name, names),
    ).fetchall()
    if len(rows) != len(names):
        raise ValueError(f"ordinary pg_stats missing for numeric columns of {relation}")
    return [
        {
            "column": str(row[0]),
            "null_frac": float(row[1]),
            "n_distinct": float(row[2]),
            "most_common_vals_present": bool(row[3]),
            "most_common_vals_count": int(row[4]),
            "histogram_bounds_present": bool(row[5]),
            "histogram_bounds_count": int(row[6]),
        }
        for row in rows
    ]


def _resolved_typing(
    connection: Any, relation: str, predicate: dict[str, str], filter_text: str
) -> dict[str, Any]:
    column = predicate["column"]
    literal = predicate["literal"]
    declared = str(
        connection.execute(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = %s::regclass AND a.attname = %s",
            (relation, column),
        ).fetchone()[0]
    )
    literal_type = str(connection.execute(f"SELECT pg_typeof({literal})::text").fetchone()[0])
    if declared == "bigint" and literal_type == "numeric":
        resolved_left = "numeric"
        implicit_column_cast = True
    else:
        resolved_left = declared
        implicit_column_cast = False
    operators = (
        (">=", "<=") if predicate["operator"] == "BETWEEN" else (predicate["operator"].lower(),)
    )
    resolved = []
    for operator in operators:
        rows = connection.execute(
            "SELECT o.oid::regoperator::text, format_type(o.oprleft, NULL), "
            "format_type(o.oprright, NULL) FROM pg_operator o "
            "WHERE o.oprnamespace = 'pg_catalog'::regnamespace AND o.oprname = %s "
            "AND format_type(o.oprleft, NULL) = %s AND format_type(o.oprright, NULL) = %s",
            (operator, resolved_left, resolved_left),
        ).fetchall()
        resolved.extend(
            {"operator": str(row[0]), "left_type": str(row[1]), "right_type": str(row[2])}
            for row in rows
        )
    cast_pattern = rf'"{re.escape(column)}"\)?::'
    return {
        "column_declared_type": declared,
        "literal_inferred_type": literal_type,
        "resolved_operator_candidates": resolved,
        "column_implicitly_cast": implicit_column_cast,
        "filter_contains_column_cast": bool(re.search(cast_pattern, filter_text)),
    }


def _representative_predicates(queries: list[dict[str, Any]]) -> list[dict[str, str]]:
    selected: dict[str, dict[str, str]] = {}
    for query in queries:
        for predicate in numeric_predicates(query["sql"]):
            selected.setdefault(predicate["column"], predicate)
    return [selected[name] for name in sorted(selected)]


def _relation_query_map(
    connection: Any,
    relation: str,
    queries: list[dict[str, Any]],
    truths: dict[str, int],
) -> dict[str, dict[str, Any]]:
    result = {}
    for query in queries:
        sql = diagnostic_query_sql(query["sql"], relation)
        exact, estimate, loss, _ = _exact_and_estimate(connection, sql, truths[query["query_id"]])
        result[query["query_id"]] = {"estimate": estimate, "qerror": loss, "exact_rows": exact}
    return result


def _decision(
    h1: dict[str, Any], h2: dict[str, Any], baseline: dict[str, Any], implicit_casts: int
) -> str:
    h1_close = abs(h1["mean"] - baseline["A"]["mean"]) / baseline["A"]["mean"] < 0.10
    h2_close = abs(h2["mean"] - baseline["C"]["mean"]) / baseline["C"]["mean"] < 0.10
    return (
        "numeric-literal-coercion-confirmed"
        if h1_close and h2_close and implicit_casts
        else "mixed"
    )


def run_type_coercion(
    *,
    dsn: str,
    canonical_run: Path = Path("runs/197e9b890ac58bc4fbcfb218"),
    baseline_gap_artifact: Path = Path(
        "diagnostics/arecel-census13-baseline-gap/baseline-gap-v1.json"
    ),
    output_directory: Path = Path("diagnostics/arecel-census13-type-coercion"),
    data_root: Path | None = None,
) -> dict[str, Any]:
    if not dsn:
        raise ValueError("a stock PostgreSQL DSN is required")
    repository = Path(__file__).resolve().parents[2]
    research_sha = _git_sha(repository)
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"type-coercion output already exists: {output_directory}")
    data_root = census13.data_root(data_root)
    queries = _load_queries(data_root)
    truth_artifact = json.loads(
        (canonical_run / "ground-truth-v1.json").read_text(encoding="utf-8")
    )
    truths = {item["query_id"]: int(item["cardinality"]) for item in truth_artifact["truths"]}
    baseline_gap = json.loads(baseline_gap_artifact.read_text(encoding="utf-8"))
    if baseline_gap["format_version"] != "arecel-census13-baseline-gap-v1":
        raise ValueError("baseline-gap source artifact has an unexpected format")
    started = time.time()
    import psycopg

    relation_specs = {
        "paper_numeric": (relation_sql("paper_numeric"), PAPER_NUMERIC_COLUMNS),
        "advisor_numeric": (relation_sql("advisor_numeric"), ADVISOR_NUMERIC_COLUMNS),
        "H1": (relation_sql("hybrid_h1"), HYBRID_H1_COLUMNS),
    }
    relation_metadata: dict[str, Any] = {}
    estimates: dict[str, dict[str, dict[str, Any]]] = {}
    representative: dict[str, Any] = {}
    multi_predicate: dict[str, Any] = {}
    with psycopg.connect(dsn, autocommit=True) as connection:
        for label, (relation, columns) in relation_specs.items():
            relation_metadata[label] = _prepare_relation(
                connection, relation, columns, census13.csv_path(data_root)
            )
            estimates[label] = _relation_query_map(connection, relation, queries, truths)
            relation_metadata[label]["pg_stats_numeric"] = _stats_sanity(connection, relation)

        predicates = _representative_predicates(queries)
        for predicate in predicates:
            entry = {"predicate": predicate}
            for label, relation in (
                ("paper_numeric", relation_specs["paper_numeric"][0]),
                ("advisor_numeric", relation_specs["advisor_numeric"][0]),
            ):
                sql = f"SELECT * FROM {relation} WHERE {predicate['clause']};"
                exact, estimate, loss, plan = _exact_and_estimate(connection, sql)
                entry[label] = {
                    "true_rows": exact,
                    "estimate": estimate,
                    "qerror": loss,
                    "verbose_filters": plan["filters"],
                    "resolved_typing": _resolved_typing(
                        connection,
                        relation,
                        predicate,
                        plan["filters"][0] if plan["filters"] else "",
                    ),
                }
            representative[predicate["column"]] = entry

        for index in TAIL_INDICES[:2]:
            query_id = f"arecel_census13_test_{index:06d}"
            query = next(query for query in queries if query["query_id"] == query_id)
            item = {"query_id": query_id, "sql": query["sql"]}
            for label, relation in (
                ("paper_numeric", relation_specs["paper_numeric"][0]),
                ("advisor_numeric", relation_specs["advisor_numeric"][0]),
            ):
                sql = diagnostic_query_sql(query["sql"], relation)
                _, estimate, loss, plan = _exact_and_estimate(connection, sql, truths[query_id])
                item[label] = {
                    "estimate": estimate,
                    "qerror": loss,
                    "verbose_filters": plan["filters"],
                }
            multi_predicate[query_id] = item
        stock_version = connection.execute(
            "SELECT version(), current_setting('server_version_num')"
        ).fetchone()

    h1_records = [
        {
            "query_id": query["query_id"],
            "weight": 1.0,
            "qerror": estimates["H1"][query["query_id"]]["qerror"],
        }
        for query in queries
    ]
    h2_records = [
        {
            "query_id": query["query_id"],
            "weight": 1.0,
            "qerror": estimates["advisor_numeric"][query["query_id"]]["qerror"],
        }
        for query in queries
    ]
    baseline_tail = {item["query_id"]: item for item in baseline_gap["tail_queries"]}
    tail = []
    for index in TAIL_INDICES:
        query_id = f"arecel_census13_test_{index:06d}"
        source_query = next(query for query in queries if query["query_id"] == query_id)
        baseline = baseline_tail[query_id]
        tail.append(
            {
                "query_id": query_id,
                "original_sql": source_query["sql"],
                "true_rows": truths[query_id],
                "paper_schema_estimate": baseline["paper_target10000_estimate"],
                "paper_schema_qerror": baseline["paper_target10000_qerror"],
                "current_bigint_schema_estimate": baseline[
                    "advisor_schema_full_target100_estimate"
                ],
                "current_bigint_schema_qerror": baseline["advisor_schema_full_target100_qerror"],
                "H1_estimate": estimates["H1"][query_id]["estimate"],
                "H1_qerror": estimates["H1"][query_id]["qerror"],
                "H2_estimate": estimates["advisor_numeric"][query_id]["estimate"],
                "H2_qerror": estimates["advisor_numeric"][query_id]["qerror"],
            }
        )

    numeric_summary = workload_numeric_summary(queries)
    baseline_distributions = {
        label: baseline_gap["comparison_ladder"][label] for label in ("A", "C")
    }
    implicit_casts = sum(
        int(entry["advisor_numeric"]["resolved_typing"]["column_implicitly_cast"])
        for entry in representative.values()
    )
    h1_summary = summary_for(h1_records, "qerror")
    h2_summary = summary_for(h2_records, "qerror")
    artifact = {
        "format_version": FORMAT_VERSION,
        "research_repository": FROZEN_RESEARCH_REPOSITORY,
        "research_commit_sha": research_sha,
        "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "upstream": {
            "repository": census13.UPSTREAM_URL,
            "commit": census13.UPSTREAM_COMMIT,
            "dataset": "census13",
            "workload": "base:test",
        },
        "dataset": census13.inspect(data_root),
        "workload": {
            "query_count": len(queries),
            "canonical_workload_sha256": sha256_file(census13.canonical_workload_path(data_root)),
            "numeric_literal_summary": numeric_summary,
        },
        "stock_postgresql": {
            "server_version": str(stock_version[0]),
            "server_version_num": int(stock_version[1]),
        },
        "source_baseline_gap_digest": baseline_gap["semantic_digest"],
        "relations": relation_metadata,
        "baseline_distributions": baseline_distributions,
        "hybrid_distributions": {
            "H1": {"schema_contract": schema_contract(HYBRID_H1_COLUMNS), **h1_summary},
            "H2": {"schema_contract": schema_contract(HYBRID_H2_COLUMNS), **h2_summary},
        },
        "representative_predicates": representative,
        "multi_predicate_examples": multi_predicate,
        "tail_queries": tail,
        "decision": _decision(h1_summary, h2_summary, baseline_distributions, implicit_casts),
        "interpretation": {
            "same_query_semantics_not_same_estimation_expression": True,
            "numeric_column_implicit_casts_in_representatives": implicit_casts,
            "scope": "mechanism diagnostic only; no broader advisor-quality claim",
        },
        "elapsed_seconds": time.time() - started,
    }
    reject_credentials(artifact)
    artifact["semantic_digest"] = semantic_digest(artifact)
    output_directory.mkdir(parents=True, exist_ok=True)
    write_json(output_directory / "type-coercion-v1.json", artifact)
    return {
        "artifact": str(output_directory / "type-coercion-v1.json"),
        "semantic_digest": artifact["semantic_digest"],
        "decision": artifact["decision"],
        "numeric_literal_summary": numeric_summary,
        "hybrid_distributions": artifact["hybrid_distributions"],
    }
