# Evidence-native 9x3

This is the current stability matrix for the Evidence-native investigation path.

It uses nine real GitHub pull requests as pinned, public Evidence sources. The
runner imports compact projections into a fresh Case, then sends three
independent Pi/DeepSeek read-only turns per Case. The private oracle is kept in
the local report and is never included in a model message.

## Matrix

- 9 PR cases, 3 rounds per case, 27 model turns.
- Each case has `pr_core`, `external_evidence`, and `simulated_runtime`
  projections. The runtime pack is explicitly synthetic and is only a wiring
  probe; it must not be reported as live production telemetry.
- Every round must have a completed Assistant answer, a provider attempt, an
  auditable runtime event trail, and citations bound to the Case's canonical
  Evidence IDs and projection hashes.
- Tool policy is `READ_ONLY`/`ANSWER_ONLY`; no collector, write tool, or
  production action is allowed in this matrix.

## Cases

The nine pinned cases are defined in
`scripts/run_github_pr_attribution_eval.py`: Grafana, Prometheus, Redis,
Kubernetes, OpenTelemetry Python, and Envoy PRs. `prometheus-19393` is a WIP
text-lexer performance PR and is intentionally a qualified-claim case.

## Execution

```bash
python scripts/run_github_pr_attribution_eval.py \
  --rounds 3 --low-bandwidth \
  --output-dir reports/eval/github-pr-attribution-9x3

python scripts/run_github_pr_live_eval.py \
  --input-dir reports/eval/github-pr-attribution-9x3 \
  --rounds 3 \
  --output-dir reports/eval/github-pr-attribution-9x3/live
```

The live runner is resumable. A completed `case_id:round-N` is reused only if
its canonical Evidence IDs and projection hashes still match. A blocked or
timed-out round is never converted into a passing result.

## Result interpretation

The matrix produces two classes of results:

1. Structural/linkage gates: provider completion, runtime audit, read-only
   policy, canonical Evidence binding, valid projection hashes, and 27/27
   completed rounds.
2. Investigation quality: mechanism attribution, counter-evidence,
   uncertainty, and impact boundary. These remain human/oracle-scored and are
   not inferred from keyword matching.

Passing the first class proves the Evidence-native chain is wired for repeated
turns. It does not prove general RCA accuracy or production autonomy.
