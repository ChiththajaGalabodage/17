"""Small, dependency-free mutation testing evaluator for Python projects.

The evaluator deliberately favours traceability and safety over mutation
volume.  It creates one AST mutation at a time, executes tests against a copy
of the project in a temporary directory, and never writes to the original
source tree.

Timed-out mutants are reported separately and are excluded from the mutation
score.  A mutant is *killed* when pytest reports test failures or collection
errors, and *survived* when pytest exits successfully.  Syntactically invalid
mutants and unusable pytest outcomes are reported as invalid.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


_ARITHMETIC_REPLACEMENTS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
    ast.FloorDiv: ast.Mult,
    ast.Div: ast.Mult,
    ast.Mod: ast.Mult,
    ast.Pow: ast.Mult,
}

_COMPARISON_REPLACEMENTS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.GtE: ast.Lt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}

_BOOLEAN_REPLACEMENTS: dict[type[ast.boolop], type[ast.boolop]] = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}

_OPERATOR_TEXT: dict[type[ast.AST], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.FloorDiv: "//",
    ast.Div: "/",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.And: "and",
    ast.Or: "or",
    ast.Not: "not",
}

_CATEGORY_PREFIX = {
    "arithmetic": "AOR",
    "comparison": "COR",
    "boolean": "BOR",
}

_COPY_IGNORED_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "venv",
}

_SAFE_INHERITED_ENVIRONMENT = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TZ",
    "WINDIR",
}


class MutationEvaluationError(RuntimeError):
    """Base exception raised when an experiment cannot produce a verdict."""


class BaselineFailure(MutationEvaluationError):
    """Raised when the specified suite does not pass on the original source."""

    def __init__(self, result: "TestRunResult") -> None:
        self.result = result
        if result.timed_out:
            reason = f"timed out after {result.duration_seconds:.3f}s"
        else:
            reason = f"exited with return code {result.return_code}"
        super().__init__(f"Mutation baseline must pass before mutants run; baseline {reason}.")


@dataclass(frozen=True, slots=True)
class GeneratedMutant:
    """A deterministic, single-site source mutation."""

    mutant_id: str
    category: str
    lineno: int
    col_offset: int
    original_operator: str
    replacement_operator: str
    node_type: str
    source_excerpt: str
    source_code: str
    source_sha256: str

    def metadata(self) -> dict[str, Any]:
        """Return traceability fields without embedding the full source text."""

        data = asdict(self)
        data.pop("source_code")
        return data


@dataclass(frozen=True, slots=True)
class TestRunResult:
    """Captured result of one isolated pytest process."""

    command: tuple[str, ...]
    return_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.return_code == 0


@dataclass(frozen=True, slots=True)
class MutantResult:
    """Execution classification and metadata for a generated mutant."""

    mutant_id: str
    category: str
    lineno: int
    col_offset: int
    original_operator: str
    replacement_operator: str
    node_type: str
    source_excerpt: str
    source_sha256: str
    status: str
    test_run: TestRunResult | None = None
    invalid_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MutationReport:
    """Complete baseline and mutant results for one source/suite pair."""

    generated_at_utc: str
    source_path: str
    original_source_sha256: str
    test_targets: tuple[str, ...]
    timeout_seconds: float
    baseline: TestRunResult
    mutants: tuple[MutantResult, ...]
    python_version: str
    platform: str

    @property
    def valid_ids(self) -> tuple[str, ...]:
        """IDs that compiled and received a test verdict or timeout."""

        return tuple(item.mutant_id for item in self.mutants if item.status != "invalid")

    @property
    def invalid_ids(self) -> tuple[str, ...]:
        return tuple(item.mutant_id for item in self.mutants if item.status == "invalid")

    @property
    def timed_out_ids(self) -> tuple[str, ...]:
        return tuple(item.mutant_id for item in self.mutants if item.status == "timed_out")

    @property
    def killed_ids(self) -> tuple[str, ...]:
        return tuple(item.mutant_id for item in self.mutants if item.status == "killed")

    @property
    def survived_ids(self) -> tuple[str, ...]:
        return tuple(item.mutant_id for item in self.mutants if item.status == "survived")

    @property
    def mutation_score(self) -> float:
        """Killed / (killed + survived) as a percentage.

        Invalid and timed-out mutants are deliberately excluded instead of
        silently treating infrastructure uncertainty as evidence of a kill.
        """

        denominator = len(self.killed_ids) + len(self.survived_ids)
        if denominator == 0:
            return 0.0
        return round(len(self.killed_ids) / denominator * 100.0, 2)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report with explicit classification lists."""

        return {
            "generated_at_utc": self.generated_at_utc,
            "source_path": self.source_path,
            "original_source_sha256": self.original_source_sha256,
            "test_targets": list(self.test_targets),
            "timeout_seconds": self.timeout_seconds,
            "baseline": asdict(self.baseline),
            "mutants": [asdict(item) for item in self.mutants],
            "valid_mutant_ids": list(self.valid_ids),
            "invalid_mutant_ids": list(self.invalid_ids),
            "timed_out_mutant_ids": list(self.timed_out_ids),
            "killed_mutant_ids": list(self.killed_ids),
            "survived_mutant_ids": list(self.survived_ids),
            "mutation_score": self.mutation_score,
            "score_denominator": len(self.killed_ids) + len(self.survived_ids),
            "score_excludes_invalid_and_timed_out": True,
            "python_version": self.python_version,
            "platform": self.platform,
        }


