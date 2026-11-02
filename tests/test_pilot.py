from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import pilot


def test_sandbox_classifies_pass_assertion_and_runtime_error() -> None:
    passed = pilot.run_in_sandbox("def value(): return 1", "assert value() == 1")
    assertion = pilot.run_in_sandbox("def value(): return 1", "assert value() == 2")
    runtime = pilot.run_in_sandbox("def value(): return 1 / 0", "value()")

    assert passed.status == "pass"
    assert assertion.status == "assertion_failed"
    assert runtime.status == "runtime_error"


def test_sandbox_enforces_wall_timeout() -> None:
    result = pilot.run_in_sandbox("import time\ndef wait(): time.sleep(10)", "wait()")
    assert result.status == "timeout"


def test_sandbox_blocks_network_and_project_files() -> None:
    network_source = """
import socket
def network_is_blocked():
    sock = socket.socket()
    sock.settimeout(0.2)
    return sock.connect_ex(("1.1.1.1", 53)) != 0
"""
    project_file = str(pilot.PROJECT_ROOT / "README.md")
    filesystem_source = (
        "import os\n"
        f"def project_is_hidden(): return not os.path.exists({project_file!r})\n"
    )

    assert pilot.run_in_sandbox(network_source, "assert network_is_blocked()").status == "pass"
    assert pilot.run_in_sandbox(filesystem_source, "assert project_is_hidden()").status == "pass"


def test_sandbox_caps_output() -> None:
    result = pilot.run_in_sandbox(
        'print("x" * 100000)\ndef value(): return 1',
        "assert value() == 1",
    )
    assert result.status == "runtime_error"
    assert result.output_truncated is True
    assert len(result.stdout.encode()) <= pilot.OUTPUT_BYTES


def test_wilson_interval_known_boundaries() -> None:
    lower, upper = pilot.wilson_interval(0, 14)
    assert lower == pytest.approx(0.0, abs=1e-12)
    assert upper == pytest.approx(0.2153108027)


def test_repository_pilot_artifacts_are_valid() -> None:
    tasks, manifest = pilot.validate_artifacts()
    assert len(tasks) == 20
    assert len(manifest) == 60
    assert sum(row["group"] == "main" for row in manifest) == 40
    assert sum(row["group"] == "control" for row in manifest) == 20


def test_repository_pilot_results_meet_acceptance_gates() -> None:
    with pilot.RUNS_PATH.open(newline="", encoding="utf-8") as stream:
        runs = list(csv.DictReader(stream))
    summary = json.loads(pilot.SUMMARY_PATH.read_text(encoding="utf-8"))

    assert len(runs) == 60
    assert all(row["reference_stock_status"] == "pass" for row in runs)
    assert all(row["counterexample_proves_wrong"] == "true" for row in runs)
    assert all("sandbox_error" not in row.values() for row in runs)
    assert summary["main_mutants"] == 40
    assert summary["control_mutants"] == 20
    assert summary["hardcode_false_passes"]["passed"] == 20
    assert summary["final_threshold_applicable"] is False
    assert pilot.REPORT_PATH == pilot.PROJECT_ROOT / "docs" / "pilot_results.md"
    assert pilot.REPORT_PATH.is_file()
