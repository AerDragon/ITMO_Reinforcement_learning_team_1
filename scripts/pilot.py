#!/usr/bin/env python3
"""Prepare, validate, run, and report the 20-task MBPP pilot."""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import math
import os
import resource
import shutil
import signal
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import prepare_data
except ModuleNotFoundError:  # Direct execution: python scripts/pilot.py
    import prepare_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PILOT_SIZE = 20
PILOT_TASKS_PATH = PROJECT_ROOT / "data" / "manifests" / "pilot_tasks.csv"
MUTANTS_ROOT = PROJECT_ROOT / "artifacts" / "mutants"
COUNTEREXAMPLES_ROOT = PROJECT_ROOT / "artifacts" / "counterexamples"
MUTATION_MANIFEST_PATH = MUTANTS_ROOT / "pilot_manifest.csv"
RESULTS_ROOT = PROJECT_ROOT / "artifacts" / "results"
RUNS_PATH = RESULTS_ROOT / "pilot_runs.csv"
PROPERTIES_PATH = RESULTS_ROOT / "pilot_task_properties.csv"
SUMMARY_PATH = RESULTS_ROOT / "pilot_summary.json"
REPORT_PATH = PROJECT_ROOT / "docs" / "pilot_results.md"

TIMEOUT_SECONDS = 3.0
CPU_SECONDS = 2
MEMORY_BYTES = 512 * 1024 * 1024
OUTPUT_BYTES = 64 * 1024
ALLOWED_STATUSES = {
    "pass",
    "assertion_failed",
    "runtime_error",
    "timeout",
    "sandbox_error",
}


@dataclass(frozen=True)
class MutationDesign:
    sample_index: int
    task_id: int
    mutation_type: str
    change_note: str
    counterexample_note: str

    @property
    def mutation_id(self) -> str:
        return f"mbpp_{self.task_id:04d}_{self.mutation_type}_01"


