import os
from pathlib import Path
from typing import Any

try:
    from google import genai
except Exception:
    genai = None


class GeminiTestGenerator:
    """Generate pytest tests using Gemini, with a deterministic fallback mode."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model
        self._client = None

        if self.api_key and genai is not None:
            self._client = genai.Client(api_key=self.api_key)

    @property
    def can_use_ai(self) -> bool:
        return self._client is not None

    def generate(self, source_file: str, analysis: dict[str, Any]) -> str:
        source = Path(source_file).read_text(encoding="utf-8")
        if self.can_use_ai:
            return self._generate_with_ai(source, analysis)
        return self._generate_fallback(source, analysis)

    def _generate_with_ai(self, source: str, analysis: dict[str, Any]) -> str:
        prompt = (
            "Generate production-quality pytest tests for this Python module. "
            "IMPORTANT RULES:\n"
            "1. ONLY return valid Python code.\n"
            "2. DO NOT include markdown code blocks (like ```python).\n"
            "3. DO NOT include any explanations.\n\n"
            f"Code analysis:\n{analysis}\n\n"
            f"Target source code:\n{source}"
        )
        response = self._client.models.generate_content(model=self.model, contents=prompt)
        
        # AI eken ena uththaraye thiyena anawashya markdown (```python) makala clean karamu
        test_code = response.text
        test_code = test_code.replace("```python", "")
        test_code = test_code.replace("```", "")
        
        return test_code.strip()

    def _generate_fallback(self, source: str, analysis: dict[str, Any]) -> str:
        target_module = Path(analysis["file"]).stem
        function_tests: list[str] = []

        for fn in analysis.get("functions", []):
            fn_name = fn["name"]
            args = fn["args"]
            if len(args) == 2:
                function_tests.append(
                    "\n".join(
                        [
                            f"def test_{fn_name}_basic():",
                            f"    result = {fn_name}(1, 2)",
                            "    assert result is not None",
                        ]
                    )
                )
            elif len(args) == 1:
                function_tests.append(
                    "\n".join(
                        [
                            f"def test_{fn_name}_basic():",
                            f"    result = {fn_name}(1)",
                            "    assert result is not None",
                        ]
                    )
                )
            else:
                function_tests.append(
                    "\n".join(
                        [
                            f"def test_{fn_name}_callable():",
                            f"    assert callable({fn_name})",
                        ]
                    )
                )

        if not function_tests:
            function_tests.append(
                "\n".join(
                    [
                        "def test_module_imports():",
                        "    assert True",
                    ]
                )
            )

        return (
            "import pytest\n"
            f"from {target_module} import *\n\n"
            "\n\n".join(function_tests)
            + "\n"
        )