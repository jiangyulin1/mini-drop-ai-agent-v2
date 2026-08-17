# Agent strategy matrix: diagnostic-strategy-smoke-v1

| Condition | Strategy | Pass rate | Root accuracy | Evidence validity | Tools | Side effects | Consistency | Cost units |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| rule-tree-low | rule_tree | 1.000 | 1.000 | 1.000 | 5 | 0 | 1.000 | 7.500 |
| evidence-first-medium | evidence_first | 1.000 | 1.000 | 1.000 | 5 | 0 | 1.000 | 10.000 |
| hybrid-high | hybrid | 1.000 | 1.000 | 1.000 | 5 | 0 | 1.000 | 15.000 |

> Offline harness measures deterministic projection quality; live Pi latency/token cost requires a VM profile.