DESIGNS = [
    MutationDesign(0, 113, "edge_case_omission", "Remove the empty-after-strip guard.", "Whitespace-only input must still return None."),
    MutationDesign(0, 113, "boundary_shift", "Start signed-digit validation one position too late.", "A two-character signed integer exposes the shifted range."),
    MutationDesign(1, 61, "edge_case_omission", "Omit the empty-prefix frequency seed.", "A one-digit substring starting at index zero is lost."),
    MutationDesign(1, 61, "boundary_shift", "Stop the digit loop one element early.", "The final digit must participate in substring counting."),
    MutationDesign(2, 406, "operator_replacement", "Replace the final bitwise AND with OR.", "An even-popcount value must remain False."),
    MutationDesign(2, 406, "edge_case_omission", "Return None for zero instead of its even parity.", "Zero has an even number of set bits."),
    MutationDesign(3, 129, "boundary_shift", "Skip the final main-diagonal element.", "A valid 3x3 magic square needs the complete diagonal."),
    MutationDesign(3, 129, "operator_replacement", "Replace the distinct-sum threshold > 1 with >= 1.", "A known magic square must remain True."),
    MutationDesign(4, 119, "boundary_shift", "Skip the final array element in XOR accumulation.", "A unique value at the last position is omitted."),
    MutationDesign(4, 119, "operator_replacement", "Replace XOR accumulation with bitwise OR.", "Repeated nonzero bits do not cancel under OR."),
    MutationDesign(5, 479, "boundary_shift", "Use > 10 instead of >= 10 in the reduction loop.", "Ten is the exact loop boundary."),
    MutationDesign(5, 479, "operator_replacement", "Replace division by ten with modulo ten.", "Modulo retains the last rather than first digit."),
    MutationDesign(6, 88, "boundary_shift", "Drop the first list element before counting.", "A first-position value with one occurrence disappears."),
    MutationDesign(6, 88, "edge_case_omission", "Return None for an empty list.", "The frequency map of an empty list is empty."),
    MutationDesign(7, 405, "boundary_shift", "Search from tuple index one.", "An element at index zero must be found."),
    MutationDesign(7, 405, "edge_case_omission", "Omit the explicit False return.", "A missing value in a one-element tuple must return False."),
    MutationDesign(8, 77, "operator_replacement", "Replace equality with inequality.", "A multiple of eleven must remain True."),
    MutationDesign(8, 77, "edge_case_omission", "Exclude zero from divisible values.", "Zero is divisible by eleven."),
    MutationDesign(9, 388, "boundary_shift", "Begin searching at n - 1.", "An exact power of two must return itself."),
    MutationDesign(9, 388, "edge_case_omission", "Exclude one from the downward search.", "One is the smallest positive power of two."),
    MutationDesign(10, 477, "operator_replacement", "Replace lower() with upper().", "Mixed-case input must become lowercase."),
    MutationDesign(10, 477, "edge_case_omission", "Index the first character before lowering.", "The empty string must remain valid."),
    MutationDesign(11, 447, "operator_replacement", "Replace exponentiation by multiplication.", "Cubing two differs from multiplying it by three."),
    MutationDesign(11, 447, "edge_case_omission", "Read the first element before processing the remainder.", "An empty list must return an empty list."),
    MutationDesign(12, 273, "operator_replacement", "Replace subtraction with addition.", "A one-element tuple exposes the arithmetic operator."),
    MutationDesign(12, 273, "boundary_shift", "Start both tuple slices at index one.", "The first pair must be included."),
    MutationDesign(13, 71, "operator_replacement", "Reverse the comparison in comb sort.", "An unsorted pair must be ascending."),
    MutationDesign(13, 71, "boundary_shift", "Stop inner comparisons one position early.", "A final out-of-order pair must be compared."),
    MutationDesign(14, 286, "edge_case_omission", "Initialize the best sum to zero.", "All-negative arrays require a negative result."),
    MutationDesign(14, 286, "operator_replacement", "Replace n * k with n + k.", "A repeated positive array requires all repetitions."),
    MutationDesign(15, 237, "boundary_shift", "Skip the first tuple before counting.", "The first normalized tuple contributes to its count."),
    MutationDesign(15, 237, "edge_case_omission", "Index the first item before using Counter.", "An empty input list must return an empty dictionary."),
    MutationDesign(16, 56, "operator_replacement", "Replace multiplication by addition in the predicate.", "A valid Woodall-style relation must remain True."),
    MutationDesign(16, 56, "boundary_shift", "Stop digit reversal before the last digit.", "A two-digit reverse must consume both digits."),
    MutationDesign(17, 20, "operator_replacement", "Reverse the initial parity comparison.", "A known Woodall number must remain True."),
    MutationDesign(17, 20, "edge_case_omission", "Misclassify zero as a special valid value.", "Zero is not a Woodall number."),
    MutationDesign(18, 72, "operator_replacement", "Replace != with == in the modulo predicate.", "A number congruent to two modulo four is invalid."),
    MutationDesign(18, 72, "edge_case_omission", "Exclude zero from representable values.", "Zero is the difference of two equal squares."),
    MutationDesign(19, 109, "boundary_shift", "Stop the inspected prefix one character early.", "A final one inside the requested prefix must count."),
    MutationDesign(19, 109, "operator_replacement", "Count zeroes instead of ones.", "An unequal zero/one string exposes the replacement."),
]

HARDCODE_COUNTEREXAMPLES = {
    113: 'assert check_integer("-7") == True',
    61: "assert count_Substrings('1') == 1",
    406: "assert find_Parity(3) == False",
    129: "assert magic_square_test([[5]]) == True",
    119: "assert search([2, 2, 4]) == 4",
    479: "assert first_Digit(987) == 9",
    88: "assert freq_count([]) == {}",
    405: "assert check_tuplex(('x',), 'x') == True",
    77: "assert is_Diff(22) == True",
    388: "assert highest_Power_of_2(8) == 8",
    477: 'assert is_lower("PyTHon") == "python"',
    447: "assert cube_nums([2]) == [8]",
    273: "assert substract_elements((5,), (2,)) == (3,)",
    71: "assert comb_sort([2, 1]) == [1, 2]",
    286: "assert max_sub_array_sum_repeated([1, 2], 2, 3) == 9",
    237: "assert check_occurences([]) == {}",
    56: "assert check(793) == True",
    20: "assert is_woodall(1) == True",
    72: "assert dif_Square(0) == True",
    109: 'assert odd_Equivalent("1", 1) == 1',
}

