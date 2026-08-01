from server.app.flamegraph_parser import extract_top_functions_from_svg


def test_extract_top_functions_from_pyspy_svg_titles():
    svg = """
    <svg>
      <g class="func_g"><title>all (200 samples, 100.00%)</title></g>
      <g class="func_g"><title>0x7f123abc (libc.so.6) (190 samples, 95.00%)</title></g>
      <g class="func_g"><title>run (threading.py:1010) (180 samples, 90.00%)</title></g>
      <g class="func_g"><title>cpu_hotspot (work.py:10) (115 samples, 9.30%)</title></g>
      <g class="func_g"><title>cpu_hotspot (work.py:10) (43 samples, 3.48%)</title></g>
      <g class="func_g"><title>&lt;module&gt; (work.py:20) (4 samples, 0.32%)</title></g>
    </svg>
    """

    result = extract_top_functions_from_svg(svg)

    assert result[0] == {
        "name": "cpu_hotspot (work.py:10)",
        "samples": 158,
        "percent": 12.78,
        "source": "flamegraph_svg",
    }
    assert result[1]["name"] == "<module> (work.py:20)"


def test_extract_top_functions_rejects_empty_or_malformed_titles():
    assert extract_top_functions_from_svg("") == []
    assert extract_top_functions_from_svg("<svg><title>no samples here</title></svg>") == []
