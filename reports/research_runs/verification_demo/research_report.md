# Research Experiment Report

Experiment ID: `verification_demo`

## Evidence status

This run does **not** yet meet the configured thesis-evidence checks.

- Only 0 study subject(s); at least 3 are required by this protocol.
- Only 0 unique killable study mutants; at least 30 are required by this protocol.
- The manifest contains only demo subjects, not thesis study subjects.

## Subject results

| Subject | Role | Reference mutation score | Valid generated runs | Generated mutation score mean | Reference-fault recall mean | Backend(s) |
|---|---|---:|---:|---:|---:|---|
| calculator-harness-smoke-test | demo | 100.00 | 1 | 100.00 | 100.00 | deterministic-fallback |

## Metric definitions

- Mutation score = killed valid mutants / (killed + survived valid mutants). Invalid and timed-out mutants are excluded and listed in raw artifacts.
- Reference-fault recall = unique mutants killed by the generated suite / unique mutants killed by the manual reference suite.
- A generated suite is valid only when static validation passes, the clean source passes, and repeated outcomes are consistent.
- End-to-end pipeline time and isolated pytest execution time are reported separately.

## Statistical analysis

Descriptive statistics include sample size, mean, median, standard deviation, range, and a deterministic bootstrap 95% confidence interval. Any paired comparison uses one aggregated observation per study subject; repeated runs are not misrepresented as independent projects.

## Provenance and limitations

- Git commit: `9d5904beaabaeb097726898070bfe3035fc946ee`
- Git worktree dirty: `True`
- Python: `3.12.3`
- Platform: `Windows-11-10.0.26200-SP0`
- Mutation operators can produce equivalent mutants; thesis-scale runs require manual review or an explicit equivalent-mutant protocol.
- The bundled calculator subject is a harness smoke test only. It must not be presented as real-world validation.
- This report intentionally provides no arbitrary weighted winner score.

Raw artifacts: `reports/research_runs/verification_demo/raw`