@dataclass(frozen=True, slots=True)
class _PathStep:
    field: str
    index: int | None = None


@dataclass(frozen=True, slots=True)
class _Candidate:
    category: str
    path: tuple[_PathStep, ...]
    action: str
    field: str | None
    index: int | None
    replacement_type: type[ast.AST] | None
    original_operator: str
    replacement_operator: str
    lineno: int
    col_offset: int
    node_type: str
    source_excerpt: str


def generate_mutants(source_code: str, *, filename: str = "<mutation-target>") -> tuple[GeneratedMutant, ...]:
    """Generate deterministic arithmetic, comparison, and boolean mutants.

    Each result is produced from a fresh copy of the original AST.  Therefore,
    mutations never accumulate across results.
    """

    tree = ast.parse(source_code, filename=filename)
    candidates = tuple(_collect_candidates(tree, source_code))
    mutants: list[GeneratedMutant] = []

    for serial, candidate in enumerate(candidates, start=1):
        mutated_tree = copy.deepcopy(tree)
        _apply_candidate(mutated_tree, candidate)
        ast.fix_missing_locations(mutated_tree)
        mutated_source = ast.unparse(mutated_tree)
        if source_code.endswith("\n"):
            mutated_source += "\n"

        digest_input = (
            f"{candidate.category}|{candidate.path}|{candidate.action}|"
            f"{candidate.original_operator}|{candidate.replacement_operator}"
        ).encode("utf-8")
        site_digest = hashlib.sha256(digest_input).hexdigest()[:8]
        prefix = _CATEGORY_PREFIX[candidate.category]
        mutant_id = (
            f"{prefix}-{serial:04d}-L{candidate.lineno:04d}-"
            f"C{candidate.col_offset:03d}-{site_digest}"
        )
        mutants.append(
            GeneratedMutant(
                mutant_id=mutant_id,
                category=candidate.category,
                lineno=candidate.lineno,
                col_offset=candidate.col_offset,
                original_operator=candidate.original_operator,
                replacement_operator=candidate.replacement_operator,
                node_type=candidate.node_type,
                source_excerpt=candidate.source_excerpt,
                source_code=mutated_source,
                source_sha256=_sha256_text(mutated_source),
            )
        )

    return tuple(mutants)


