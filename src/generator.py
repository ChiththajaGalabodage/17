import os
import time
from pathlib import Path
from typing import Any

from src.output_format import build_fallback_explanation, parse_generation_bundle

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

    def generate(self, source_file: str, analysis: dict[str, Any]) -> dict[str, Any]:
        source = Path(source_file).read_text(encoding="utf-8")
        if self.can_use_ai:
            return self._generate_with_ai(source, analysis)
        return self._generate_fallback(source, analysis)

    def _build_prompt(self, source: str, analysis: dict[str, Any]) -> str:
        return (
            "Generate production-quality pytest tests for this Python module.\n"
            "Return ONLY a JSON object with exactly these keys:\n"
            "{\n"
            '  "test_code": ["import pytest", "...pytest code lines..."],\n'
            '  "explanation": ["concise bullet 1", "concise bullet 2"]\n'
            "}\n\n"
            "Rules:\n"
            "1. test_code must be a JSON array of strings, one per line of pytest code.\n"
            "2. explanation must be a JSON array of short, readable bullets.\n"
            "3. Do not include markdown fences, prose, or extra keys.\n"
            "4. Keep the code executable as-is.\n\n"
            f"Code analysis:\n{analysis}\n\n"
            f"Target source code:\n{source}"
        )

    def _generate_with_ai(self, source: str, analysis: dict[str, Any]) -> dict[str, Any]:
        prompt = self._build_prompt(source, analysis)
        max_retries = 3
        retry_delay_seconds = 5

        for attempt in range(1, max_retries + 1):
            try:
                response = self._client.models.generate_content(model=self.model, contents=prompt)
                bundle = parse_generation_bundle(response.text or "")
                if bundle["test_code"]:
                    return bundle
                raise ValueError("Empty response from API")
            except Exception as error:
                print(f"API Error (Attempt {attempt}/{max_retries}): {error}")
                if attempt < max_retries:
                    print(f"Retrying in {retry_delay_seconds} seconds...")
                    time.sleep(retry_delay_seconds)
                else:
                    print("Max retries reached. Using fallback generator.")
                    return self._generate_fallback(source, analysis)

        return self._generate_fallback(source, analysis)

    def _generate_fallback(self, source: str, analysis: dict[str, Any]) -> dict[str, Any]:
        target_module = Path(analysis["file"]).stem
        function_tests: list[str] = []
        explanation_lines = build_fallback_explanation(analysis)

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

        return {
            "test_code": (
                "import pytest\n"
                f"from {target_module} import *\n\n"
                "\n\n".join(function_tests)
                + "\n"
            ),
            "explanation": explanation_lines,
        }