# Blind Judge Method for Expert-Intervention Fair-30

The existing `score_suite.py` is a machine gate for integrity and execution
contracts. It must not be reported as a semantic capability score. A judge-AI
review should use the following protocol.

## Separation of concerns

- The coordinator only anonymizes, assigns packets, validates JSON, hashes
  ballots, and computes medians/weighted arithmetic after ballots are frozen.
- A case-audit judge reads public case, replay evidence, oracle and intervention
  only, and records which oracle claims are actually derivable from public
  evidence. Underdetermined claims become `null`, not a failure.
- Three independent fresh-context judges score each candidate/case for A and
  B1. They must not see other candidates or prior scores.
- Product judges score B2 and D from event timelines; protocol types are
  reported separately rather than forced into one ranking.
- C is qualitative unless tool and byte instrumentation is comparable.
- A fresh arbitrator rereads disagreements; it does not select a majority vote.

## 0-4 anchors

- 4: stable completion, semantically correct conclusion, evidence and limits
  handled, no material workflow defect.
- 3: core objective achieved with one meaningful gap or recoverable issue.
- 2: partial completion with both correct and incorrect or missing reasoning.
- 1: only a small local success; major errors dominate.
- 0: clear wrong behavior or no completion.
- `null`: evidence/protocol is insufficient or not comparable.

## A: diagnosis reasoning

- A1 causal mechanism and root-cause boundary: 35%
- A2 evidence validity, counter-evidence and lifecycle: 25%
- A3 uncertainty, abstention and confidence calibration: 20%
- A4 next action distinguishing competing hypotheses: 10%
- A5 repeat stability: 10%

Do not penalize equivalent terminology or an enum mismatch as reasoning error;
record contract failures separately. Do not require private-oracle facts that
the public projection cannot support.

## B1: post-intervention judgment

- B1.1 recognizes and responds to intervention: 20%
- B1.2 handles excluded/low-trust evidence: 25%
- B1.3 avoids blind obedience to unverified expert hints: 20%
- B1.4 revises, narrows or defends the conclusion appropriately: 25%
- B1.5 states the new evidence gap: 10%

Read negation as whole-sentence meaning. For example, “not verified” and
“cannot confirm” are not affirmative claims.

## Required ballot evidence

Every extreme score (0, 1, or 4) needs a direct path/event reference. Each
ballot records execution failures, adapter failures, contract failures and
semantic reasoning failures separately. Scores are discrete; no keyword-hit
percentage is allowed.

The resulting report must publish medians, valid sample counts, nulls,
disagreements and limitations. It must not publish a universal total score or
claim industry-leading performance from this 30-case sample.
