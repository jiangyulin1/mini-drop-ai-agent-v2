from __future__ import annotations

from scripts.reproducible_text import canonical_text_sha256


def test_windows_utf8_and_line_endings_are_reproducible(tmp_path):
    lf = tmp_path / "lf.md"
    crlf = tmp_path / "crlf.md"
    lf.write_bytes("Mini-Drop 证据\n第二行\n".encode("utf-8"))
    crlf.write_bytes("Mini-Drop 证据\r\n第二行\r\n".encode("utf-8"))

    assert canonical_text_sha256(lf) == canonical_text_sha256(crlf)
