from scripts.run_github_pr_attribution_eval import load_case_specs, selected_specs


def test_external_case_manifest_is_appended_without_network(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(
        '{"cases":[{"case_id":"external-1","repo":"example/project","number":1,'
        '"oracle":{},"runtime":{}}]}',
        encoding="utf-8",
    )

    specs = load_case_specs(str(manifest))

    assert len(specs) == 10
    assert selected_specs("external-1", specs)[0]["repo"] == "example/project"


def test_external_case_manifest_rejects_duplicate_case_id(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(
        '{"cases":[{"case_id":"prometheus-19393","repo":"example/project",'
        '"number":1,"oracle":{},"runtime":{}}]}',
        encoding="utf-8",
    )

    try:
        load_case_specs(str(manifest))
    except SystemExit as exc:
        assert "duplicate case id" in str(exc)
    else:
        raise AssertionError("duplicate case id must be rejected")