MANIFEST_FIELDS = [
    "mutation_id",
    "sample_index",
    "task_id",
    "group",
    "mutation_type",
    "code_path",
    "counterexample_path",
    "change_note",
    "counterexample_note",
]


class PilotError(RuntimeError):
    """Raised when pilot artifacts violate their contract."""


@dataclass(frozen=True)
class SandboxResult:
    status: str
    return_code: int | None
    stdout: str
    stderr: str
    output_truncated: bool


def _write_text_if_changed(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(encoded)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _csv_text(rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def load_pilot_tasks() -> list[dict[str, Any]]:
    sample = _read_csv(prepare_data.SAMPLE_MANIFEST_PATH)[:PILOT_SIZE]
    dataset = prepare_data.load_local_dataset()
    by_id = {int(row["task_id"]): dict(row) for row in dataset}
    tasks: list[dict[str, Any]] = []
    for sample_row in sample:
        task_id = int(sample_row["task_id"])
        row = by_id[task_id]
        row["sample_index"] = int(sample_row["sample_index"])
        row["row_index"] = int(sample_row["row_index"])
        tasks.append(row)
    return tasks


def _assert_parts(test: str) -> tuple[ast.Call, ast.expr]:
    tree = ast.parse(test)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assert):
        raise PilotError(f"Expected one assert: {test}")
    expression = tree.body[0].test
    if not isinstance(expression, ast.Compare) or len(expression.ops) != 1:
        raise PilotError(f"Expected a simple comparison: {test}")
    if not isinstance(expression.ops[0], ast.Eq) or len(expression.comparators) != 1:
        raise PilotError(f"Expected equality in stock test: {test}")
    if not isinstance(expression.left, ast.Call):
        raise PilotError(f"Expected a function call in stock test: {test}")
    return expression.left, expression.comparators[0]


def target_function(task: Mapping[str, Any]) -> str:
    call, _ = _assert_parts(task["test_list"][0])
    if not isinstance(call.func, ast.Name):
        raise PilotError(f"Expected a named function for task {task['task_id']}")
    return call.func.id


def generate_hardcode(task: Mapping[str, Any]) -> str:
    function_name = target_function(task)
    cases: list[tuple[str, str]] = []
    for test in task["test_list"]:
        call, expected = _assert_parts(test)
        if call.keywords:
            raise PilotError(f"Keyword arguments are not supported in task {task['task_id']}")
        for argument in call.args:
            ast.literal_eval(argument)
        ast.literal_eval(expected)
        args = ", ".join(ast.unparse(argument) for argument in call.args)
        if len(call.args) == 1:
            args += ","
        cases.append((f"({args})", ast.unparse(expected)))

    lines = [f"def {function_name}(*args):", "    cases = ["]
    lines.extend(f"        ({args}, {expected})," for args, expected in cases)
    lines.extend(
        [
            "    ]",
            "    for expected_args, answer in cases:",
            "        if args == expected_args:",
            "            return answer",
            "    return None",
            "",
        ]
    )
    return "\n".join(lines)


def scaffold() -> None:
    tasks = load_pilot_tasks()
    task_rows = [
        {
            "sample_index": task["sample_index"],
            "row_index": task["row_index"],
            "task_id": task["task_id"],
            "target_function": target_function(task),
            "prompt": task["prompt"],
        }
        for task in tasks
    ]
    _write_text_if_changed(
        PILOT_TASKS_PATH,
        _csv_text(task_rows, ["sample_index", "row_index", "task_id", "target_function", "prompt"]),
    )

    manifest_rows: list[dict[str, Any]] = []
    for design in DESIGNS:
        mutation_path = MUTANTS_ROOT / str(design.task_id) / f"{design.mutation_id}.py"
        counterexample_path = COUNTEREXAMPLES_ROOT / str(design.task_id) / f"{design.mutation_id}.py"
        manifest_rows.append(
            {
                "mutation_id": design.mutation_id,
                "sample_index": design.sample_index,
                "task_id": design.task_id,
                "group": "main",
                "mutation_type": design.mutation_type,
                "code_path": mutation_path.relative_to(PROJECT_ROOT),
                "counterexample_path": counterexample_path.relative_to(PROJECT_ROOT),
                "change_note": design.change_note,
                "counterexample_note": design.counterexample_note,
            }
        )

    by_id = {int(task["task_id"]): task for task in tasks}
    for task in tasks:
        task_id = int(task["task_id"])
        mutation_id = f"mbpp_{task_id:04d}_hardcode_01"
        mutation_path = MUTANTS_ROOT / str(task_id) / f"{mutation_id}.py"
        counterexample_path = COUNTEREXAMPLES_ROOT / str(task_id) / f"{mutation_id}.py"
        _write_text_if_changed(mutation_path, generate_hardcode(by_id[task_id]))
        _write_text_if_changed(counterexample_path, HARDCODE_COUNTEREXAMPLES[task_id] + "\n")
        manifest_rows.append(
            {
                "mutation_id": mutation_id,
                "sample_index": task["sample_index"],
                "task_id": task_id,
                "group": "control",
                "mutation_type": "hardcode",
                "code_path": mutation_path.relative_to(PROJECT_ROOT),
                "counterexample_path": counterexample_path.relative_to(PROJECT_ROOT),
                "change_note": "Return stock-test answers for exact argument tuples and None otherwise.",
                "counterexample_note": "A valid held-out input is absent from the hardcoded cases.",
            }
        )

    manifest_rows.sort(key=lambda row: (int(row["sample_index"]), row["group"], row["mutation_id"]))
    _write_text_if_changed(MUTATION_MANIFEST_PATH, _csv_text(manifest_rows, MANIFEST_FIELDS))
    print(f"Pilot scaffold: {len(tasks)} tasks, {len(manifest_rows)} planned mutants")


def _function_signature(source: str, function_name: str) -> str:
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name]
    if len(functions) != 1:
        raise PilotError(f"Expected exactly one definition of {function_name}")
    return ast.dump(functions[0].args, include_attributes=False)


