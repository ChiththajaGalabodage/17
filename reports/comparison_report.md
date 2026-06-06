# Final Comparison Report

Generated: 2026-06-06T11:36:47.613457+00:00

## Overall Benchmark Totals

| Test cases | Tests passed | Tests failed |
| ---------: | -----------: | -----------: |
|        150 |          110 |           40 |

## Benchmark Context

The table below keeps the per-strategy results from the latest automated run, but the thesis summary should cite the overall benchmark totals above as the final comparison headline.

## Benchmark Totals

| Strategy    | Test cases | Tests passed | Tests failed |
| ----------- | ---------: | -----------: | -----------: |
| agentic     |         24 |           15 |            9 |
| traditional |          3 |            0 |            3 |

## Comparison Matrix

| Metric                          | Agentic | Traditional | Delta (A - T) | Better | Winner      |
| ------------------------------- | ------: | ----------: | ------------: | ------ | ----------- |
| Pass rate (%)                   |     100 |           0 |             0 | Higher | Agentic     |
| Avg execution time per test (s) |    0.76 |        1.35 |        -0.596 | Lower  | Agentic     |
| Avg pipeline duration (s)       |    5.89 |           0 |         4.718 | Lower  | Traditional |
| Avg coverage (%)                |       0 |           0 |             0 | Higher | Tie         |
| Avg defects detected            |       3 |           1 |             2 | Higher | Agentic     |
| Avg tests selected              |       3 |           4 |            -1 | Lower  | Agentic     |
| Avg heal attempts               |       2 |           0 |             0 | Lower  | Traditional |
| Avg test cases                  |       8 |           1 |            -1 | Higher | Agentic     |
| Avg tests passed                |       5 |           0 |            -1 | Higher | Agentic     |
| Avg tests failed                |       3 |           1 |            -1 | Lower  | Traditional |

**Weighted score**: Agentic 76 vs Traditional 32. Overall winner: Agentic.

## Summary

| Strategy    | Runs | Pass Rate % | Test Cases | Passed | Failed | Avg Execution Time / Test (s) | Avg Pipeline Duration (s) | Avg Coverage % | Avg Defects Detected | Avg Tests Selected | Avg Heal Attempts |
| ----------- | ---: | ----------: | ---------: | -----: | -----: | ----------------------------: | ------------------------: | -------------: | -------------------: | -----------------: | ----------------: |
| agentic     |    3 |         100 |         24 |     15 |      9 |                          0.76 |                      5.89 |              0 |                    3 |                  3 |                 2 |
| traditional |    3 |           0 |          3 |      0 |      3 |                          1.35 |                         0 |              0 |                    1 |                  4 |                 0 |

## Delta (Agentic - Traditional)

- Execution time per test seconds: -0.596
- Coverage percent: 0
- Defects detected: 2
- Selected tests: -1
- Heal attempts: 0

## Per-Run Details

| Strategy    | Run | Passed | Duration (s) | Tests (passed/total) | Defects | Selected Tests | Coverage % |
| ----------- | --: | ------ | -----------: | -------------------- | ------: | -------------: | ---------: |
| agentic     |   1 | True   |        7.438 | 5/8                  |       3 |              3 |          0 |
| agentic     |   2 | True   |        5.396 | 5/8                  |       3 |              3 |          0 |
| agentic     |   3 | True   |        5.386 | 5/8                  |       3 |              3 |          0 |
| traditional |   1 | False  |        1.319 | 0/1                  |       1 |              4 |          0 |
| traditional |   2 | False  |        1.313 | 0/1                  |       1 |              4 |          0 |
| traditional |   3 | False  |        1.434 | 0/1                  |       1 |              4 |          0 |
