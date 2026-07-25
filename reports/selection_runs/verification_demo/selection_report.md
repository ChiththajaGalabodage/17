# Test Selection Experiment

This run does **not** yet meet the configured selection-evidence checks.

- Only 0 study scenario(s); at least 30 are required by this protocol.
- The manifest contains only demo scenarios.

| Scenario | Role | Baseline recall | Baseline reduction | Proposed recall mean | Proposed reduction mean | Backend(s) |
|---|---|---:|---:|---:|---:|---|
| selector-harness-smoke-test | demo | 1.0000 | 0.9091 | 1.0000 | 0.9091 | deterministic |

Selection accuracy is not the selected-test percentage. Precision, recall, F1, and reduction are computed against versioned relevant-test oracles.

The proposed selector is a safety-constrained LLM hybrid: deterministic impact matches cannot be dropped. It is not described as a learned predictive model unless historical training is added.