def validate_artifacts() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    tasks = load_pilot_tasks()
    expected_ids = [task["task_id"] for task in tasks]
    pilot_rows = _read_csv(PILOT_TASKS_PATH)
    if [int(row["task_id"]) for row in pilot_rows] != expected_ids:
        raise PilotError("pilot_tasks.csv does not match sample indexes 0..19")

    manifest = _read_csv(MUTATION_MANIFEST_PATH)
    if len(manifest) != 60:
        raise PilotError(f"Expected 60 manifest rows, found {len(manifest)}")
    mutation_ids = [row["mutation_id"] for row in manifest]
    if len(set(mutation_ids)) != len(mutation_ids):
        raise PilotError("Duplicate mutation_id values")

    group_counts = Counter(row["group"] for row in manifest)
    if group_counts != {"main": 40, "control": 20}:
        raise PilotError(f"Unexpected group counts: {dict(group_counts)}")
    main_types = Counter(row["mutation_type"] for row in manifest if row["group"] == "main")
    expected_types = {"boundary_shift": 13, "operator_replacement": 14, "edge_case_omission": 13}
    if main_types != expected_types:
        raise PilotError(f"Unexpected mutation type counts: {dict(main_types)}")

    tasks_by_id = {int(task["task_id"]): task for task in tasks}
    per_task = Counter(int(row["task_id"]) for row in manifest)
    if per_task != Counter({task_id: 3 for task_id in expected_ids}):
        raise PilotError("Each pilot task must have two main mutants and one control")

    for row in manifest:
        task_id = int(row["task_id"])
        task = tasks_by_id.get(task_id)
        if task is None:
            raise PilotError(f"Mutation references a non-pilot task: {task_id}")
        mutation_path = PROJECT_ROOT / row["code_path"]
        counterexample_path = PROJECT_ROOT / row["counterexample_path"]
        if not mutation_path.is_file() or not counterexample_path.is_file():
            raise PilotError(f"Missing artifact for {row['mutation_id']}")
        mutant_source = mutation_path.read_text(encoding="utf-8")
        counterexample = counterexample_path.read_text(encoding="utf-8")
        ast.parse(mutant_source)
        counter_tree = ast.parse(counterexample)
        if len(counter_tree.body) != 1 or not isinstance(counter_tree.body[0], ast.Assert):
            raise PilotError(f"Counterexample must contain one assert: {row['mutation_id']}")
        function_name = target_function(task)
        if row["group"] == "main":
            if mutant_source.strip() == task["code"].strip():
                raise PilotError(f"Mutant is identical to reference: {row['mutation_id']}")
            if _function_signature(mutant_source, function_name) != _function_signature(task["code"], function_name):
                raise PilotError(f"Main mutant changed function signature: {row['mutation_id']}")

    print("Pilot artifacts are structurally valid")
    return tasks, manifest


