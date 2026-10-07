#!/usr/bin/env -S uv run python
import argparse
import json
import time
import uuid

from query import MANIFEST_PATH, resolve_project_id, sequence_matches, timing_stats
from sqlmodel import Session, text

from app.models.api_model import PatternGroup, PatternMetaCharacters
from app.models.sql_model import engine
from app.utils.convert_ppm_to_sql import convert_steps_to_sql_query_template

GAP_MARKER = "*"


def build_pattern(terms: list[str]) -> list[PatternGroup]:
    return [
        PatternGroup(
            name=f"g{i}",
            steps=[] if term == GAP_MARKER else [term],
            multiplicity="1",
            metaCharacters=PatternMetaCharacters(
                startsWith=False, endsWith=False, negate=False
            ),
        )
        for i, term in enumerate(terms)
    ]


def run_dsl_query(project_id: uuid.UUID, terms: list[str], repeat: int) -> dict:
    pattern = build_pattern(terms)
    query = convert_steps_to_sql_query_template(project_id, pattern)

    timings_ms = []
    matches: list[str] = []
    with Session(engine) as session:
        for i in range(repeat):
            start = time.perf_counter()
            rows = session.execute(text(query)).all()
            timings_ms.append((time.perf_counter() - start) * 1000)
            if i == 0:
                matches = sorted({row[0] for row in rows})

    return {"matches": matches, "timing_ms": timing_stats(timings_ms)}


def run_test_mode(repeat: int) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    profiles = manifest["notebooks"]
    project_id = resolve_project_id()

    all_correct = True
    for name, terms in manifest["needles"].items():
        expected = sorted(
            profile_name
            for profile_name, profile in profiles.items()
            if sequence_matches(profile["steps"], terms)
        )
        result = run_dsl_query(project_id, terms, repeat)
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


def run_query_mode(terms: list[str], project_id: uuid.UUID | None, repeat: int) -> None:
    if project_id is None:
        project_id = resolve_project_id()

    result = run_dsl_query(project_id, terms, repeat)
    print(json.dumps(result, indent=2))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--steps",
        nargs="+",
        help="Ordered step names",
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
    if not args.steps:
        raise SystemExit("pass --steps or --test")
    run_query_mode(args.steps, args.project_id, args.repeat)


if __name__ == "__main__":
    main()
