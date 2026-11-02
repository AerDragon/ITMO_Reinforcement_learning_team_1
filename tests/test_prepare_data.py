from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import pytest
from datasets import Dataset, Features, List, Value

from scripts import prepare_data


def _valid_rows(task_ids: list[int]) -> dict[str, list[object]]:
    count = len(task_ids)
    return {
        "source_file": ["fixture.ipynb"] * count,
        "task_id": task_ids,
        "prompt": ["Return the input."] * count,
        "code": ["def identity(value):\n    return value"] * count,
        "test_imports": [[] for _ in task_ids],
        "test_list": [["assert identity(1) == 1"] for _ in task_ids],
    }


def _write_parquet(path: Path, values: dict[str, list[object]]) -> None:
    features = Features(
        {
            "source_file": Value("string"),
            "task_id": Value("int32"),
            "prompt": Value("string"),
            "code": Value("string"),
            "test_imports": List(Value("string")),
            "test_list": List(Value("string")),
        }
    )
    available_features = Features(
        {name: feature for name, feature in features.items() if name in values}
    )
    Dataset.from_dict(values, features=available_features).to_parquet(str(path))


def test_verify_file_rejects_corrupted_content(tmp_path: Path) -> None:
    path = tmp_path / "dataset.parquet"
    path.write_bytes(b"corrupted")

    with pytest.raises(prepare_data.DataPreparationError, match="Unexpected SHA-256"):
        prepare_data.verify_file(
            path,
            expected_size=len(b"corrupted"),
            expected_sha256="0" * 64,
        )


def test_existing_valid_download_does_not_use_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"already downloaded"
    path = tmp_path / "dataset.parquet"
    path.write_bytes(payload)

    def fail_if_called(*args: object, **kwargs: object) -> io.BytesIO:
        raise AssertionError("network must not be used for a valid existing file")

    monkeypatch.setattr(prepare_data, "urlopen", fail_if_called)
    result = prepare_data.download_dataset(
        path,
        expected_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert result == path
    assert path.read_bytes() == payload


def test_validate_rejects_wrong_schema(tmp_path: Path) -> None:
    path = tmp_path / "wrong-schema.parquet"
    values = _valid_rows([1])
    values.pop("prompt")
    _write_parquet(path, values)

    with pytest.raises(prepare_data.DataPreparationError, match="Unexpected columns"):
        prepare_data.validate_dataset(
            path,
            check_integrity=False,
            expected_rows=1,
            expected_statistics=False,
        )


def test_validate_rejects_duplicate_task_ids(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.parquet"
    _write_parquet(path, _valid_rows([7, 7]))

    with pytest.raises(prepare_data.DataPreparationError, match="Duplicate task_id"):
        prepare_data.validate_dataset(
            path,
            check_integrity=False,
            expected_rows=2,
            expected_statistics=False,
        )


def test_sample_is_deterministic_unique_and_in_source(tmp_path: Path) -> None:
    rows = [
        {"task_id": task_id}
        for task_id in [101, 7, 44, 3, 900, 18, 65, 22, 71, 5]
    ]
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"

    prepare_data.write_sample_manifest(
        rows, first_path, sample_size=6, seed=prepare_data.SAMPLE_SEED
    )
    prepare_data.write_sample_manifest(
        rows, second_path, sample_size=6, seed=prepare_data.SAMPLE_SEED
    )

    assert first_path.read_bytes() == second_path.read_bytes()
    with first_path.open(newline="", encoding="utf-8") as stream:
        selected = list(csv.DictReader(stream))
    selected_ids = [int(row["task_id"]) for row in selected]
    assert len(selected_ids) == 6
    assert len(set(selected_ids)) == 6
    assert set(selected_ids) <= {row["task_id"] for row in rows}


def test_repository_dataset_matches_pinned_contract() -> None:
    dataset, statistics = prepare_data.validate_dataset()

    assert len(dataset) == prepare_data.EXPECTED_ROWS
    assert statistics["total_tests"] == prepare_data.EXPECTED_TOTAL_TESTS
    assert statistics["tasks_with_imports"] == prepare_data.EXPECTED_TASKS_WITH_IMPORTS


def test_repository_sample_matches_pinned_dataset(tmp_path: Path) -> None:
    dataset = prepare_data.load_local_dataset()
    regenerated_path = tmp_path / "sample.csv"
    prepare_data.write_sample_manifest(dataset, regenerated_path)

    assert regenerated_path.read_bytes() == prepare_data.SAMPLE_MANIFEST_PATH.read_bytes()
    with regenerated_path.open(newline="", encoding="utf-8") as stream:
        selected = list(csv.DictReader(stream))
    selected_ids = [int(row["task_id"]) for row in selected]
    assert len(selected_ids) == prepare_data.SAMPLE_SIZE
    assert len(set(selected_ids)) == prepare_data.SAMPLE_SIZE
    assert set(selected_ids) <= set(dataset["task_id"])
