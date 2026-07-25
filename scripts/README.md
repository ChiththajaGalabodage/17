# Experiment scripts

Use these entry points for new research evidence:

- `run_research_experiment.py`: generated versus manual suites on the same clean subjects and traceable mutants.
- `evaluate_test_selection.py`: deterministic change-impact versus Gemini-hybrid selection against versioned relevant-test oracles.
- `validate_generated_tests.py`: static validation utility.
- `plot_benchmark_execution_time.py`: charting utility for compatible CSV inputs.

Example:

```powershell
python scripts/run_research_experiment.py --offline --runs 1 --mutation-limit 5
python scripts/evaluate_test_selection.py --offline --runs 1
```

`compare_methods.py`, `run_prototype_experiments.py`, and the old top-level comparison reports are retained only for historical compatibility. Their metrics treat pytest failures as defects and do not provide unique fault ground truth; do not cite them as thesis evidence.

See the repository [README](../README.md) for manifest schemas, live Gemini commands, evidence gates, and the thesis-scale protocol.
