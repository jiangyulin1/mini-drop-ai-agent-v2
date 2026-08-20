# Collector Agent v1

This suite evaluates acquisition decisions and evidence-grounded analysis. It is
not a root-cause keyword benchmark.

- `public/` contains model-visible case prompts and branching replay data.
- `private/` is evaluator-only and must never enter prompts, environment
  variables, command lines, fixture paths, logs, or Evidence.
- `schemas/` defines the submitted run trace and private Oracle contracts.
- `manifest.json` locks the catalog, scenarios, policy, prompt, and budgets.

Validate the suite without reading a candidate trace:

```bash
uv run python scripts/run_collector_agent_eval.py --validate-only
```

Score a development trace (the bundled seed suite is intentionally smaller
than the formal 30-scenario gate):

```bash
uv run python scripts/run_collector_agent_eval.py \
  --traces /path/to/run-traces.json --development \
  --output reports/collector-agent/development.json
```

Formal scoring refuses suites with fewer than 30 independent scenarios and
fails the run if any safety or Oracle-leakage hard gate is non-zero.

Run the model-driven replay pilot without exposing the private Oracle:

```bash
DEEPSEEK_API_KEY=... uv run python scripts/run_replay_agent.py \
  --profile mini_drop --arm M1 \
  --output reports/collector-agent/mini-drop-run.json
```

The API key is read only from the named environment variable. The trace stores
provider request IDs and token counts, never credentials or model reasoning.
