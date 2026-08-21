# Evaluation reports

This directory only keeps manually reviewed, durable summaries named
`verified-*.md`. Raw responses, runtime events, traffic logs, databases,
preflight output and blocked-run placeholders are local artifacts and are
ignored by Git.

A verified report must state:

- whether a real model provider was called;
- whether the run completed or stopped at preflight;
- how scoring was performed and what the score does not establish;
- the number of rounds and cases;
- known evidence, platform and traffic-accounting limitations;
- hashes for the local evidence files used to prepare the summary.

Preflight success, health checks and projection-only preparation are not model
evaluation results. Never copy provider credentials, internal tokens, raw
production payloads or local databases into this directory.
