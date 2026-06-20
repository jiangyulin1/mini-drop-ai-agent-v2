"""micro-drop CLI tests."""

import json

from server.app import cli


def test_ai_config_prints_provider(monkeypatch, capsys):
    monkeypatch.setenv("MINI_DROP_AI_PROVIDER", "test-provider")
    monkeypatch.setenv("MINI_DROP_AI_BASE_URL", "https://ai.example.com")
    monkeypatch.setenv("MINI_DROP_AI_MODEL", "m1")
    code = cli.main(["ai-config"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["provider"] == "test-provider"
    assert out["model"] == "m1"


def test_parse_uses_rule_fallback_without_key(monkeypatch, capsys):
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    code = cli.main(["parse", "mysqld", "CPU", "飙高"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["collector_type"] == "perf_cpu"
    assert out["process_name"] == "mysqld"


def test_diff_top_returns_nonzero_on_threshold(tmp_path, capsys):
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    base.write_text('[{"name":"fib","percent":10.0}]', encoding="utf-8")
    head.write_text('[{"name":"fib","percent":20.0}]', encoding="utf-8")
    code = cli.main([
        "diff-top", "--base", str(base), "--head", str(head), "--threshold", "5",
    ])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["failed"] is True
    assert out["changes"][0]["delta_percent"] == 10.0


def test_ci_check_returns_nonzero_on_regression(tmp_path, capsys):
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    base.write_text('[{"name":"fib","percent":10.0}]', encoding="utf-8")
    head.write_text('[{"name":"fib","percent":16.0}]', encoding="utf-8")

    code = cli.main([
        "ci-check", "--base", str(base), "--head", str(head), "--threshold", "5",
    ])
    out = json.loads(capsys.readouterr().out)

    assert code == 2
    assert out["ci_status"] == "failed"
    assert out["failed"] is True


def test_alert_returns_nonzero_when_threshold_exceeded(tmp_path, capsys):
    top = tmp_path / "top.json"
    top.write_text('[{"name":"fib","samples":1000,"percent":72.5}]', encoding="utf-8")

    code = cli.main([
        "alert", "--top-json", str(top), "--hotspot-threshold", "70", "--sample-threshold", "100",
    ])
    out = json.loads(capsys.readouterr().out)

    assert code == 2
    assert out["triggered"] is True
    assert out["top_function"] == "fib"


def test_export_summary_markdown(tmp_path, capsys):
    top = tmp_path / "top.json"
    top.write_text('[{"name":"fib","samples":12,"percent":33.3}]', encoding="utf-8")

    code = cli.main(["export-summary", "--top-json", str(top), "--format", "markdown"])
    out = capsys.readouterr().out

    assert code == 0
    assert "| Rank | Function | Samples | Percent |" in out
    assert "`fib`" in out


def test_keywords_and_suggest(capsys):
    code = cli.main(["keywords", "--kind", "collectors"])
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert set(out["collectors"]) >= {"perf_cpu", "ebpf_io", "pyspy", "continuous_perf", "java_async", "go_pprof", "memory_smaps"}

    code = cli.main(["suggest", "ci", "--kind", "commands"])
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["commands"] == ["ci-check"]


def test_completion_scripts_include_keywords(capsys):
    code = cli.main(["completion", "--shell", "bash"])
    out = capsys.readouterr().out

    assert code == 0
    assert "complete -F _micro_drop_complete micro-drop" in out
    assert "ci-check" in out
    assert "perf_cpu" in out

    code = cli.main(["completion", "--shell", "powershell"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Register-ArgumentCompleter" in out
    assert "'ci-check'" in out


def test_batch_diagnose_uses_rule_engine(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "task_metadata": {
            "task_id": "task_cli",
            "collector_type": "perf_cpu",
            "status": "DONE",
        },
        "top_functions": [
            {"name": "fib_recursive", "samples": 1000, "percent": 80.0},
        ],
        "suggestions": ["CPU hotspot detected"],
    }), encoding="utf-8")

    code = cli.main(["batch-diagnose", "--dir", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["total"] == 1
    assert out["items"][0]["ok"] is True
    assert out["items"][0]["top_cause"] == "cpu_hotspot_recursive"
