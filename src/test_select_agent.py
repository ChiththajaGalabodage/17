import ast
import subprocess
import sys
from pathlib import Path


class TestSelectAgent:
    """Select the smallest relevant pytest set for changed source files."""

    def __init__(self, repo_root: str = ".") -> None:
        self.repo_root = Path(repo_root).resolve()

    def get_changed_files(self, base_ref: str = "HEAD~1") -> list[str]:
        """Return changed files from git; includes staged and unstaged changes."""
        commands = [
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            ["git", "diff", "--name-only", "--cached"],
            ["git", "diff", "--name-only"],
        ]
        changed: set[str] = set()

        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    cwd=str(self.repo_root),
                    check=False,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    for line in completed.stdout.splitlines():
                        value = line.strip()
                        if value:
                            changed.add(value.replace("\\", "/"))
            except Exception:
                # If git is unavailable, we keep fallback behavior in caller.
                continue

        return sorted(changed)

    def select_tests(self, changed_files: list[str], tests_dir: str = "tests") -> list[str]:
        """Predict impacted tests from changed files using import and symbol matching."""
        test_root = self.repo_root / tests_dir
        if not test_root.exists():
            return []

        test_files = sorted(str(path) for path in test_root.glob("test_*.py"))
        if not test_files:
            return []

        changed_modules = self._extract_changed_modules(changed_files)
        changed_symbols = self._extract_changed_symbols(changed_files)

        selected: set[str] = set()
        for test_file in test_files:
            test_path = Path(test_file)
            relative = test_path.relative_to(self.repo_root).as_posix()

            if relative in changed_files:
                selected.add(relative)
                continue

            imports = self._extract_imported_modules(test_path)
            if imports.intersection(changed_modules):
                selected.add(relative)
                continue

            source = test_path.read_text(encoding="utf-8")
            if changed_symbols and any(symbol in source for symbol in changed_symbols):
                selected.add(relative)

        if selected:
            return sorted(selected)

        # Safety fallback: run all tests if we cannot predict any impacted file.
        return [Path(file_path).relative_to(self.repo_root).as_posix() for file_path in test_files]

    def _extract_changed_modules(self, changed_files: list[str]) -> set[str]:
        modules: set[str] = set()
        for file_path in changed_files:
            if not file_path.endswith(".py"):
                continue
            path = Path(file_path)
            if path.name.startswith("test_"):
                continue
            modules.add(path.stem)
        return modules

    def _extract_changed_symbols(self, changed_files: list[str]) -> set[str]:
        symbols: set[str] = set()
        for file_path in changed_files:
            if not file_path.endswith(".py"):
                continue
            absolute = self.repo_root / file_path
            if not absolute.exists() or absolute.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(absolute.read_text(encoding="utf-8"))
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.add(node.name)
        return symbols

    def _extract_imported_modules(self, file_path: Path) -> set[str]:
        imports: set[str] = set()
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except Exception:
            return imports

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        return imports


def run_selected_tests(selected_tests: list[str]) -> int:
    """Helper entry-point for quick local usage."""
    if not selected_tests:
        return 0
    command = [sys.executable, "-m", "pytest", "-q", *selected_tests]
    completed = subprocess.run(command, check=False)
    return completed.returncode