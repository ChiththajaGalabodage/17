# LLM Continuous Testing Framework

This repository implements an evidence-preserving prototype for the research topic **Enhancing Continuous Testing in DevOps and CI/CD Pipelines Using Large Language Models**.

The framework contains:

- a test-generation agent (Gemini, with an explicitly labelled deterministic fallback),
- static quality and safety validation,
- change-impact and optional Gemini-hybrid test selection,
- a guardrail-tested experimental healer for a verified missing-`pytest` import,
- repeated-run flakiness checks,
- isolated mutation testing for unique fault ground truth, and
- versioned experiment runners that preserve raw artifacts and provenance.

## Important research rule

A passing pipeline is not automatically evidence that the method works. Generated tests are accepted only when they:

1. pass static quality and safety gates,
2. pass against the clean reference implementation,
3. produce consistent repeated outcomes, and
4. detect traceable mutants without modifying the original project.

The bundled calculator benchmark is a **harness smoke test only**. The experiment report deliberately marks it as insufficient thesis evidence.

Existing top-level files under `reports/` were produced by older scripts and use invalid proxy metrics such as failed tests = defects. Do not cite those legacy reports. Valid new evidence is written under a unique component-specific experiment directory.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Use `requirements-lock.txt` for exact thesis experiment reproduction.

Set `GEMINI_API_KEY` for live LLM experiments. Use `--offline` only for deterministic harness verification; offline results are automatically marked ineligible for an LLM-effect claim.

## Run the continuous-testing pipeline

Clean benchmark:

```powershell
python main.py `
  --source benchmarks/calculator_subject.py `
  --test-output reports/manual_run/generated_tests.py `
  --report-output reports/manual_run/pipeline.json `
  --minimum-target-coverage 80 `
  --stability-runs 3 `
  --offline
```

Live Gemini run:

```powershell
python main.py `
  --source path/to/subject.py `
  --test-output reports/manual_run/generated_tests.py `
  --report-output reports/manual_run/pipeline.json `
  --minimum-target-coverage 80 `
  --stability-runs 5
```

Exit codes have semantic meaning:

- `0`: validation passed, tests passed, and repeated outcomes were consistent.
- `1`: executed tests found a failure or inconsistent outcome.
- `2`: the generated suite failed static validation and was not executed.

Product failures are retained as defect evidence. Runtime healing is limited to a verified missing-`pytest` import. A candidate is rejected if any non-import test statement changes. These are implementation guardrails, not empirical proof of healing effectiveness or semantic preservation.

## Evaluate test generation and defect detection

The versioned subject manifest is [experiments/subjects.example.json](experiments/subjects.example.json). Run the demo protocol with:

```powershell
python scripts/run_research_experiment.py `
  --manifest experiments/subjects.example.json `
  --offline `
  --runs 3 `
  --stability-runs 3 `
  --mutation-limit 20
```

For a live study, remove `--offline`, add real open-source subjects to a separate versioned manifest, and use immutable clean revisions whose manual reference suites pass.

The runner reports:

- generated-suite validity rate,
- assertion and target-callable coverage,
- mutation score,
- recall of unique reference-killed mutants,
- invalid, timed-out, killed, and survived mutant IDs,
- repeated-run consistency,
- isolated pytest execution time,
- end-to-end generation pipeline time,
- model/fallback backend and prompt/source hashes,
- Git, Python, platform, and dependency provenance, and
- descriptive statistics with bootstrap 95% confidence intervals.

No arbitrary weighted “overall winner” is calculated.

## Evaluate test selection

Selection must be evaluated against relevant-test oracles, not by calling the selected percentage “accuracy.” The example schema is [experiments/selection_scenarios.example.json](experiments/selection_scenarios.example.json).

```powershell
python scripts/evaluate_test_selection.py `
  --manifest experiments/selection_scenarios.example.json `
  --offline `
  --runs 3
```

The selection report compares the deterministic change-impact baseline with the safety-constrained Gemini hybrid using:

- precision,
- recall,
- F1,
- selected-test fraction, and
- test reduction.

The LLM may add tests but cannot drop deterministic impact matches. Without a trained historical model, this component is accurately described as **LLM-assisted hybrid test selection**, not a learned predictive model.

## Thesis-scale evidence checklist

Each report exposes outcome-neutral `evidence_readiness` separately from `claim_support`. Readiness means the configured protocol and provenance checks are complete; it does not mean the method won. The legacy `claim_readiness` field remains only as a compatibility alias for component evidence readiness.

At minimum, prepare:

- 3–5 real projects pinned to exact commits,
- at least 30 independent, killable and reviewed faults/mutants,
- at least 30 versioned change-selection scenarios with full-suite relevant-test oracles,
- at least 30 labeled healing scenarios containing repairable artifacts and protected product/mixed failures,
- multiple Gemini generations per subject/scenario,
- an explicit equivalent-mutant review protocol,
- the same source revisions and fault set for every strategy,
- manual full-suite, deterministic change-impact, and proposed LLM-assisted arms,
- component ablations for generator, validator, selector, and safe healer, and
- paired analysis at project/change/fault level rather than treating repeated timing runs as independent samples.

The `role` field in manifests must be `study` for real evidence. Keep examples and development fixtures as `demo`. Generation/fault detection, selection, and healing have separate readiness results; an overall framework claim requires all applicable components plus preregistered outcome thresholds.

## Tests and CI

Framework regressions and intentional research defects are separated:

```powershell
python -m pytest -q tests benchmarks/tests `
  --ignore=tests/test_research_suite.py `
  --ignore=tests/test_generated.py `
  --ignore=tests/generated_tests.py `
  --ignore=tests/experiments
```

Intentional faults are evaluated through the mutation/research harness and are never counted as ordinary framework test failures. The GitHub Actions workflow uses a full Git checkout so change-impact selection can resolve parent revisions.
