# smolagents adapter

Pinned source is `e3a5b8994b301983b91c0325546e9dc82eab8cf0`. Use
`ToolCallingAgent`, not `CodeAgent`; expose only the five common tools and
disable Python execution, web search, shell and filesystem access.

## Execution status

Executed with DeepSeek `deepseek-v4-flash` through the benchmark-owned unified replay adapter. Upstream runtime not executed in this environment.
