import ast
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class FunctionInfo:
    name: str
    args: list[str]
    has_docstring: bool
    line: int


@dataclass
class ClassInfo:
    name: str
    methods: list[str]
    line: int


def analyze_code(file_path: str) -> dict[str, Any]:
    """Analyze a Python file with AST and return a structured summary."""
    path = Path(file_path)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions_by_name: dict[str, FunctionInfo] = {}
    classes: list[ClassInfo] = []
    imports: list[str] = []
    shadowed_definitions: list[dict[str, Any]] = []

    # Only top-level definitions form the importable module surface.  Using
    # ast.walk here previously misreported methods and nested helpers as bare
    # module functions, which led the generator to create invalid calls.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [arg.arg for arg in node.args.args]
            if node.name in functions_by_name:
                previous = functions_by_name[node.name]
                shadowed_definitions.append(
                    {
                        "name": node.name,
                        "shadowed_line": previous.line,
                        "effective_line": node.lineno,
                    }
                )
            functions_by_name[node.name] = FunctionInfo(
                name=node.name,
                args=args,
                has_docstring=bool(ast.get_docstring(node)),
                line=node.lineno,
            )
        elif isinstance(node, ast.ClassDef):
            method_names = [
                child.name
                for child in node.body
                if isinstance(child, ast.FunctionDef)
            ]
            classes.append(
                ClassInfo(name=node.name, methods=method_names, line=node.lineno)
            )
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)

    functions = sorted(functions_by_name.values(), key=lambda item: item.line)

    analysis: dict[str, Any] = {
        "file": str(path),
        "line_count": len(source.splitlines()),
        "function_count": len(functions),
        "class_count": len(classes),
        "imports": sorted(set(imports)),
        "functions": [asdict(item) for item in functions],
        "classes": [asdict(item) for item in sorted(classes, key=lambda x: x.line)],
        "shadowed_definitions": shadowed_definitions,
    }
    return analysis