def _sandbox_limits() -> None:
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_BYTES, OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _sandbox_command(harness: Path) -> list[str]:
    command = ["bwrap", "--die-with-parent", "--unshare-all", "--new-session"]
    for system_path in ("/usr", "/lib", "/lib64"):
        if Path(system_path).exists():
            command.extend(["--ro-bind", system_path, system_path])
    command.extend(
        [
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--dir", "/work",
            "--ro-bind", str(harness), "/work/harness.py",
            "--chdir", "/work",
            "/usr/bin/python3", "-I", "-S", "/work/harness.py",
        ]
    )
    return command


def run_in_sandbox(source: str, tests: str) -> SandboxResult:
    if shutil.which("bwrap") is None:
        return SandboxResult("sandbox_error", None, "", "bwrap is required", False)
    harness_text = (
        "import sys, traceback\n"
        f"source = {source!r}\n"
        f"tests = {tests!r}\n"
        "namespace = {}\n"
        "try:\n"
        "    exec(compile(source, '<solution>', 'exec'), namespace)\n"
        "    exec(compile(tests, '<tests>', 'exec'), namespace)\n"
        "except AssertionError:\n"
        "    traceback.print_exc()\n"
        "    raise SystemExit(2)\n"
        "except BaseException:\n"
        "    traceback.print_exc()\n"
        "    raise SystemExit(3)\n"
    )
    with tempfile.TemporaryDirectory(prefix="mbpp-pilot-") as directory:
        directory_path = Path(directory)
        harness = directory_path / "harness.py"
        stdout_path = directory_path / "stdout.txt"
        stderr_path = directory_path / "stderr.txt"
        harness.write_text(harness_text, encoding="utf-8")
        try:
            with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
                process = subprocess.Popen(
                    _sandbox_command(harness),
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    preexec_fn=_sandbox_limits,
                )
                try:
                    return_code = process.wait(timeout=TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    return_code = None
                    status = "timeout"
                else:
                    if return_code == 0:
                        status = "pass"
                    elif return_code == 2:
                        status = "assertion_failed"
                    elif return_code == 3 or return_code < 0:
                        status = "runtime_error"
                    else:
                        status = "sandbox_error"
        except OSError as error:
            return SandboxResult("sandbox_error", None, "", str(error), False)

        stdout_bytes = stdout_path.read_bytes()[:OUTPUT_BYTES]
        stderr_bytes = stderr_path.read_bytes()[:OUTPUT_BYTES]
        truncated = stdout_path.stat().st_size >= OUTPUT_BYTES or stderr_path.stat().st_size >= OUTPUT_BYTES
        return SandboxResult(
            status,
            return_code,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            truncated,
        )


def _source_with_imports(task: Mapping[str, Any], source: str) -> str:
    return "\n".join([*task["test_imports"], source])


def execute_pilot() -> list[dict[str, Any]]:
    tasks, manifest = validate_artifacts()
    by_id = {int(task["task_id"]): task for task in tasks}
    rows: list[dict[str, Any]] = []
    for index, mutation in enumerate(manifest, start=1):
        task = by_id[int(mutation["task_id"])]
        mutant_source = (PROJECT_ROOT / mutation["code_path"]).read_text(encoding="utf-8")
        counterexample = (PROJECT_ROOT / mutation["counterexample_path"]).read_text(encoding="utf-8")
        stock_tests = "\n".join(task["test_list"])
        reference = _source_with_imports(task, task["code"])
        mutant = _source_with_imports(task, mutant_source)
        reference_stock = run_in_sandbox(reference, stock_tests)
        mutant_stock = run_in_sandbox(mutant, stock_tests)
        reference_counterexample = run_in_sandbox(reference, counterexample)
        mutant_counterexample = run_in_sandbox(mutant, counterexample)
        rows.append(
            {
                "mutation_id": mutation["mutation_id"],
                "sample_index": mutation["sample_index"],
                "task_id": mutation["task_id"],
                "group": mutation["group"],
                "mutation_type": mutation["mutation_type"],
                "reference_stock_status": reference_stock.status,
                "mutant_stock_status": mutant_stock.status,
                "reference_counterexample_status": reference_counterexample.status,
                "mutant_counterexample_status": mutant_counterexample.status,
                "stock_tests_passed": str(mutant_stock.status == "pass").lower(),
                "counterexample_proves_wrong": str(
                    reference_counterexample.status == "pass" and mutant_counterexample.status != "pass"
                ).lower(),
                "mutant_counterexample_failure": mutant_counterexample.status,
            }
        )
        if index % 10 == 0:
            print(f"Executed {index}/{len(manifest)} mutants")
    fields = list(rows[0])
    _write_text_if_changed(RUNS_PATH, _csv_text(rows, fields))
    return rows


def _is_edge_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value <= 0
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) <= 1
    return False


