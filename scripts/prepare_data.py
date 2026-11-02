#!/usr/bin/env python3
"""Download, validate, and sample the pinned MBPP dataset slice."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import warnings
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.request import Request, urlopen

from datasets import Dataset, load_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_PATH = PROJECT_ROOT / "data" / "raw" / "mbpp_sanitized_test.parquet"
DATASET_MANIFEST_PATH = PROJECT_ROOT / "data" / "manifests" / "dataset.json"
SAMPLE_MANIFEST_PATH = PROJECT_ROOT / "data" / "manifests" / "sample_seed_42.csv"

DATASET_ID = "google-research-datasets/mbpp"
DATASET_CONFIG = "sanitized"
DATASET_SPLIT = "test"
DATASET_REVISION = "4bb6404fdc6cacfda99d4ac4205087b89d32030c"
DATASET_FILENAME = "sanitized/test-00000-of-00001.parquet"
DATASET_URL = (
    f"https://huggingface.co/datasets/{DATASET_ID}/resolve/"
    f"{DATASET_REVISION}/{DATASET_FILENAME}"
)
DATASET_LICENSE = "CC-BY-4.0"
EXPECTED_SIZE = 60_864
EXPECTED_SHA256 = "e9e9efa2c0d59ef5e55537a9d126b8f875d5ac010a8d75628d76824884e15850"

EXPECTED_COLUMNS = [
    "source_file",
    "task_id",
    "prompt",
    "code",
    "test_imports",
    "test_list",
]
EXPECTED_SCHEMA = {
    "source_file": "string",
    "task_id": "int32",
    "prompt": "string",
    "code": "string",
    "test_imports": "list[string]",
    "test_list": "list[string]",
}
EXPECTED_ROWS = 257
EXPECTED_TEST_DISTRIBUTION = {3: 253, 4: 3, 5: 1}
EXPECTED_TOTAL_TESTS = 776
EXPECTED_TASKS_WITH_IMPORTS = 13
SAMPLE_SEED = 42
SAMPLE_SIZE = 150


class DataPreparationError(RuntimeError):
    """Raised when downloaded or derived data violates project invariants."""


def file_sha256(path: Path) -> str:
    """Return a file's SHA-256 digest without loading it fully into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(
    path: Path,
    *,
    expected_size: int = EXPECTED_SIZE,
    expected_sha256: str = EXPECTED_SHA256,
) -> None:
    """Verify that a file is exactly the pinned upstream artifact."""
    if not path.is_file():
        raise DataPreparationError(f"Dataset file does not exist: {path}")

    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise DataPreparationError(
            f"Unexpected dataset size for {path}: {actual_size}, expected {expected_size}"
        )

    actual_sha256 = file_sha256(path)
    if actual_sha256 != expected_sha256:
        raise DataPreparationError(
            f"Unexpected SHA-256 for {path}: {actual_sha256}, expected {expected_sha256}"
        )