def evaluate_mutations(
    source_path: str | Path,
    test_targets: Sequence[str | Path],
    *,
    project_root: str | Path | None = None,
    timeout_seconds: float = 30.0,
    max_mutants: int | None = None,
) -> MutationReport:
    """Run traceable mutants against a pytest suite in isolated project copies.

    ``test_targets`` accepts project-relative pytest files/directories and
    optional node IDs such as ``tests/test_api.py::test_create``.  The suite is
    first executed against an untouched copy of the original project.  A
    :class:`BaselineFailure` aborts the experiment when that run fails or times
    out, preventing already-broken software from inflating the mutation score.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if max_mutants is not None and max_mutants <= 0:
        raise ValueError("max_mutants must be greater than zero when provided")
    if not test_targets:
        raise ValueError("At least one pytest target is required")

    root, source, relative_source, normalized_targets = _resolve_inputs(
        source_path=source_path,
        test_targets=test_targets,
        project_root=project_root,
    )
    original_source = source.read_text(encoding="utf-8")

    baseline = _run_isolated_suite(
        project_root=root,
        test_targets=normalized_targets,
        timeout_seconds=timeout_seconds,
    )
    if not baseline.passed:
        raise BaselineFailure(baseline)

    generated = generate_mutants(original_source, filename=relative_source.as_posix())
    if max_mutants is not None:
        generated = generated[:max_mutants]

    results: list[MutantResult] = []
    for mutant in generated:
        try:
            compile(mutant.source_code, relative_source.as_posix(), "exec")
        except (SyntaxError, ValueError, TypeError) as exc:
            results.append(_invalid_result(mutant, f"compile failed: {exc}"))
            continue

        test_run = _run_isolated_suite(
            project_root=root,
            test_targets=normalized_targets,
            timeout_seconds=timeout_seconds,
            replacement=(relative_source, mutant.source_code),
        )
        if test_run.timed_out:
            status = "timed_out"
            invalid_reason = None
        elif test_run.return_code == 0:
            status = "survived"
            invalid_reason = None
        elif test_run.return_code in {1, 2}:
            # Pytest uses 1 for test failures and commonly 2 for collection
            # failures caused by importing a mutated module.  Both demonstrate
            # that the suite rejected the mutant.
            status = "killed"
            invalid_reason = None
        else:
            status = "invalid"
            invalid_reason = f"pytest produced unusable return code {test_run.return_code}"

        results.append(
            MutantResult(
                **mutant.metadata(),
                status=status,
                test_run=test_run,
                invalid_reason=invalid_reason,
            )
        )

    return MutationReport(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        source_path=relative_source.as_posix(),
        original_source_sha256=_sha256_text(original_source),
        test_targets=normalized_targets,
        timeout_seconds=float(timeout_seconds),
        baseline=baseline,
        mutants=tuple(results),
        python_version=platform.python_version(),
        platform=platform.platform(),
    )


def _collect_candidates(tree: ast.AST, source_code: str) -> Iterable[_Candidate]:
    candidates: list[_Candidate] = []

    def add_operator_candidate(
        *,
        node: ast.AST,
        path: tuple[_PathStep, ...],
        category: str,
        action: str,
        original_type: type[ast.AST],
        replacement_type: type[ast.AST],
        field: str,
        index: int | None = None,
    ) -> None:
        candidates.append(
            _Candidate(
                category=category,
                path=path,
                action=action,
                field=field,
                index=index,
                replacement_type=replacement_type,
                original_operator=_OPERATOR_TEXT[original_type],
                replacement_operator=_OPERATOR_TEXT[replacement_type],
                lineno=int(getattr(node, "lineno", 0) or 0),
                col_offset=int(getattr(node, "col_offset", 0) or 0),
                node_type=type(node).__name__,
                source_excerpt=_source_excerpt(source_code, node),
            )
        )

    def visit(node: ast.AST, path: tuple[_PathStep, ...]) -> None:
        if isinstance(node, (ast.BinOp, ast.AugAssign)):
            original_type = type(node.op)
            replacement = _ARITHMETIC_REPLACEMENTS.get(original_type)
            if replacement is not None:
                add_operator_candidate(
                    node=node,
                    path=path,
                    category="arithmetic",
                    action="set_field_operator",
                    original_type=original_type,
                    replacement_type=replacement,
                    field="op",
                )

        if isinstance(node, ast.Compare):
            for index, operator in enumerate(node.ops):
                original_type = type(operator)
                replacement = _COMPARISON_REPLACEMENTS.get(original_type)
                if replacement is not None:
                    add_operator_candidate(
                        node=node,
                        path=path,
                        category="comparison",
                        action="set_list_operator",
                        original_type=original_type,
                        replacement_type=replacement,
                        field="ops",
                        index=index,
                    )

        if isinstance(node, ast.BoolOp):
            original_type = type(node.op)
            replacement = _BOOLEAN_REPLACEMENTS.get(original_type)
            if replacement is not None:
                # Python stores ``a and b and c`` as one BoolOp node.  Build a
                # candidate per connector so a mutant changes one textual and
                # semantic site instead of changing every connector at once.
                for connector_index in range(len(node.values) - 1):
                    add_operator_candidate(
                        node=node,
                        path=path,
                        category="boolean",
                        action="mutate_bool_connector",
                        original_type=original_type,
                        replacement_type=replacement,
                        field="op",
                        index=connector_index,
                    )

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            candidates.append(
                _Candidate(
                    category="boolean",
                    path=path,
                    action="remove_not",
                    field=None,
                    index=None,
                    replacement_type=None,
                    original_operator="not",
                    replacement_operator="identity",
                    lineno=int(getattr(node, "lineno", 0) or 0),
                    col_offset=int(getattr(node, "col_offset", 0) or 0),
                    node_type=type(node).__name__,
                    source_excerpt=_source_excerpt(source_code, node),
                )
            )

        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            candidates.append(
                _Candidate(
                    category="boolean",
                    path=path,
                    action="toggle_boolean_constant",
                    field="value",
                    index=None,
                    replacement_type=None,
                    original_operator=str(node.value),
                    replacement_operator=str(not node.value),
                    lineno=int(getattr(node, "lineno", 0) or 0),
                    col_offset=int(getattr(node, "col_offset", 0) or 0),
                    node_type=type(node).__name__,
                    source_excerpt=_source_excerpt(source_code, node),
                )
            )

        for field, value in ast.iter_fields(node):
            if isinstance(value, ast.AST):
                visit(value, path + (_PathStep(field),))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        visit(item, path + (_PathStep(field, index),))

    visit(tree, ())
    return candidates


def _apply_candidate(tree: ast.AST, candidate: _Candidate) -> None:
    target = _node_at_path(tree, candidate.path)
    if candidate.action == "set_field_operator":
        if candidate.field is None or candidate.replacement_type is None:
            raise AssertionError("Malformed operator candidate")
        setattr(target, candidate.field, candidate.replacement_type())
        return
    if candidate.action == "set_list_operator":
        if candidate.field is None or candidate.index is None or candidate.replacement_type is None:
            raise AssertionError("Malformed comparison candidate")
        values = getattr(target, candidate.field)
        values[candidate.index] = candidate.replacement_type()
        return
    if candidate.action == "mutate_bool_connector":
        if (
            not isinstance(target, ast.BoolOp)
            or candidate.index is None
            or candidate.replacement_type is None
        ):
            raise AssertionError("Malformed boolean connector candidate")
        original_type = type(target.op)
        expression: ast.expr = copy.deepcopy(target.values[0])
        for connector_index, value in enumerate(target.values[1:]):
            operator_type = candidate.replacement_type if connector_index == candidate.index else original_type
            expression = ast.BoolOp(
                op=operator_type(),
                values=[expression, copy.deepcopy(value)],
            )
        ast.copy_location(expression, target)
        _replace_node_at_path(tree, candidate.path, expression)
        return
    if candidate.action == "toggle_boolean_constant":
        if not isinstance(target, ast.Constant) or not isinstance(target.value, bool):
            raise AssertionError("Boolean candidate no longer points to a boolean constant")
        target.value = not target.value
        return
    if candidate.action == "remove_not":
        if not isinstance(target, ast.UnaryOp) or not isinstance(target.op, ast.Not):
            raise AssertionError("Boolean candidate no longer points to a not expression")
        replacement = ast.copy_location(copy.deepcopy(target.operand), target)
        _replace_node_at_path(tree, candidate.path, replacement)
        return
    raise AssertionError(f"Unsupported mutation action: {candidate.action}")


def _node_at_path(tree: ast.AST, path: tuple[_PathStep, ...]) -> ast.AST:
    current: ast.AST = tree
    for step in path:
        value = getattr(current, step.field)
        current = value if step.index is None else value[step.index]
        if not isinstance(current, ast.AST):
            raise AssertionError(f"AST path did not resolve to a node: {path}")
    return current


def _replace_node_at_path(tree: ast.AST, path: tuple[_PathStep, ...], replacement: ast.AST) -> None:
    if not path:
        raise AssertionError("Cannot replace the AST root")
    parent = _node_at_path(tree, path[:-1])
    last = path[-1]
    if last.index is None:
        setattr(parent, last.field, replacement)
    else:
        getattr(parent, last.field)[last.index] = replacement


def _resolve_inputs(
    *,
    source_path: str | Path,
    test_targets: Sequence[str | Path],
    project_root: str | Path | None,
) -> tuple[Path, Path, Path, tuple[str, ...]]:
    raw_source = Path(source_path)
    if project_root is None:
        root = Path.cwd().resolve()
        if raw_source.is_absolute():
            root = raw_source.resolve().parent
    else:
        root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project root not found: {root}")

    source = raw_source.resolve() if raw_source.is_absolute() else (root / raw_source).resolve()
    relative_source = _relative_to_root(source, root, "Source path")
    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() != ".py":
        raise ValueError("Mutation source must be a Python file")

    normalized_targets: list[str] = []
    for raw_target in test_targets:
        target_text = os.fspath(raw_target)
        if not target_text or target_text.startswith("-"):
            raise ValueError(f"Unsafe or empty pytest target: {target_text!r}")
        file_part, separator, node_part = target_text.partition("::")
        candidate = Path(file_part)
        target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        relative_target = _relative_to_root(target, root, "Test target")
        if not target.exists():
            raise FileNotFoundError(f"Test target not found: {target}")
        normalized = relative_target.as_posix()
        if separator:
            if not node_part or "\x00" in node_part:
                raise ValueError(f"Invalid pytest node ID: {target_text!r}")
            normalized += f"::{node_part}"
        normalized_targets.append(normalized)

    return root, source, relative_source, tuple(normalized_targets)


def _relative_to_root(path: Path, root: Path, label: str) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the project root: {path}") from exc


def _run_isolated_suite(
    *,
    project_root: Path,
    test_targets: tuple[str, ...],
    timeout_seconds: float,
    replacement: tuple[Path, str] | None = None,
) -> TestRunResult:
    with tempfile.TemporaryDirectory(prefix="mutation-eval-") as temporary:
        isolation_root = Path(temporary)
        copied_project = isolation_root / "project"
        shutil.copytree(project_root, copied_project, ignore=_copy_ignore)

        if replacement is not None:
            relative_source, source_code = replacement
            copied_source = copied_project / relative_source
            copied_source.parent.mkdir(parents=True, exist_ok=True)
            copied_source.write_text(source_code, encoding="utf-8")

        isolated_home = isolation_root / "home"
        isolated_temp = isolation_root / "tmp"
        isolated_home.mkdir()
        isolated_temp.mkdir()
        environment = _scrubbed_environment(
            project_root=copied_project,
            home=isolated_home,
            temporary=isolated_temp,
        )
        command = (sys.executable, "-m", "pytest", "-q", *test_targets)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=copied_project,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            return TestRunResult(
                command=command,
                return_code=None,
                timed_out=True,
                duration_seconds=round(duration, 3),
                stdout=_timeout_output(exc.stdout),
                stderr=_timeout_output(exc.stderr),
            )
        except OSError as exc:
            raise MutationEvaluationError(f"Could not start isolated pytest: {exc}") from exc

        return TestRunResult(
            command=command,
            return_code=completed.returncode,
            timed_out=False,
            duration_seconds=round(time.monotonic() - started, 3),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    parent = Path(directory)
    for name in names:
        candidate = parent / name
        if name in _COPY_IGNORED_NAMES or name == ".env" or name.startswith(".env."):
            ignored.add(name)
        elif candidate.is_symlink():
            # A symlink could escape the project root or expose host data.
            ignored.add(name)
    return ignored


def _scrubbed_environment(*, project_root: Path, home: Path, temporary: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.upper() in _SAFE_INHERITED_ENVIRONMENT:
            environment[key] = value

    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMP": str(temporary),
            "TEMP": str(temporary),
            "TMPDIR": str(temporary),
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(project_root),
        }
    )
    return environment


def _invalid_result(mutant: GeneratedMutant, reason: str) -> MutantResult:
    return MutantResult(
        **mutant.metadata(),
        status="invalid",
        invalid_reason=reason,
    )


def _source_excerpt(source_code: str, node: ast.AST, limit: int = 160) -> str:
    excerpt = ast.get_source_segment(source_code, node) or type(node).__name__
    compact = " ".join(excerpt.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


__all__ = [
    "BaselineFailure",
    "GeneratedMutant",
    "MutantResult",
    "MutationEvaluationError",
    "MutationReport",
    "TestRunResult",
    "evaluate_mutations",
    "generate_mutants",
]