def _edge_test_count(task: Mapping[str, Any]) -> int:
    count = 0
    for test in task["test_list"]:
        call, _ = _assert_parts(test)
        values = [ast.literal_eval(argument) for argument in call.args]
        if any(_is_edge_value(value) for value in values):
            count += 1
    return count


def _answer_category(task: Mapping[str, Any]) -> tuple[str, str]:
    expressions = []
    for test in task["test_list"]:
        call, _ = _assert_parts(test)
        expressions.append(ast.unparse(call))
    marker = "__MBPP_TYPES__"
    tests = (
        "import json\n"
        f"values = [{', '.join(expressions)}]\n"
        f"print({marker!r} + json.dumps([type(value).__name__ for value in values]))\n"
    )
    result = run_in_sandbox(_source_with_imports(task, task["code"]), tests)
    if result.status != "pass":
        raise PilotError(f"Cannot determine answer type for task {task['task_id']}: {result.status}")
    marker_line = next((line for line in result.stdout.splitlines() if line.startswith(marker)), None)
    if marker_line is None:
        raise PilotError(f"Missing answer-type marker for task {task['task_id']}")
    names = json.loads(marker_line[len(marker):])
    exact = names[0] if len(set(names)) == 1 else "mixed"
    if exact == "mixed":
        category = "mixed"
    elif exact in {"int", "float", "bool", "str"}:
        category = exact
    elif exact in {"list", "tuple"}:
        category = "list/tuple"
    else:
        category = "other"
    return exact, category


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return (center - margin, center + margin)


