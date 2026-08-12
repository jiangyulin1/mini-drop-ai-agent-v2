from server.app.diagnosis.distributed_actuation import _task_idempotency_key


def test_remote_action_task_key_is_stable_for_control_retries():
    first = _task_idempotency_key("restart", "shop_paymentservice", "case-1:1:1:restart")
    retry = _task_idempotency_key("restart", "shop_paymentservice", "case-1:1:1:restart")
    another_action = _task_idempotency_key("restart", "shop_paymentservice", "case-1:2:2:restart")

    assert first == retry
    assert first != another_action
    assert len(first) <= 128
