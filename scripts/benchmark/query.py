#!/usr/bin/env -S uv run python
import argparse
import fnmatch
import json
import pathlib
import statistics
import time
import uuid

from seed import PROJECT_NAME
from sqlmodel import Session, select, text

from app.models.sql_model import Project, engine

MANIFEST_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
    / "data"
    / "notebooks.manifest.json"
)

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


def run_query(project_id: uuid.UUID, pattern: str, repeat: int) -> dict:
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


def resolve_project_id() -> uuid.UUID:
    with Session(engine) as session:
        project = session.exec(
            select(Project).where(Project.name == PROJECT_NAME)
        ).first()
        if project is None:
            raise SystemExit(
                f"Project '{PROJECT_NAME}' not found, run scripts/seed.py first"
            )
        return project.id


def build_test_battery(needles: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    battery = [(f"{name}_exact", terms) for name, terms in needles.items()]
    battery.append(("wildcard_data_then_model", ["Data *", "Model *"]))
    battery.append(("no_match_6", ["Save Results"] * 6))
    battery.append(("long_10_terms", ["Data Preparation", "Model Evaluation"] * 5))
    return battery


def run_test_mode(repeat: int) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    profiles = manifest["notebooks"]
    project_id = resolve_project_id()

    all_correct = True
    for name, terms in build_test_battery(manifest["needles"]):
        expected = sorted(
            profile_name
            for profile_name, profile in profiles.items()
            if sequence_matches(profile["steps"], terms)
        )
        result = run_query(project_id, compile_pattern(terms), repeat)
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


def run_query_mode(pattern: str, project_id: uuid.UUID | None, repeat: int) -> None:
    if project_id is None:
        project_id = resolve_project_id()

    result = run_query(project_id, pattern, repeat)
    print(json.dumps(result, indent=2))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--steps",
        nargs="+",
        help="Ordered step names, one term per step. e.g. --steps 'Data *' 'Data Modeling' ",
    )
    parser.add_argument(
        "--regex",
        help="Raw Postgres ERE matched against sequence, encoded as chr(31) e.g. --regex 'Data Modeling.*Model Deployment'",
    )
    parser.add_argument("--project-id", type=uuid.UUID, default=None)
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.test:
        run_test_mode(args.repeat)
        return
    if not args.regex and not args.steps:
        raise SystemExit("pass --steps, --regex, or --test")
    pattern = args.regex or compile_pattern(args.steps)
    run_query_mode(pattern, args.project_id, args.repeat)


if __name__ == "__main__":
    main()
