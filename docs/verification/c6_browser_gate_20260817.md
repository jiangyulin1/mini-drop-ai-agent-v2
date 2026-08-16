# C6 Browser Gate — 2026-08-17

Status: `PASS`

## Environment

- Web: current workspace source, Vite on `127.0.0.1:5173`
- API: current workspace source, Uvicorn on `127.0.0.1:8291`
- Database: isolated persistent SQLite database used across an actual API process restart
- Browser: Playwright 1.62.1, Chromium 151.0.7922.34
- Runtime: deterministic (the browser gate does not depend on a model provider)

## Mandatory browser scenarios

| Scenario | Result | Evidence |
| --- | --- | --- |
| Question-driven entry | PASS | Browser created `case_20260816_160354_bf115abaae` through the New Diagnosis form and rendered the canonical Workspace. |
| Data-driven entry | PASS | Normal dashboard displayed `task_20260816_160059_c5165b`; `fromTask` created/opened the same canonical Case chain. |
| Refresh | PASS | The persisted AssistantMessage was rendered exactly once after reload. |
| Disconnect/reconnect | PASS | Chromium was placed offline and online; after recovery/reload the same message was still rendered exactly once. |
| Server restart | PASS | Uvicorn was terminated and started again against the same database; a new browser context opened the Case deep link and observed the message exactly once before and after another reload. |
| No placeholder/blank Workbench | PASS | `Canonical Workspace Snapshot`, Evidence, Campaign/Execution, Causal Graph, Gap/Conclusion and control revisions were visible; no `Accepted` placeholder was present. |

Commands and results:

```text
npm run test:e2e:c6          -> 2 passed
npm run test:e2e:c6:restart  -> 1 passed
npm test                     -> 20 files, 58 tests passed
npm run lint                 -> passed with zero warnings
npm run audit:prod           -> production dependency audit gate passed
npm run build                -> passed
```

## Canonical network contract

The browser request trace contained the canonical endpoints below (along with list/supporting APIs):

```text
GET  /api/v1/cases/{case_id}/workspace
GET  /api/v1/cases/{case_id}/events?limit=300
GET  /api/v1/cases/{case_id}/events/stream?after_seq={snapshot.last_event_seq}
POST /api/v1/cases/{case_id}/agent/turn
GET  /api/tasks/{task_id}
```

The SSE server subscribes before reading the replay window and fences both replayed and live frames by monotonic `case_event_seq`. `test_sse_snapshot_subscribe_window_has_no_gap_or_duplicate` additionally injects a duplicate bus delivery and proves the next delivered frame is the next durable sequence.

## Bundle record

Vite reports the current route-split AI workspace chunk as `96.85 kB` minified / `35.03 kB` gzip and its CSS as `19.84 kB` / `4.62 kB` gzip. The immediately preceding build was `96.70 kB` / `34.98 kB`; the `+0.15 kB` minified (`+0.05 kB` gzip) delta is the canonical Workspace renderer and Case deep-link support. The dashboard entry chunk decreased from `65.68 kB` to `65.67 kB`. Large Ant Design and ECharts graphs remain isolated vendor chunks.
