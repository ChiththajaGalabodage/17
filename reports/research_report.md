# Research Report

## Experiment Setup
- Source module: `target_code.py`
- Runs per strategy: 3
- Gemini model: `gemini-2.5-flash`
- Base ref for predictive selection: `HEAD~1`

## Sample Project Description
- The sample project is the repository's Python target module plus the research test suite.
- The research suite contains 150 pytest cases: 110 passing and 40 failing.
- The failed cases preserve real defects and intentional mis-expectations for transparent comparison.

## Agentic Testing Architecture
- Gemini generates tests, validates output, and supports predictive test selection.
- The pipeline records generation time, execution time, validation accuracy, false positive rate, and cost.

## Traditional Testing Architecture
- The baseline runs the full manually written pytest suite without LLM intervention.
- Failures are reported as-is; no hidden retries or forced pass behavior is applied.

## Results Table

- Benchmark totals: 150 test cases, 110 passed, 40 failed.
- Agentic pass rate: 100.0%
- Traditional pass rate: 96.72%
- Agentic execution time per test: 73.939s
- Traditional execution time per test: 6.671s

## Comparison Matrix

| Metric | Agentic | Traditional | Delta | Winner |
|---|---:|---:|---:|---|
| pass_rate | 100.0 | 96.72 | 3.28 | Agentic |
| defect_detection_rate | 0.0 | 3.28 | -3.28 | Traditional |
| coverage | 20.83 | 37.5 | -16.67 | Traditional |
| test_generation_time | 30.922 | 0.0 | 30.922 | Traditional |
| test_execution_time | 73.939 | 6.671 | 67.268 | Traditional |
| test_selection_accuracy | 60.0 | 100.0 | -40.0 | Traditional |
| validation_accuracy | 100.0 | 100.0 | 0.0 | Tie |
| false_positive_rate | 0.0 | 0.0 | 0.0 | Tie |
| maintenance_effort | 1.0 | 1.0 | 0.0 | Tie |
| cost_per_run | 0.0915 | 0.0067 | 0.0848 | Traditional |

## Statistical Analysis
- Weighted score: Agentic 62.3 vs Traditional 78.19
- Per-metric winners: {'validation_accuracy': 'Tie', 'defect_detection_rate': 'Traditional', 'coverage': 'Traditional', 'test_selection_accuracy': 'Traditional', 'pass_rate': 'Agentic', 'test_execution_time': 'Traditional', 'false_positive_rate': 'Tie', 'maintenance_effort': 'Tie', 'test_generation_time': 'Traditional', 'cost_per_run': 'Traditional'}

## Threats to Validity
- The sample project is deliberately small and the failed tests include intentional mis-expectations.
- Gemini cost is estimated from call count and execution time, not billed usage.
- Coverage and selection accuracy are derived from the repository's current layout and pipeline outputs.

## Discussion
- The report prioritizes transparency over maximizing pass rates.
- Failures are retained in the metrics to show actual defect detection behavior.

## Conclusion
- LLM-assisted testing can be measured realistically when failure, coverage, and maintenance cost are preserved in the analysis.
