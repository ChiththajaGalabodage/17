pyth# AI Test Generator

A modular agentic testing framework for CI/CD that combines three autonomous agents:

- Test generation agent (LLM + deterministic fallback)
- Predictive test selection agent (change-impact based)
- Self-healing agent (repairs failing generated tests)

The framework can be executed standalone (`main.py`) or benchmarked against a traditional pipeline (`scripts/compare_methods.py`).

## Phase-Based Workflow

### 1) Design Agentic Framework

The framework is split into isolated modules so each capability can evolve independently:

- `src/analyzer.py`: Extracts code structure via AST
- `src/generator.py`: Generates tests with Gemini or deterministic fallback
- `src/test_select_agent.py`: Selects impacted tests from code changes
- `src/healer.py`: Repairs failing generated tests
- `src/runner.py`: Executes selected tests
- `src/reporter.py`: Writes structured run reports
- `src/pipeline_tracker.py`: Tracks stage-by-stage lifecycle events

This modular design makes the pipeline easy to plug into CI/CD systems and easy to extend with additional agents.

### 2) Deploy Into CI/CD

Deployment options:

- Single agentic run: `python main.py --source target_code.py --max-heal-attempts 2 --predictive-test-selection`
- Experiment mode (agentic vs traditional): `python scripts/compare_methods.py --source target_code.py --runs 3`

The workflow in `.github/workflows/pipeline.yml` runs both modes and uploads artifacts.

### 3) Compare with Traditional Methods

Comparison script:

- Runs agentic pipeline for `N` iterations
- Runs traditional pytest baseline for `N` iterations
- Captures metrics per run:
  - Execution time
  - Test counts (passed/failed/total)
  - Defects detected (failed + errors)
  - Coverage percentage (line coverage)
  - Selected-test footprint

Outputs:

- `reports/comparison_report.json`
- `reports/comparison_report.csv`
- `reports/comparison_report.md`

### 4) Analyze Results

Use the generated reports for quantitative and qualitative analysis:

- Quantitative: pass rate, average duration, average coverage, defect detection
- Qualitative: healing behavior, selected test subset size, and per-run execution details

The summary includes a direct delta section (`Agentic - Traditional`) to evaluate effectiveness, efficiency, and reliability.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Optional (for LLM-backed generation):

```bash
set GEMINI_API_KEY=your_api_key_here
```

If no key is set, generation and healing still execute with deterministic local fallback behavior.

## Usage

### Agentic Pipeline

```bash
python main.py --source target_code.py --max-heal-attempts 2 --predictive-test-selection
```

Useful flags:

- `--test-output tests/test_generated.py`
- `--report-output reports/report.json`
- `--model gemini-2.5-flash`
- `--base-ref HEAD~1`

### Agentic vs Traditional Benchmark

```bash
python scripts/compare_methods.py --source target_code.py --runs 3
```

Useful flags:

- `--report-output reports/comparison_report.json`
- `--max-heal-attempts 2`
- `--base-ref HEAD~1`
- `--model gemini-2.5-flash`

## CI

GitHub Actions workflow outputs:

- `tests/test_generated.py`
- `reports/report.json`
- `reports/comparison_report.json`
- `reports/comparison_report.csv`
- `reports/comparison_report.md`
