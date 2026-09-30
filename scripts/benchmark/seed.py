#!/usr/bin/env -S uv run python
import argparse
import json
import pathlib
import random
import uuid

from sqlalchemy import delete, insert
from sqlmodel import Session, col, select

from app.models.sql_model import Profile, Project, Step, engine

PROJECT_NAME = "scale-test"
MIN_STEPS = 20
MAX_STEPS = 60
NEEDLE_RATE = 0.1
SEED = 42

VOCAB = [
    "Data Collection",
    "Data Preparation",
    "Data Modeling",
    "Model Evaluation",
    "Model Deployment",
    "Save Results",
]

NEEDLES: dict[str, list[str]] = {
    "needle_a": [
        "Data Collection",
        "Data Preparation",
        "Data Modeling",
        "Model Evaluation",
        "Model Deployment",
        "Save Results",
    ],
    "needle_b": [
        "Model Evaluation",
        "Data Preparation",
        "Model Evaluation",
        "Data Preparation",
        "Model Evaluation",
        "Data Preparation",
        "Data Modeling",
    ],
    "needle_c": [
        "Data Collection",
        "Data Preparation",
        "Data Preparation",
        "Data Modeling",
        "Model Evaluation",
        "Model Evaluation",
        "Model Deployment",
        "Save Results",
    ],
    "needle_d": [
        "Data Preparation",
        "Data Modeling",
        "Data Preparation",
        "Model Evaluation",
        "Model Evaluation",
        "Save Results",
    ],
    "needle_e": [
        "Data Collection",
        "Data Preparation",
        "Data Modeling",
        "Model Evaluation",
        "Data Preparation",
        "Data Modeling",
        "Model Evaluation",
        "Model Deployment",
        "Save Results",
    ],
}


def ground_truth_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "data" / f"{PROJECT_NAME}.json"


def get_or_create_project(session: Session, reset: bool) -> Project:
    project = session.exec(select(Project).where(Project.name == PROJECT_NAME)).first()
    if project is not None:
        if not reset:
            raise SystemExit(
                f"Project '{PROJECT_NAME}' already exists (id={project.id}). "
                "Pass --reset to regenerate it."
            )
        profile_ids = session.exec(
            select(Profile.id).where(Profile.project_id == project.id)
        ).all()
        if profile_ids:
            session.execute(delete(Step).where(col(Step.profile_id).in_(profile_ids)))
            session.execute(
                delete(Profile).where(col(Profile.project_id) == project.id)
            )
            session.commit()
        return project

    project = Project(name=PROJECT_NAME)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def assign_needles(num_notebooks: int) -> dict[int, str]:
    per_needle = round(num_notebooks * NEEDLE_RATE)
    indices = list(range(num_notebooks))
    random.shuffle(indices)

    assignment: dict[int, str] = {}
    cursor = 0
    for needle_name in NEEDLES:
        for index in indices[cursor : cursor + per_needle]:
            assignment[index] = needle_name
        cursor += per_needle
    return assignment


def build_sequence(needle_name: str | None) -> list[str]:
    length = random.randint(MIN_STEPS, MAX_STEPS)
    sequence = [random.choice(VOCAB) for _ in range(length)]
    if needle_name is not None:
        needle_terms = NEEDLES[needle_name]
        insert_at = random.randint(0, len(sequence))
        sequence = sequence[:insert_at] + needle_terms + sequence[insert_at:]
    return sequence


def generate_profiles(
    project_id: uuid.UUID, num_notebooks: int
) -> tuple[list[dict], list[dict], dict]:
    needle_assignment = assign_needles(num_notebooks)
    profile_rows = []
    step_rows = []
    ground_truth_profiles = {}

    for index in range(num_notebooks):
        needle_name = needle_assignment.get(index)
        sequence = build_sequence(needle_name)
        profile_name = f"scale-notebook-{index:05d}"
        profile_id = uuid.uuid4()

        profile_rows.append(
            {
                "id": profile_id,
                "project_id": project_id,
                "name": profile_name,
                "json_profile": {
                    "name": profile_name,
                    "source": [{"name": name} for name in sequence],
                },
                "encoded_profile": " -> ".join(sequence),
            }
        )

        for position, name in enumerate(sequence):
            step_rows.append(
                {
                    "id": uuid.uuid4(),
                    "name": name,
                    "position": position,
                    "number_children": 0,
                    "profile_id": profile_id,
                    "previous_step_id": None,
                }
            )

        ground_truth_profiles[profile_name] = {"needle": needle_name, "steps": sequence}

    return profile_rows, step_rows, ground_truth_profiles


def bulk_insert(
    session: Session, model: type, rows: list[dict], chunk_size: int = 5000
) -> None:
    for start in range(0, len(rows), chunk_size):
        session.execute(insert(model), rows[start : start + chunk_size])
    session.commit()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-notebooks", type=int, default=1000)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete any existing scale-test notebooks before generating new ones.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    random.seed(SEED)

    with Session(engine) as session:
        project = get_or_create_project(session, args.reset)
        project_id = project.id
        profile_rows, step_rows, ground_truth_profiles = generate_profiles(
            project_id, args.num_notebooks
        )
        bulk_insert(session, Profile, profile_rows)
        bulk_insert(session, Step, step_rows)

    needle_counts = {
        needle_name: sum(
            1
            for profile in ground_truth_profiles.values()
            if profile["needle"] == needle_name
        )
        for needle_name in NEEDLES
    }

    ground_truth = {
        "project_id": str(project_id),
        "needle_counts": needle_counts,
        "profiles": ground_truth_profiles,
    }

    out_path = ground_truth_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ground_truth, indent=2, default=str))

    print(f"project_id={project_id}")
    print(f"notebooks={args.num_notebooks} steps_total={len(step_rows)}")
    print(f"needle_counts={needle_counts}")
    print(f"ground_truth={out_path}")
    print("next: uv run python scripts/benchmark/query.py --test")


if __name__ == "__main__":
    main()
