"""Bounded service-level recovery checks for the autonomous loop."""

from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class RecoveryCheckError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def run_http_checks(
    checks: list[dict[str, Any]],
    *,
    allowed_hosts: set[str],
    default_samples: int = 3,
) -> dict[str, Any]:
    if not checks:
        return {"status": "indeterminate", "reason": "未配置服务级恢复检查", "checks": []}
    results = [_run_one(raw, allowed_hosts, default_samples) for raw in checks[:10]]
    if any(item["status"] == "failed" for item in results):
        status = "not_recovered"
    elif all(item["status"] == "passed" for item in results):
        status = "recovered"
    else:
        status = "indeterminate"
    return {
        "status": status,
        "reason": f"{sum(item['status'] == 'passed' for item in results)}/{len(results)} 项服务检查通过",
        "checks": results,
    }


def _run_one(raw: dict[str, Any], allowed_hosts: set[str], default_samples: int) -> dict[str, Any]:
    request_config = _prepare_request(raw, allowed_hosts)
    setup_raw = raw.get("setup") or []
    if not isinstance(setup_raw, list) or len(setup_raw) > 3:
        raise RecoveryCheckError("恢复检查 setup 必须是最多 3 项的列表")
    setup = [_prepare_request(item, allowed_hosts) for item in setup_raw]
    samples = max(1, min(int(raw.get("samples") or default_samples), 5))
    attempts: list[dict[str, Any]] = []
    cookie_jar = http.cookiejar.CookieJar()
    # Host validation above is the egress policy. Do not let ambient proxy
    # variables reroute a validated internal check to an unrelated proxy.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect,
        urllib.request.HTTPCookieProcessor(cookie_jar),
    )
    for _ in range(samples):
        setup_attempts = [_perform(item, opener) for item in setup]
        if any(not item["passed"] for item in setup_attempts):
            attempts.append({
                "status_code": 0,
                "latency_ms": round(sum(item["latency_ms"] for item in setup_attempts), 2),
                "passed": False,
                "error": "setup_failed",
                "setup": setup_attempts,
            })
            continue
        item = _perform(request_config, opener)
        if setup_attempts:
            item["setup"] = setup_attempts
        attempts.append(item)
    passed = all(item["passed"] for item in attempts)
    parsed = request_config["parsed"]
    return {
        "name": str(raw.get("name") or parsed.path or parsed.hostname)[:128],
        "url": urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", "")),
        "method": request_config["method"],
        "status": "passed" if passed else "failed",
        "attempts": attempts,
    }


def _prepare_request(raw: dict[str, Any], allowed_hosts: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecoveryCheckError("恢复检查项必须是对象")
    url = str(raw.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise RecoveryCheckError("恢复检查 URL 非法")
    if parsed.hostname not in allowed_hosts:
        raise RecoveryCheckError(f"恢复检查主机 {parsed.hostname} 不在允许列表")
    method = str(raw.get("method") or "GET").upper()
    if method not in {"GET", "POST"}:
        raise RecoveryCheckError("恢复检查只允许 GET/POST")
    timeout = max(1.0, min(float(raw.get("timeout_sec") or 5), 10.0))
    expected = {int(value) for value in (raw.get("expected_statuses") or [200])}
    if not expected or any(value < 100 or value > 599 for value in expected):
        raise RecoveryCheckError("expected_statuses 非法")
    body: bytes | None = None
    headers = {"User-Agent": "mini-drop-recovery-verifier/1.0"}
    if method == "POST":
        json_payload = raw.get("json")
        form_payload = raw.get("form")
        if isinstance(json_payload, dict) and form_payload is None:
            body = json.dumps(json_payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        elif isinstance(form_payload, dict) and json_payload is None:
            if any(not isinstance(value, (str, int, float, bool)) for value in form_payload.values()):
                raise RecoveryCheckError("POST form 只允许标量值")
            body = urllib.parse.urlencode(form_payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            raise RecoveryCheckError("POST 恢复检查必须且只能提供 json 或 form")
        if len(body) > 16 * 1024:
            raise RecoveryCheckError("恢复检查请求体超过 16KiB")
    return {
        "url": url,
        "parsed": parsed,
        "method": method,
        "timeout": timeout,
        "expected": expected,
        "body": body,
        "headers": headers,
    }


def _perform(config: dict[str, Any], opener) -> dict[str, Any]:
    started = time.monotonic()
    status_code = 0
    error = ""
    try:
        response = opener.open(
            urllib.request.Request(
                config["url"], data=config["body"], headers=config["headers"],
                method=config["method"],
            ),
            timeout=config["timeout"],
        )
        status_code = int(response.status)
        if len(response.read(64 * 1024 + 1)) > 64 * 1024:
            error = "response_too_large"
        response.close()
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        error = f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = str(exc)[:200]
    return {
        "status_code": status_code,
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
        "passed": status_code in config["expected"] and error != "response_too_large",
        "error": error,
    }
