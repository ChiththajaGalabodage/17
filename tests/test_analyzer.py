from pathlib import Path

from src.analyzer import analyze_code


def test_analyzer_reports_only_effective_top_level_functions(tmp_path: Path) -> None:
    source = tmp_path / "subject.py"
    source.write_text(
        "def duplicate(value):\n"
        "    def nested():\n"
        "        return value\n"
        "    return nested()\n\n"
        "class Service:\n"
        "    def method(self):\n"
        "        return 1\n\n"
        "def duplicate(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )

    result = analyze_code(str(source))

    assert result["function_count"] == 1
    assert result["functions"][0]["name"] == "duplicate"
    assert result["functions"][0]["line"] == 10
    assert result["classes"][0]["methods"] == ["method"]
    assert result["shadowed_definitions"] == [
        {"name": "duplicate", "shadowed_line": 1, "effective_line": 10}
    ]
