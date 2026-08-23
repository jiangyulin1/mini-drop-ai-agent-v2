# Open-Source Components

This document records the open-source boundary for the evidence-driven
investigator.  The rule is simple: use a project for orchestration or graph
mechanics when it is mature, but keep Mini-Drop's Evidence, revision fences,
collection reuse, and authorization as the authority.

## Selected components

| Component | Repository | License | Use in Mini-Drop | Boundary |
|---|---|---|---|---|
| LangGraph | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | MIT | Optional stateful investigation graph: branch execution, checkpoint thread IDs, replay and human interrupts | It receives a bounded branch context and can only propose obligations/next actions. It never decides Evidence truth or dispatches a collector |
| Temporal Python SDK | [temporalio/sdk-python](https://github.com/temporalio/sdk-python) | MIT | Later option for cross-process durable workflows and activity retries | Do not introduce it into the current control plane yet; it overlaps with Task, Outbox, Wakeup, generation and revision fencing |
| eventsourcing | [pyeventsourcing/eventsourcing](https://github.com/pyeventsourcing/eventsourcing) | BSD-3-Clause | Reference for aggregate/event-replay patterns | Existing SQL revisions, Evidence lifecycle and audit events remain the source of truth |
| NetworkX | [networkx/networkx](https://github.com/networkx/networkx) | BSD-3-Clause | Optional offline analysis of tree ancestry, descendants and cut sets | Never use it as persistent state, permission enforcement or Evidence authority |
| Prefect | [PrefectHQ/prefect](https://github.com/PrefectHQ/prefect) | Apache-2.0 | Useful for general data pipelines | Not selected for investigation semantics: it does not provide our Evidence dependency and revocation contract |
| Dapr Workflow | [dapr/python-sdk](https://github.com/dapr/python-sdk) | Apache-2.0 | Future option only when Mini-Drop runs on Dapr/Actors | Requires a Dapr runtime and would add an infrastructure dependency without solving Evidence governance |

## What is implemented now

`server/app/agent_runtime/langgraph_adapter.py` is an optional adapter.  It
compiles a small graph with `reconcile -> select_obligation -> review_gate`,
uses a stable `case/run/branch` checkpoint thread ID, and exposes an explicit
human interrupt.  It filters state to the current branch's selected Evidence
IDs.  The default installation does not import LangGraph; install the
`orchestration` extra on Python 3.10+ to enable it.

The durable investigation tree is stored by Mini-Drop as an audit/index
projection (`investigation_tree_nodes`, `investigation_tree_dependencies`, and
`investigation_tree_events`).  Its write paths are explicit and tenant-scoped:

- Agent proposes a node or dependency through the internal Tool Gateway.
- An operator can inspect or transition a node through the Case API.
- Evidence invalidation recursively abandons descendants and records every
  transition; original Artifact/Evidence rows are retained for replay.

The tree does not make all Case Evidence visible to a branch.  A node must
carry explicit Evidence references, and reuse still goes through
`CollectionSupervisor` and `EvidenceReuseDecisionModel`.

## Installation

The normal package remains Python 3.9-compatible.  For a Python 3.10+
environment that wants the optional components:

```bash
uv sync --extra orchestration
```

The optional graph runtime is not required for deterministic operation or for
the existing Pi sidecar protocol.

## Why not replace the control plane

LangGraph and Temporal provide execution semantics, not the domain guarantees
that make a security investigation trustworthy.  In particular, neither one
knows that an excluded review revision must invalidate a claim, fence a late
collection result, or prevent an unselected Projection from entering a sibling
branch.  Those checks stay in Mini-Drop and are recorded in SQL before a graph
is resumed.
