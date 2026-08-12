from server.app.prometheus_metrics import MetricsRegistry


def test_observations_use_prometheus_summary_format():
    registry = MetricsRegistry()
    for value in (1, 2, 3, 4, 5):
        registry.histogram_observe("mini_drop_latency_ms", value)

    output = registry.generate()

    assert "# TYPE mini_drop_latency_ms summary" in output
    assert 'mini_drop_latency_ms{quantile="0.5"} 3.0' in output
    assert 'mini_drop_latency_ms{quantile="0.95"} 5.0' in output
    assert "mini_drop_latency_ms_count 5" in output
    assert "mini_drop_latency_ms_sum 15.0" in output
    assert "mini_drop_latency_ms_p95" not in output


def test_label_values_are_escaped_for_prometheus_text_format():
    registry = MetricsRegistry()
    registry.counter_inc(
        "mini_drop_example_total",
        {"value": 'line one\nline "two" \\ end'},
    )

    output = registry.generate()

    assert 'value="line one\\nline \\"two\\" \\\\ end"' in output