def download_dataset(
    target: Path = RAW_DATA_PATH,
    *,
    force: bool = False,
    url: str = DATASET_URL,
    expected_size: int = EXPECTED_SIZE,
    expected_sha256: str = EXPECTED_SHA256,
) -> Path:
    """Download the pinned Parquet file atomically after integrity checks."""
    target = Path(target)
    if target.exists() and not force:
        verify_file(
            target,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        print(f"Dataset already exists and is valid: {target}")
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temp_stream:
            temp_path = Path(temp_stream.name)
            request = Request(url, headers={"User-Agent": "mbpp-test-audit/1.0"})
            with urlopen(request) as response:
                shutil.copyfileobj(response, temp_stream)

        verify_file(
            temp_path,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    print(f"Downloaded pinned dataset: {target}")
    return target


def load_local_dataset(path: Path = RAW_DATA_PATH) -> Dataset:
    """Load the local Parquet file as the named test split."""
    result = load_dataset(
        "parquet",
        data_files={DATASET_SPLIT: str(path)},
        split=DATASET_SPLIT,
    )
    if not isinstance(result, Dataset):
        raise DataPreparationError("Expected a single Dataset for the test split")
    return result


def _feature_type(feature: Any) -> str:
    dtype = getattr(feature, "dtype", None)
    if dtype is not None:
        return str(dtype)

    inner = getattr(feature, "feature", None)
    inner_dtype = getattr(inner, "dtype", None)
    if inner_dtype is not None:
        return f"list[{inner_dtype}]"
    return str(feature)


def _validate_python_fragments(row_index: int, row: Mapping[str, Any]) -> None:
    fragments = [
        ("code", row["code"]),
        *(("test_imports", value) for value in row["test_imports"]),
        *(("test_list", value) for value in row["test_list"]),
    ]
    for field, fragment in fragments:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(fragment)
        except SyntaxError as error:
            raise DataPreparationError(
                f"Invalid Python syntax in row {row_index}, field {field}: {error}"
            ) from error

        if field == "test_list" and (
            len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assert)
        ):
            raise DataPreparationError(
                f"Expected one assert statement in row {row_index}: {fragment!r}"
            )


def validate_dataset(
    path: Path = RAW_DATA_PATH,
    *,
    check_integrity: bool = True,
    expected_rows: int = EXPECTED_ROWS,
    expected_statistics: bool = True,
) -> tuple[Dataset, dict[str, Any]]:
    """Validate schema, fixed statistics, uniqueness, and Python syntax."""
    path = Path(path)
    if check_integrity:
        verify_file(path)

    dataset = load_local_dataset(path)
    if dataset.column_names != EXPECTED_COLUMNS:
        raise DataPreparationError(
            f"Unexpected columns: {dataset.column_names}; expected {EXPECTED_COLUMNS}"
        )

    actual_schema = {
        name: _feature_type(dataset.features[name]) for name in EXPECTED_COLUMNS
    }
    if actual_schema != EXPECTED_SCHEMA:
        raise DataPreparationError(
            f"Unexpected schema: {actual_schema}; expected {EXPECTED_SCHEMA}"
        )

    if len(dataset) != expected_rows:
        raise DataPreparationError(
            f"Unexpected row count: {len(dataset)}; expected {expected_rows}"
        )

    task_ids = [int(task_id) for task_id in dataset["task_id"]]
    unique_task_ids = set(task_ids)
    if len(unique_task_ids) != len(task_ids):
        duplicates = sorted(
            task_id for task_id, count in Counter(task_ids).items() if count > 1
        )
        raise DataPreparationError(f"Duplicate task_id values: {duplicates}")

    test_distribution = Counter()
    total_tests = 0
    tasks_with_imports = 0
    for row_index, row in enumerate(dataset):
        _validate_python_fragments(row_index, row)
        test_count = len(row["test_list"])
        test_distribution[test_count] += 1
        total_tests += test_count
        tasks_with_imports += bool(row["test_imports"])

    if expected_statistics:
        if dict(test_distribution) != EXPECTED_TEST_DISTRIBUTION:
            raise DataPreparationError(
                "Unexpected tests-per-task distribution: "
                f"{dict(test_distribution)}; expected {EXPECTED_TEST_DISTRIBUTION}"
            )
        if total_tests != EXPECTED_TOTAL_TESTS:
            raise DataPreparationError(
                f"Unexpected total test count: {total_tests}; "
                f"expected {EXPECTED_TOTAL_TESTS}"
            )
        if tasks_with_imports != EXPECTED_TASKS_WITH_IMPORTS:
            raise DataPreparationError(
                f"Unexpected tasks-with-imports count: {tasks_with_imports}; "
                f"expected {EXPECTED_TASKS_WITH_IMPORTS}"
            )

    statistics = {
        "rows": len(dataset),
        "unique_task_ids": len(unique_task_ids),
        "task_id_min": min(task_ids),
        "task_id_max": max(task_ids),
        "total_tests": total_tests,
        "tests_per_task": {
            str(test_count): task_count
            for test_count, task_count in sorted(test_distribution.items())
        },
        "tasks_with_imports": tasks_with_imports,
    }
    return dataset, statistics


def _write_text_if_changed(target: Path, content: str) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    if target.is_file() and target.read_bytes() == encoded:
        return False

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temp_stream:
            temp_path = Path(temp_stream.name)
            temp_stream.write(encoded)
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return True


def write_dataset_manifest(
    statistics: Mapping[str, Any], target: Path = DATASET_MANIFEST_PATH
) -> Path:
    """Write stable provenance and validation metadata."""
    manifest = {
        "dataset_id": DATASET_ID,
        "config": DATASET_CONFIG,
        "split": DATASET_SPLIT,
        "revision": DATASET_REVISION,
        "license": DATASET_LICENSE,
        "source_file": DATASET_FILENAME,
        "source_url": DATASET_URL,
        "local_file": str(RAW_DATA_PATH.relative_to(PROJECT_ROOT)),
        "file_size_bytes": EXPECTED_SIZE,
        "sha256": EXPECTED_SHA256,
        "schema": EXPECTED_SCHEMA,
        "statistics": dict(statistics),
    }
    content = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_text_if_changed(Path(target), content)
    return Path(target)


def _sample_rows(
    rows: Iterable[Mapping[str, Any]], *, sample_size: int, seed: int
) -> list[dict[str, int]]:
    indexed_tasks: list[tuple[int, int]] = []
    for row_index, row in enumerate(rows):
        indexed_tasks.append((int(row["task_id"]), row_index))

    task_to_row = dict(indexed_tasks)
    if len(task_to_row) != len(indexed_tasks):
        raise DataPreparationError("Cannot sample a dataset with duplicate task_id values")
    if sample_size > len(task_to_row):
        raise DataPreparationError(
            f"Sample size {sample_size} exceeds dataset size {len(task_to_row)}"
        )

    selected_ids = random.Random(seed).sample(sorted(task_to_row), sample_size)
    return [
        {
            "sample_index": sample_index,
            "row_index": task_to_row[task_id],
            "task_id": task_id,
        }
        for sample_index, task_id in enumerate(selected_ids)
    ]


def write_sample_manifest(
    dataset: Iterable[Mapping[str, Any]],
    target: Path = SAMPLE_MANIFEST_PATH,
    *,
    sample_size: int = SAMPLE_SIZE,
    seed: int = SAMPLE_SEED,
) -> Path:
    """Create the deterministic sample manifest as stable CSV bytes."""
    selected_rows = _sample_rows(dataset, sample_size=sample_size, seed=seed)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=["sample_index", "row_index", "task_id"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(selected_rows)
    _write_text_if_changed(Path(target), output.getvalue())
    return Path(target)


def prepare_all(*, force: bool = False) -> None:
    download_dataset(force=force)
    dataset, statistics = validate_dataset()
    write_dataset_manifest(statistics)
    write_sample_manifest(dataset)
    print(f"Validated {statistics['rows']} tasks and {statistics['total_tests']} tests")
    print(f"Dataset manifest: {DATASET_MANIFEST_PATH}")
    print(f"Sample manifest: {SAMPLE_MANIFEST_PATH}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download", help="download pinned data")
    download_parser.add_argument("--force", action="store_true")

    subparsers.add_parser("validate", help="validate data and write metadata")
    subparsers.add_parser("sample", help="validate data and write the sample")

    all_parser = subparsers.add_parser("all", help="run the full preparation flow")
    all_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "download":
            download_dataset(force=args.force)
        elif args.command == "validate":
            _, statistics = validate_dataset()
            write_dataset_manifest(statistics)
            print(f"Dataset is valid: {RAW_DATA_PATH}")
        elif args.command == "sample":
            dataset, _ = validate_dataset()
            write_sample_manifest(dataset)
            print(f"Sample manifest: {SAMPLE_MANIFEST_PATH}")
        elif args.command == "all":
            prepare_all(force=args.force)
    except (DataPreparationError, OSError) as error:
        print(f"Preparation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
