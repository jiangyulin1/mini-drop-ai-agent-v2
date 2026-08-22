# Adapter contract

Each adapter is an isolated entrypoint. It receives one anonymous public case,
the common five read-only tools and the shared system prompt. It must emit the
run manifest files described in `prompts/02-adapt-and-run.md`.

## Execution status

All five adapters were executed through the benchmark-owned unified replay
adapter with DeepSeek `deepseek-v4-flash`. The per-agent `run_case.py` files are
thin wrappers that fix the agent id; they do not execute the upstream agent's
private runtime. This is documented as a comparability limitation in
`comparisons/FINAL_ACCEPTANCE.md`.
