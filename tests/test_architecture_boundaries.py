from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

from fastapi import APIRouter
from sqlalchemy import UniqueConstraint

from server.app.http.routers.health import router as health_router
from server.app.application.repository_facade import FROZEN_REPOSITORY_SURFACE
from server.app.main import create_app
from server.app.models import Base
from server.app.routes.nlp import router as nlp_router
from server.app.routes.tasks import router as tasks_router
from server.app.runtime_services import repo as repository_service_proxy
from server.app.sql_repository import SqlRepository

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROUTERS = ROOT / "server" / "app" / "http" / "routers"


def _http_route_contract(application):
    """Flatten HTTP routes across FastAPI's eager and deferred router models."""

    for route in application.routes:
        contexts = (
            route.effective_route_contexts()
            if hasattr(route, "effective_route_contexts")
            else (route,)
        )
        for context in contexts:
            path = getattr(context, "path", None)
            methods = getattr(context, "methods", None)
            if path is not None and methods is not None:
                yield path, tuple(sorted(methods))


def test_routes_do_not_import_main():
    violations = []
    route_files = list(CANONICAL_ROUTERS.glob("*.py"))
    route_files.extend((ROOT / "server" / "app" / "routes").glob("*.py"))
    route_files.append(ROOT / "server" / "app" / "v6_routes.py")
    for path in route_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "server.app.main":
                violations.append(path.name)
            if isinstance(node, ast.Import):
                violations.extend(
                    path.name for alias in node.names if alias.name == "server.app.main"
                )
    assert violations == []


def test_main_has_no_star_route_imports():
    tree = ast.parse(
        (ROOT / "server" / "app" / "main.py").read_text(encoding="utf-8"),
    )
    star_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
    ]
    assert star_imports == []


def test_main_is_a_lightweight_entrypoint_and_factory_is_repeatable():
    main_path = ROOT / "server" / "app" / "main.py"
    assert len(main_path.read_text(encoding="utf-8").splitlines()) <= 100

    first = create_app()
    second = create_app()
    assert first is not second
    first_contract = set(_http_route_contract(first))
    second_contract = set(_http_route_contract(second))
    assert first_contract == second_contract
    assert first.state.container is not second.state.container
    assert (
        first.state.container.application_services
        is not second.state.container.application_services
    )
    assert first.state.container.repository is not second.state.container.repository


def test_routes_use_a_bound_application_facade_not_a_global_repository():
    assert not isinstance(repository_service_proxy, SqlRepository)
    for path in [
        *(ROOT / "server" / "app" / "routes").glob("*.py"),
        ROOT / "server" / "app" / "v6_routes.py",
    ]:
        source = path.read_text(encoding="utf-8")
        assert "from server.app.sql_repository import SqlRepository" not in source
        assert "SqlRepository()" not in source


def test_legacy_repository_facade_surface_cannot_grow_implicitly():
    paths = [
        *(ROOT / "server" / "app" / "routes").glob("*.py"),
        ROOT / "server" / "app" / "v6_routes.py",
        ROOT / "server" / "app" / "app_factory.py",
        *(ROOT / "server" / "app" / "grpc_services").glob("*.py"),
    ]
    used: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id in {"repo", "repository"}:
                    used.add(node.attr)
                if (
                    isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "self"
                    and node.value.attr == "_repo"
                ):
                    used.add(node.attr)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                target = node.args[0]
                if isinstance(target, ast.Name) and target.id in {"repo", "repository"}:
                    used.add(node.args[1].value)
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == "_repo"
                ):
                    used.add(node.args[1].value)
    assert used <= FROZEN_REPOSITORY_SURFACE


def test_models_have_one_canonical_package_source():
    assert not (ROOT / "server" / "app" / "models.py").exists()
    assert (ROOT / "server" / "app" / "models" / "__init__.py").is_file()


def test_unique_constraint_names_are_schema_unique_for_postgres():
    names = [
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    ]
    assert len(names) == len(set(names))


def test_importing_main_starts_no_threads_or_network_connections():
    script = """
import socket
import threading

def forbidden(*args, **kwargs):
    raise AssertionError('external side effect during import')

socket.create_connection = forbidden
socket.socket.connect = forbidden
threading.Thread.start = forbidden
import server.app.main
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_openapi_contract_has_unique_operations_and_core_paths():
    application = create_app()
    operations: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []
    for path, methods in _http_route_contract(application):
        for method in methods:
            key = (path, method)
            if key in operations:
                duplicates.append(key)
            operations.add(key)
    assert duplicates == []

    schema = application.openapi()
    assert schema["info"] == {"title": "Mini-Drop Server", "version": "0.1.0"}
    for path in (
        "/api/healthz",
        "/api/tasks",
        "/api/agents",
        "/api/v1/cases",
        "/api/v1/diagnoses",
        "/api/v1/cases/{case_id}/agent/turn",
    ):
        assert path in schema["paths"]


def test_health_router_is_an_explicit_api_router():
    assert isinstance(health_router, APIRouter)
    paths = {route.path for route in health_router.routes}
    assert paths == {"/api/healthz", "/api/livez", "/api/readyz"}


def test_tasks_router_is_explicit_and_bootstrap_independent():
    assert isinstance(tasks_router, APIRouter)
    paths = {route.path for route in tasks_router.routes}
    assert "/api/tasks" in paths
    assert "/api/task-kinds" in paths

    source = (ROOT / "server" / "app" / "routes" / "tasks.py").read_text(
        encoding="utf-8",
    )
    assert "server.app.main" not in source
    assert "@app." not in source


def test_nlp_router_is_explicit_and_bootstrap_independent():
    assert isinstance(nlp_router, APIRouter)
    assert {route.path for route in nlp_router.routes} == {
        "/api/nlp/parse",
        "/api/nlp/summarize",
    }
    source = (ROOT / "server" / "app" / "routes" / "nlp.py").read_text(
        encoding="utf-8",
    )
    assert "server.app.main" not in source
    assert "@app." not in source