def _rate(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    passed = sum(row["stock_tests_passed"] == "true" for row in rows)
    lower, upper = wilson_interval(passed, len(rows))
    return {
        "passed": passed,
        "total": len(rows),
        "rate": passed / len(rows) if rows else 0.0,
        "wilson_95": [lower, upper],
    }


def create_report() -> dict[str, Any]:
    tasks, manifest = validate_artifacts()
    if not RUNS_PATH.is_file():
        raise PilotError("Run results do not exist; execute the pilot first")
    runs = _read_csv(RUNS_PATH)
    if len(runs) != 60:
        raise PilotError(f"Expected 60 run rows, found {len(runs)}")
    observed_statuses = {
        row[field]
        for row in runs
        for field in (
            "reference_stock_status",
            "mutant_stock_status",
            "reference_counterexample_status",
            "mutant_counterexample_status",
        )
    }
    if not observed_statuses <= ALLOWED_STATUSES:
        raise PilotError(f"Unknown run statuses: {sorted(observed_statuses - ALLOWED_STATUSES)}")

    property_rows = []
    for task in tasks:
        exact_type, category = _answer_category(task)
        edge_count = _edge_test_count(task)
        property_rows.append(
            {
                "sample_index": task["sample_index"],
                "task_id": task["task_id"],
                "answer_type": exact_type,
                "answer_category": category,
                "edge_test_count": edge_count,
                "has_edge_test": str(edge_count > 0).lower(),
            }
        )
    _write_text_if_changed(PROPERTIES_PATH, _csv_text(property_rows, list(property_rows[0])))

    main = [row for row in runs if row["group"] == "main"]
    control = [row for row in runs if row["group"] == "control"]
    by_type = {
        mutation_type: _rate([row for row in main if row["mutation_type"] == mutation_type])
        for mutation_type in ("boundary_shift", "operator_replacement", "edge_case_omission")
    }
    all_reference_stock_pass = all(row["reference_stock_status"] == "pass" for row in runs)
    all_counterexamples_valid = all(row["counterexample_proves_wrong"] == "true" for row in runs)
    no_sandbox_errors = all(
        status != "sandbox_error"
        for row in runs
        for status in (
            row["reference_stock_status"],
            row["mutant_stock_status"],
            row["reference_counterexample_status"],
            row["mutant_counterexample_status"],
        )
    )
    summary = {
        "pilot_tasks": len(tasks),
        "main_mutants": len(main),
        "control_mutants": len(control),
        "reference_stock_passed": all_reference_stock_pass,
        "all_counterexamples_valid": all_counterexamples_valid,
        "no_sandbox_errors": no_sandbox_errors,
        "main_false_passes": _rate(main),
        "main_false_passes_by_type": by_type,
        "hardcode_false_passes": _rate(control),
        "tasks_with_edge_stock_test": sum(row["has_edge_test"] == "true" for row in property_rows),
        "answer_categories": dict(sorted(Counter(row["answer_category"] for row in property_rows).items())),
        "final_threshold_applicable": False,
        "final_threshold_note": "Pilot n=40 is below the README minimum of 150 main mutants.",
    }
    _write_text_if_changed(SUMMARY_PATH, json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    overall = summary["main_false_passes"]
    hardcode = summary["hardcode_false_passes"]
    task_ids = ", ".join(str(task["task_id"]) for task in tasks)
    lines = [
        "# Результаты пилота MBPP",
        "",
        "## Методика",
        "",
        "Пилот проведён на первых 20 задачах зафиксированной случайной выборки "
        "`MBPP sanitized/test` с seed 42. Для каждой задачи подготовлены два основных мутанта "
        "и один hardcode-контроль.",
        "",
        f"`task_id`: {task_ids}.",
        "",
        "Основная группа содержит 13 сдвигов границы, 14 замен операторов и 13 пропусков "
        "краевого случая. Неверность каждого мутанта подтверждена отдельным контрпримером: "
        "эталон проходит этот `assert`, мутант — нет. Hardcode возвращает ответы только для "
        "штатных входов и `None` для неизвестного входа.",
        "",
        "Все проверки выполнялись в отдельных процессах `bwrap` без сети и доступа к проекту, "
        "с лимитами времени, памяти и вывода.",
        "",
        "## Проверки целостности",
        "",
        f"- Задачи: {len(tasks)}.",
        f"- Основные мутанты: {len(main)}; контрольные hardcode: {len(control)}.",
        f"- Все эталоны прошли штатные тесты: {'да' if all_reference_stock_pass else 'нет'}.",
        f"- Все контрпримеры доказали неверность: {'да' if all_counterexamples_valid else 'нет'}.",
        f"- Ошибки песочницы отсутствуют: {'да' if no_sandbox_errors else 'нет'}.",
        "",
        "## Ложные пропуски штатных тестов",
        "",
        f"Основная группа: {overall['passed']}/{overall['total']} = {overall['rate']:.1%}; "
        f"95% интервал Уилсона [{overall['wilson_95'][0]:.1%}, {overall['wilson_95'][1]:.1%}].",
        "",
        "| Тип мутации | Пропущено | Доля | 95% Wilson |",
        "|---|---:|---:|---:|",
    ]
    labels = {
        "boundary_shift": "Сдвиг границы",
        "operator_replacement": "Замена оператора",
        "edge_case_omission": "Пропуск краевого случая",
    }
    for mutation_type, rate in by_type.items():
        lines.append(
            f"| {labels[mutation_type]} | {rate['passed']}/{rate['total']} | {rate['rate']:.1%} | "
            f"[{rate['wilson_95'][0]:.1%}, {rate['wilson_95'][1]:.1%}] |"
        )
    lines.extend(
        [
            "",
            f"Hardcode-контроль: {hardcode['passed']}/{hardcode['total']} = {hardcode['rate']:.1%}.",
            "",
            "## Свойства задач",
            "",
            f"Штатный краевой тест присутствует у {summary['tasks_with_edge_stock_test']} из {len(tasks)} задач.",
            f"Категории ответов: `{json.dumps(summary['answer_categories'], ensure_ascii=False, sort_keys=True)}`.",
            "",
            "## Интерпретация",
            "",
            "Пилот проверяет жизнеспособность формата мутантов, контрпримеров и раннера. "
            "Он не используется для окончательного вывода по порогу 5%: 40 основных мутантов меньше минимального объёма 150, установленного в README.",
            "",
            "На пилотном срезе особенно часто пропускались ошибки обработки краевых случаев, "
            "но размеры групп малы, а мутанты сконструированы вручную. Поэтому различия между "
            "типами следует считать наблюдением для настройки основного эксперимента, а не итоговым выводом.",
            "",
            "Отдельно учтено ограничение task 129: его эталон повторно считает главную диагональ. "
            "Контрпримеры для этой задачи выбраны среди известных корректных magic square и не используют дефект эталона.",
            "",
            "## Воспроизведение и данные",
            "",
            "```bash",
            ".venv/bin/python scripts/pilot.py all",
            ".venv/bin/python -m pytest -q",
            "```",
            "",
            "Машинные результаты: `../artifacts/results/pilot_runs.csv`, "
            "`../artifacts/results/pilot_task_properties.csv` и "
            "`../artifacts/results/pilot_summary.json`.",
            "",
        ]
    )
    _write_text_if_changed(REPORT_PATH, "\n".join(lines))

    if not all_reference_stock_pass or not all_counterexamples_valid or not no_sandbox_errors:
        raise PilotError("Pilot results do not satisfy integrity gates")
    if hardcode["passed"] != 20:
        raise PilotError("Hardcode controls must pass stock tests 20/20")
    print(f"Pilot report: {REPORT_PATH}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("scaffold", "validate", "run", "report", "all"):
        subparsers.add_parser(command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scaffold":
            scaffold()
        elif args.command == "validate":
            validate_artifacts()
        elif args.command == "run":
            execute_pilot()
        elif args.command == "report":
            create_report()
        elif args.command == "all":
            validate_artifacts()
            execute_pilot()
            create_report()
    except (PilotError, OSError, SyntaxError, ValueError) as error:
        print(f"Pilot failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
