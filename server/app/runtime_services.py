"""Per-application service graph and request/background binding.

The exported names are immutable compatibility proxies.  They never own a
repository and resolve through the currently bound ``ApplicationServices``.
New code should receive ``ApplicationServices`` (or a narrower service) from
the application container directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator

from server.app.application.case_supervision_repository import CaseSupervisionRepository
from server.app.application.repository_facade import RepositoryApplicationFacade
from server.app.diagnosis import DiagnosisOrchestrator
from server.app.diagnosis.case_evidence import CaseEvidenceService
from server.app.diagnosis.collection_supervisor import CollectionSupervisor
from server.app.diagnosis.evidence_analysis import EvidenceAnalysisService
from server.app.diagnosis.cluster_scope import TargetResolver
from server.app.diagnosis.evidence_attachments import EvidenceAttachmentService
from server.app.diagnosis.fanout import FanoutCollectionService
from server.app.diagnosis.investigation_plan import InvestigationPlanService
from server.app.diagnosis.mcp_fact_resolver import McpEvidenceService, McpFactResolver
from server.app.diagnosis.reference_resolver import ReferenceResolver
from server.app.diagnosis.source_gateway import SourceGateway
from server.app.mcp_integration import MCPClientManager
from server.app.sql_repository import SqlRepository


@dataclass(frozen=True)
class ApplicationServices:
    persistence_adapter: Any
    repository: RepositoryApplicationFacade
    case_supervision_repository: CaseSupervisionRepository
    diagnosis_orchestrator: DiagnosisOrchestrator
    mcp_client_manager: MCPClientManager
    source_gateway: SourceGateway
    reference_resolver: ReferenceResolver
    evidence_attachment_service: EvidenceAttachmentService
    case_evidence_service: CaseEvidenceService
    collection_supervisor: CollectionSupervisor
    evidence_analysis_service: EvidenceAnalysisService
    investigation_plan_service: InvestigationPlanService
    target_resolver: TargetResolver
    fanout_service: FanoutCollectionService
    mcp_evidence_service: McpEvidenceService


def build_application_services(repository: Any | None = None) -> ApplicationServices:
    """Build a complete service graph around one persistence adapter."""

    concrete_repository = repository or SqlRepository()
    repository_facade = RepositoryApplicationFacade(concrete_repository)
    case_supervision_repository = CaseSupervisionRepository(concrete_repository)
    diagnosis_orchestrator = DiagnosisOrchestrator(concrete_repository)
    try:
        mcp_client_manager = MCPClientManager()
    except ValueError as exc:
        raise RuntimeError(f"invalid MCP connector configuration: {exc}") from exc
    source_gateway = SourceGateway(
        concrete_repository,
        diagnosis_orchestrator,
        extra_connectors=mcp_client_manager.connectors,
        extra_source_definitions=mcp_client_manager.source_definitions(),
    )
    reference_resolver = ReferenceResolver(concrete_repository)
    evidence_attachment_service = EvidenceAttachmentService(
        concrete_repository,
        reference_resolver,
    )
    case_evidence_service = CaseEvidenceService(concrete_repository)
    collection_supervisor = CollectionSupervisor(concrete_repository)
    evidence_analysis_service = EvidenceAnalysisService(concrete_repository)
    investigation_plan_service = InvestigationPlanService(concrete_repository)
    target_resolver = TargetResolver()
    fanout_service = FanoutCollectionService(concrete_repository)
    mcp_fact_resolver = McpFactResolver(
        native_collectors={"sys_metrics", "log_scan", "perf_cpu", "connection_probe"},
        registered_sources={item.source_id for item in mcp_client_manager.source_definitions()},
    )
    mcp_evidence_service = McpEvidenceService(
        mcp_fact_resolver,
        query_fn=lambda source_id, request, principal_id: source_gateway.query(
            source_id,
            request,
            principal_id=principal_id,
        ),
    )
    return ApplicationServices(
        persistence_adapter=concrete_repository,
        repository=repository_facade,
        case_supervision_repository=case_supervision_repository,
        diagnosis_orchestrator=diagnosis_orchestrator,
        mcp_client_manager=mcp_client_manager,
        source_gateway=source_gateway,
        reference_resolver=reference_resolver,
        evidence_attachment_service=evidence_attachment_service,
        case_evidence_service=case_evidence_service,
        collection_supervisor=collection_supervisor,
        evidence_analysis_service=evidence_analysis_service,
        investigation_plan_service=investigation_plan_service,
        target_resolver=target_resolver,
        fanout_service=fanout_service,
        mcp_evidence_service=mcp_evidence_service,
    )


_CURRENT_SERVICES: ContextVar[ApplicationServices | None] = ContextVar(
    "mini_drop_application_services",
    default=None,
)
_COMPATIBILITY_DEFAULT: ApplicationServices | None = None


def install_compatibility_default(services: ApplicationServices) -> None:
    """Install the first app graph solely for legacy direct-import callers."""

    global _COMPATIBILITY_DEFAULT
    if _COMPATIBILITY_DEFAULT is None:
        _COMPATIBILITY_DEFAULT = services


def current_application_services() -> ApplicationServices:
    services = _CURRENT_SERVICES.get() or _COMPATIBILITY_DEFAULT
    if services is None:
        raise RuntimeError("application services are not bound")
    return services


@contextmanager
def bind_application_services(services: ApplicationServices) -> Iterator[None]:
    token: Token[ApplicationServices | None] = _CURRENT_SERVICES.set(services)
    try:
        yield
    finally:
        _CURRENT_SERVICES.reset(token)


class _ServiceProxy:
    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attribute: str) -> Any:
        target = getattr(current_application_services(), self._name)
        return getattr(target, attribute)

    def __setattr__(self, name: str, value: Any) -> None:
        target = getattr(current_application_services(), self._name)
        setattr(target, name, value)


# Frozen compatibility names.  HTTP and background execution bind the correct
# application graph before these are resolved.
repo = _ServiceProxy("repository")
case_supervision_repository = _ServiceProxy("case_supervision_repository")
diagnosis_orchestrator = _ServiceProxy("diagnosis_orchestrator")
mcp_client_manager = _ServiceProxy("mcp_client_manager")
source_gateway = _ServiceProxy("source_gateway")
reference_resolver = _ServiceProxy("reference_resolver")
evidence_attachment_service = _ServiceProxy("evidence_attachment_service")
case_evidence_service = _ServiceProxy("case_evidence_service")
collection_supervisor = _ServiceProxy("collection_supervisor")
evidence_analysis_service = _ServiceProxy("evidence_analysis_service")
investigation_plan_service = _ServiceProxy("investigation_plan_service")
target_resolver = _ServiceProxy("target_resolver")
fanout_service = _ServiceProxy("fanout_service")
mcp_evidence_service = _ServiceProxy("mcp_evidence_service")
