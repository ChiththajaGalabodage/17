from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from src.output_format import (
    module_name_from_path,
    parse_generation_bundle,
)

try:
    from google import genai
except Exception:  # pragma: no cover - optional provider dependency
    genai = None


class GeminiTestGenerator:
    """Generate structured pytest suites and preserve provider provenance.

    The deterministic fallback is intentionally small and exists only to
    verify the harness without network access. Unknown semantics are skipped,
    causing the quality validator to reject the candidate rather than
    fabricating a weak oracle.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        *,
        temperature: float = 0.2,
        seed: int | None = 4885,
    ) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY") if api_key is None else api_key
        self.model = model
        self.temperature = float(temperature)
        self.seed = seed
        self._client = None
        self._api_calls = 0
        self._last_backend = "not-run"
        self._fallback_reason: str | None = None
        self.api_usage_records: list[dict[str, Any]] = []

        if self.api_key and genai is not None:
            self._client = genai.Client(api_key=self.api_key)

    @property
    def can_use_ai(self) -> bool:
        return self._client is not None

    def generate(self, source_file: str, analysis: dict[str, Any]) -> dict[str, Any]:
        source = Path(source_file).read_text(encoding="utf-8")
        started = time.perf_counter()
        self._api_calls = 0
        self.api_usage_records = []
        self._fallback_reason = None

        if self.can_use_ai:
            bundle = self._generate_with_ai(source, analysis)
        else:
            self._last_backend = "deterministic-fallback"
            self._fallback_reason = "GEMINI_API_KEY or google-genai client unavailable"
            bundle = self._generate_fallback(source, analysis)

        prompt = self._build_prompt(source, analysis)
        bundle["provenance"] = {
            "backend": self._last_backend,
            "model": self.model if self._last_backend == "gemini" else None,
            "api_calls": self._api_calls,
            "fallback_reason": self._fallback_reason,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "temperature": self.temperature,
            "seed": self.seed,
            "api_usage_records": list(self.api_usage_records),
        }
        return bundle

    def _build_prompt(self, source: str, analysis: dict[str, Any]) -> str:
        instructions = (
            "TestGenAgent Prompt (HIGH-QUALITY)\n"
            "You are a senior Python test engineer. Generate production-quality pytest tests for the supplied module.\n\n"
            "STRICT REQUIREMENTS:\n"
            "- DO NOT use \"assert result is not None\"\n"
            "- Every test must validate real expected outputs\n"
            "- Use correct data types for inputs\n"
            "- Each testable public function should have a normal, edge, and documented failure case where applicable\n"
            "- Use meaningful unique test names\n"
            "- Tests must be deterministic and must not access network, credentials, shell commands, or files outside pytest temporary fixtures\n"
            "- Do not alter or mock away the behaviour being tested\n\n"
            "OUTPUT FORMAT:\n"
            "- Return only a JSON object with exactly two keys\n"
            "- test_code: an array of Python source lines\n"
            "- explanation: an array of concise design bullets\n"
            "- No markdown fences or extra keys\n"
        )
        return (
            f"{instructions}\nCode analysis:\n{analysis}\n\n"
            f"Target source code:\n{source}"
        )

    def _generate_with_ai(
        self,
        source: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = self._build_prompt(source, analysis)
        max_retries = 3
        retry_delay_seconds = 5
        for attempt in range(1, max_retries + 1):
            try:
                self._api_calls += 1
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={
                        "temperature": self.temperature,
                        "seed": self.seed,
                        "response_mime_type": "application/json",
                    },
                )
                self.record_api_response(response, phase="generation")
                bundle = parse_generation_bundle(response.text or "")
                if bundle["test_code"].strip():
                    self._last_backend = "gemini"
                    return bundle
                raise ValueError("Empty response from API")
            except Exception as error:
                print(f"API Error (Attempt {attempt}/{max_retries}): {error}")
                if attempt < max_retries:
                    print(f"Retrying in {retry_delay_seconds} seconds...")
                    time.sleep(retry_delay_seconds)
                    continue
                self._last_backend = "deterministic-fallback"
                self._fallback_reason = (
                    f"Gemini generation failed after {max_retries} attempts: {error}"
                )
                print("Max retries reached. Using the harness-only fallback.")
                return self._generate_fallback(source, analysis)
        return self._generate_fallback(source, analysis)

    def record_api_response(self, response: Any, *, phase: str) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            usage_payload: Any = None
        elif hasattr(usage, "model_dump"):
            usage_payload = usage.model_dump(mode="json")
        elif isinstance(usage, dict):
            usage_payload = usage
        else:
            usage_payload = str(usage)
        self.api_usage_records.append(
            {
                "phase": phase,
                "usage": usage_payload,
                "model_version": getattr(response, "model_version", None),
                "response_id": getattr(response, "response_id", None),
            }
        )

    def _generate_fallback(
        self,
        source: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        del source  # fallback uses only the explicit harness surface
        if self._last_backend == "not-run":
            self._last_backend = "deterministic-fallback"
        module_name = module_name_from_path(Path(analysis["file"]))
        tests = self._build_harness_tests(analysis)
        if not tests:
            tests = [
                "def test_generation_requires_semantic_oracle():\n"
                "    pytest.skip('No deterministic semantic oracle is available; configure the LLM backend')"
            ]
        return {
            "test_code": (
                "import pytest\n"
                "from datetime import datetime\n"
                f"from {module_name} import *\n\n"
                + "\n\n".join(tests)
                + "\n"
            ),
            "explanation": [
                "Harness-only deterministic fallback; this run is not LLM evidence.",
                "Unknown semantic contracts are skipped so the validator fails closed.",
            ],
        }

    def _build_harness_tests(self, analysis: dict[str, Any]) -> list[str]:
        tests: list[str] = []
        for function in analysis.get("functions", []):
            name = str(function.get("name", ""))
            if name == "main":
                continue
            if name == "add":
                tests.append(
                    "def test_add_returns_the_sum_of_two_numbers():\n"
                    "    assert add(1, 2) == 3\n"
                    "    assert add(-4, 9) == 5"
                )
            elif name == "subtract":
                tests.append(
                    "def test_subtract_returns_the_difference_of_two_numbers():\n"
                    "    assert subtract(7, 2) == 5\n"
                    "    assert subtract(3, 8) == -5"
                )
            elif name == "multiply":
                tests.append(
                    "def test_multiply_returns_the_product_of_two_numbers():\n"
                    "    assert multiply(3, 4) == 12\n"
                    "    assert multiply(-2, 5) == -10"
                )
            elif name == "divide":
                tests.append(
                    "def test_divide_returns_quotient_and_handles_zero():\n"
                    "    assert divide(8, 2) == 4\n"
                    "    assert divide(9, 3) == 3\n"
                    "    assert divide(5, 0) == 'Error: Cannot divide by zero!'"
                )
            elif name == "reset_demo_state":
                tests.append(
                    "def test_reset_demo_state_is_repeatable():\n"
                    "    assert reset_demo_state() is None\n"
                    "    assert reset_demo_state() is None"
                )
            elif name == "get_user_age":
                tests.append(
                    "def test_get_user_age_returns_a_realistic_integer():\n"
                    "    result = get_user_age()\n"
                    "    assert isinstance(result, int)\n"
                    "    assert 0 <= result <= 130"
                )
            elif name == "get_first_item":
                tests.append(
                    "def test_get_first_item_returns_the_first_value():\n"
                    "    assert get_first_item([10, 20, 30]) == 10\n"
                    "    assert get_first_item(['a', 'b']) == 'a'"
                )
            else:
                safe_name = name if name.isidentifier() else "unknown_callable"
                tests.append(
                    f"def test_{safe_name}_requires_llm_semantic_oracle():\n"
                    f"    pytest.skip('No deterministic semantic oracle is available for {safe_name}')"
                )
        return tests
