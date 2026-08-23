"""Optional LangGraph bridge for evidence-scoped investigation branches.
LangGraph is intentionally an optional orchestration dependency.  Mini-Drop
keeps ownership of Case/Evidence/Projection/revision state and hands LangGraph
only a bounded branch context.  This module therefore contains no collector or
Evidence writes: graph output is a proposal that must return through the
existing Tool Gateway and CollectionSupervisor.

Install the optional ``orchestration`` extra on Python 3.10+ to enable it.  The
default deterministic and Pi runtimes do not import LangGraph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypedDict


class LangGraphUnavailable(RuntimeError):
    """Raised when the optional LangGraph extra is not installed."""


class InvestigationGraphState(TypedDict, total=False):
    case_id: str
    run_id: str
    branch_id: str
    node_id: str
    visible_evidence_ids: list[str]
    obligations: list[dict[str, Any]]
    frontier: list[dict[str, Any]]
    selected_obligation: dict[str, Any] | None
    status: str
    review_request: dict[str, Any] | None
    graph_revision: int


@dataclass(frozen=True)
class LangGraphRuntimeContext:
    """The only state that may cross the Mini-Drop/LangGraph boundary."""

    case_id: str
    run_id: str
    branch_id: str
    node_id: str
    visible_evidence_ids: tuple[str, ...] = ()
    graph_revision: int = 1

    def config(self) -> dict[str, dict[str, str]]:
        # A stable thread ID gives LangGraph checkpoint/replay semantics while
        # keeping Case and branch identity explicit in every graph invocation.
        return {
            "configurable": {
                "thread_id": f"case:{self.case_id}:run:{self.run_id}:branch:{self.branch_id}",
            },
        }

    def initial_state(
        self,
        *,
        obligations: list[dict[str, Any]] | None = None,
        frontier: list[dict[str, Any]] | None = None,
    ) -> InvestigationGraphState:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "node_id": self.node_id,
            "visible_evidence_ids": sorted({str(item) for item in self.visible_evidence_ids if str(item)}),
            "obligations": list(obligations or []),
            "frontier": list(frontier or []),
            "selected_obligation": None,
            "status": "READY",
            "review_request": None,
            "graph_revision": int(self.graph_revision or 1),
        }


def langgraph_available() -> bool:
    """Return whether the optional dependency can be imported."""

    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True


def _require_langgraph() -> None:
    if not langgraph_available():
        raise LangGraphUnavailable(
            "LangGraph is optional; install mini-drop[orchestration] on Python 3.10+"
        )


class LangGraphInvestigationAdapter:
    """Compile the bounded investigation graph when LangGraph is installed.

    ``obligation_selector`` is deliberately injected by the caller.  It can
    rank obligations using Mini-Drop's existing AdaptivePlanner without making
    the open-source graph library a source of diagnostic truth.
    """

    runtime_type = "langgraph"
    runtime_version = "langgraph-adapter.v1"

    def __init__(self, obligation_selector: Callable[[list[dict[str, Any]]], dict[str, Any] | None] | None = None):
        self._obligation_selector = obligation_selector or (lambda items: items[0] if items else None)

    def compile(self, *, checkpointer: Any | None = None) -> Any:
        _require_langgraph()
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt

        selector = self._obligation_selector

        def reconcile(state: InvestigationGraphState) -> InvestigationGraphState:
            visible = sorted({str(item) for item in state.get("visible_evidence_ids", []) if str(item)})
            obligations = [item for item in state.get("obligations", []) if isinstance(item, dict)]
            return {
                **state,
                "visible_evidence_ids": visible,
                "obligations": obligations,
                "status": "RECONCILED",
            }

        def select_obligation(state: InvestigationGraphState) -> InvestigationGraphState:
            selected = selector(list(state.get("obligations", [])))
            return {
                **state,
                "selected_obligation": selected,
                "status": "OBLIGATION_SELECTED" if selected else "WAITING_EVIDENCE",
            }

        def review_gate(state: InvestigationGraphState) -> InvestigationGraphState:
            selected = state.get("selected_obligation")
            if not selected:
                return state
            if not bool(selected.get("requires_human_review")):
                return state
            response = interrupt({
                "kind": "INVESTIGATION_REVIEW",
                "case_id": state.get("case_id"),
                "run_id": state.get("run_id"),
                "branch_id": state.get("branch_id"),
                "node_id": state.get("node_id"),
                "obligation": selected,
            })
            return {**state, "review_request": response, "status": "REVIEWED"}

        graph = StateGraph(InvestigationGraphState)
        graph.add_node("reconcile", reconcile)
        graph.add_node("select_obligation", select_obligation)
        graph.add_node("review_gate", review_gate)
        graph.add_edge(START, "reconcile")
        graph.add_edge("reconcile", "select_obligation")
        graph.add_edge("select_obligation", "review_gate")
        graph.add_edge("review_gate", END)
        return graph.compile(checkpointer=checkpointer or MemorySaver())

    @staticmethod
    def checkpoint_payload(state: InvestigationGraphState) -> dict[str, Any]:
        """Return a JSON-safe checkpoint payload for CaseContextSnapshot."""

        allowed = {
            "case_id", "run_id", "branch_id", "node_id", "visible_evidence_ids",
            "obligations", "frontier", "selected_obligation", "status",
            "review_request", "graph_revision",
        }
        payload = {key: state.get(key) for key in allowed if key in state}
        payload["visible_evidence_ids"] = sorted({
            str(item) for item in payload.get("visible_evidence_ids", []) if str(item)
        })
        return payload
