# Expert-Intervention Fair-30 Blind Judge Package

This package migrates the supplied anonymous multi-agent review method to the
30-case suite. The previous `score_suite.py` output remains a machine gate for
execution/integrity checks only; it is not a semantic capability score.

## Current status

`PARTIAL_PENDING_CASE_AUDIT_AND_BALLOTS`

The package contains one anonymized candidate (`CAND-52EE75BF`) with 30
`fair_same_data` and 30 `expert_intervention_tuning` event views. Other agents
do not currently have native runs for these 30 cases, so no placeholder
candidate or score was created.

## Layout

- `input/cases/`: public incident, replay projection and intervention material;
  no private oracle.
- `input/candidates/CAND-*/`: anonymous event and injection views recovered
  from the JYL Case event API.
- `jury-packets/A/`: diagnosis reasoning packets, three fresh judges per case.
- `jury-packets/B1/`: post-intervention judgment packets, three fresh judges per
  case.
- `jury-packets/C/`: collection planning/resource packets, three fresh judges
  per case.
- `jury-packets/product/`: B2/D product workflow packets; protocol types remain
  separate and are not forced into one ranking.
- `case-audit/STATUS.json`: case evidence ceiling audit is still pending.
- `private/`: oracle, identity registry and run index; never expose to judges.

## Migrated scoring

Judges use independent fresh contexts and a discrete 0-4 scale. `null` means
the public evidence or protocol is insufficient/comparable only in a limited
way; it is not a zero. A1-A5 cover diagnosis reasoning, B1.1-B1.5 cover
post-intervention judgment, and C covers collection planning. Extreme scores
require direct event/path evidence. Equivalent terminology must be judged by
meaning, not keyword equality. A separate arbitrator reviews material
disagreements; majority vote is not a substitute for rereading evidence.

The package is not complete until case evidence ceilings, independent ballots,
disagreement arbitration and ballot hashes are frozen.
