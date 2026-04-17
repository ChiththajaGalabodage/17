# AI Test Generator

A modular Python pipeline that analyzes target source code, generates pytest tests using Gemini (with a local fallback), executes tests, self-heals failing tests, and emits JSON reports.

## Project Structure

- `.github/workflows/pipeline.yml`: GitHub Actions pipeline orchestrator
- `src/analyzer.py`: AST-based code analyzer
- `src/generator.py`: Gemini test generator + fallback generator
- `src/runner.py`: Pytest execution wrapper
- `src/healer.py`: Self-healing test logic
- `src/reporter.py`: Report builder/writer
- `main.py`: Pipeline entry point
- `requirements.txt`: Dependencies

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Optionally set Gemini key for AI generation:

```bash
set GEMINI_API_KEY=your_api_key_here
```

If no key is set, the pipeline still runs using deterministic fallback test generation.

## Usage

```bash
python main.py --source target_code.py --max-heal-attempts 2
```

Useful flags:

- `--test-output tests/test_generated.py`
- `--report-output reports/report.json`
- `--model gemini-2.5-flash`

## CI

The GitHub Actions workflow installs dependencies, runs the pipeline, and uploads generated artifacts:

- `tests/test_generated.py`
- `reports/report.json`
