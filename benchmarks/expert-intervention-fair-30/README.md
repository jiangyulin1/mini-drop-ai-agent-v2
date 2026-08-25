# Expert Intervention Fair-30

This package is a deterministic, compact replay suite for two evaluation
tracks:

- `fair_same_data`: every agent receives the same anonymous incident, evidence
  projections, hashes, tool contract, and one investigation turn.
- `expert_intervention_tuning`: the same first turn is followed by one
  state-based expert event. The agent must re-read active evidence, update or
  defend its hypothesis, and submit a second answer. Operator hints are
  unverified and evidence-review exclusions are binding.

The suite contains 30 cases (`case-01` through `case-30`) covering local CPU
hotspots and regressions, memory leak versus retained capacity, runtime locks,
network latency/loss, downstream saturation, same-host CPU/memory pressure,
disk I/O, descriptor exhaustion, periodic scheduler/GC jitter, evidence
quality failures, scope correction, hypothesis challenge, plan reprioritizing,
and compound incidents with primary causes and amplifiers.

## Layout

`cases/public/` contains the public incident prompt and evidence index.
`cases/replay/` contains only bounded evidence projections; raw source packets
are intentionally absent. Every projection has a deterministic SHA-256 hash.
`private/oracles.json` contains evaluator-only expected location, domain,
classification, mechanism keywords, evidence requirements, abstention policy,
and expert-track expectations. It must not be sent to an agent.
`interventions/` contains one state-based intervention pack per case.

## Rebuild and validate

```bash
python3 benchmarks/expert-intervention-fair-30/build_suite.py
python3 - <<'PY'
import json
from pathlib import Path
r = Path('benchmarks/expert-intervention-fair-30')
m = json.loads((r/'manifest.json').read_text())
assert m['rounds'] == 30 and len(m['cases']) == 30
assert len(json.loads((r/'private/oracles.json').read_text())['cases']) == 30
print('expert-intervention-fair-30: valid')
PY
```

The manifest sets `repetitions: 1` and `rounds: 30`: this is one complete
single-round pass over thirty cases, rather than thirty repetitions of one
case. The package is API/replay-only and uploads compact projections (never
raw source data), keeping it suitable for the constrained server upload link.
