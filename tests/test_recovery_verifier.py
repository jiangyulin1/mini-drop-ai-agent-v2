import io
import urllib.error

import pytest

from server.app.diagnosis import recovery_verifier


class _Response:
    status = 302

    def read(self, _limit):
        return b""

    def close(self):
        pass


def test_synthetic_check_runs_setup_and_form_with_shared_opener(monkeypatch):
    calls = []

    class _Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.data, request.headers.get("Content-type"), timeout))
            return _Response()

    monkeypatch.setattr(recovery_verifier.urllib.request, "build_opener", lambda *_: _Opener())
    result = recovery_verifier.run_http_checks(
        [{
            "name": "checkout",
            "url": "http://shop/cart/checkout",
            "method": "POST",
            "form": {"email": "ops@example.com"},
            "expected_statuses": [302],
            "samples": 1,
            "setup": [{
                "url": "http://shop/cart",
                "method": "POST",
                "form": {"product_id": "sku-1", "quantity": 1},
                "expected_statuses": [302],
            }],
        }],
        allowed_hosts={"shop"},
    )

    assert result["status"] == "recovered"
    assert [item[0] for item in calls] == ["http://shop/cart", "http://shop/cart/checkout"]
    assert calls[0][2] == "application/x-www-form-urlencoded"


def test_synthetic_check_rejects_unapproved_setup_host():
    with pytest.raises(recovery_verifier.RecoveryCheckError):
        recovery_verifier.run_http_checks(
            [{
                "url": "http://shop/health",
                "setup": [{"url": "http://metadata.internal/", "method": "GET"}],
            }],
            allowed_hosts={"shop"},
        )
