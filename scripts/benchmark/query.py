#!/usr/bin/env -S uv run python
import argparse
import fnmatch
import json
import statistics
import time
import uuid

from seed import NEEDLES, ground_truth_path
from sqlmodel import Session, text

from app.models.sql_model import engine

TOKEN_DELIMITER = "\x1f"

QUERY = """
    WITH sequences AS (
        SELECT
            p.name AS profile_name,
            :delimiter || string_agg(s.name, :delimiter ORDER BY s.position) || :delimiter
                AS token_string
        FROM step s
        JOIN profile p ON p.id = s.profile_id
        WHERE p.project_id = :project_id
        GROUP BY p.id, p.name
    )
    SELECT profile_name
    FROM sequences
    WHERE token_string ~ :pattern
"""


def _glob_to_are(term: str) -> str:
    pattern = []
    for char in term:
        if char == "*":
            pattern.append(f"[^{TOKEN_DELIMITER}]*")
        elif char == "?":
            pattern.append(f"[^{TOKEN_DELIMITER}]")
        else:
            pattern.append(char)
    return "".join(pattern)


def compile_pattern(steps: list[str]) -> str:
    return "".join(
        rf"{TOKEN_DELIMITER}{_glob_to_are(step)}(?={TOKEN_DELIMITER})" for step in steps
    )


def timing_stats(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 3),
        "mean_ms": round(statistics.mean(ordered), 3),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(ordered[-1], 3),
    }


def run_query(project_id: uuid.UUID, steps: list[str], repeat: int) -> dict:
    pattern = compile_pattern(steps)
    params = {
        "project_id": str(project_id),
        "delimiter": TOKEN_DELIMITER,
        "pattern": pattern,
    }

    timings_ms = []
    matches: list[str] = []
    with Session(engine) as session:
        for i in range(repeat):
            start = time.perf_counter()
            rows = session.execute(text(QUERY), params).all()
            timings_ms.append((time.perf_counter() - start) * 1000)
            if i == 0:
                matches = sorted(row[0] for row in rows)

    return {"matches": matches, "timing_ms": timing_stats(timings_ms)}


def _term_matches(term: str, name: str) -> bool:
    if "*" in term or "?" in term:
        return fnmatch.fnmatchcase(name, term)
    return term == name


def sequence_matches(sequence: list[str], terms: list[str]) -> bool:
    span = len(sequence) - len(terms)
    if span < 0:
        return False
    for start in range(span + 1):
        if all(_term_matches(terms[i], sequence[start + i]) for i in range(len(terms))):
            return True
    return False


def build_test_battery() -> list[tuple[str, list[str]]]:
    battery = [(f"{name}_exact", terms) for name, terms in NEEDLES.items()]
    battery.append(
        (
            "needle_a_wildcard",
            [
                "Data *",
                "Data Preparation",
                "Data Modeling",
                "Model Evaluation",
                "Model *",
                "Save Results",
            ],
        )
    )
    battery.append(("no_match_6", ["Save Results"] * 6))
    battery.append(("long_10_terms", ["Data Preparation", "Model Evaluation"] * 5))
    return battery


def run_test_mode(repeat: int) -> None:
    ground_truth = json.loads(ground_truth_path().read_text())
    project_id = uuid.UUID(ground_truth["project_id"])
    profiles = ground_truth["profiles"]

    all_correct = True
    for name, terms in build_test_battery():
        expected = sorted(
            profile_name
            for profile_name, profile in profiles.items()
            if sequence_matches(profile["steps"], terms)
        )
        result = run_query(project_id, terms, repeat)
        actual = sorted(result["matches"])
        correct = actual == expected
        all_correct = all_correct and correct

        status = "OK" if correct else "MISMATCH"
        print(
            f"[{status}] {name} ({len(terms)} terms) "
            f"expected={len(expected)} actual={len(actual)} "
            f"median_ms={result['timing_ms']['median_ms']}"
        )

    if not all_correct:
        raise SystemExit(1)


def run_query_mode(steps: list[str], project_id: uuid.UUID | None, repeat: int) -> None:
    if project_id is None:
        ground_truth = json.loads(ground_truth_path().read_text())
        project_id = uuid.UUID(ground_truth["project_id"])

    result = run_query(project_id, steps, repeat)
    print(json.dumps(result, indent=2))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--steps",
        nargs="+",
        help="Ordered step names to match; '*'/'?' wildcards allowed. Ignored with --test.",
    )
    parser.add_argument(
        "--project-id",
        type=uuid.UUID,
        default=None,
        help="Defaults to the project seeded by scripts/seed.py.",
    )
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run the built-in correctness+timing battery instead of a custom query.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.test:
        run_test_mode(args.repeat)
        return
    if not args.steps:
        raise SystemExit("--steps is required unless --test is passed")
    run_query_mode(args.steps, args.project_id, args.repeat)


if __name__ == "__main__":
    main()
