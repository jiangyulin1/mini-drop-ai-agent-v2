"""NLP 结果总结与追问建议。

任务完成后，基于结构化数据（TopN/火焰图概要/建议）
生成一段中文总结，并可追问下一步动作。
"""

from __future__ import annotations

from server.app.ai_provider import chat_completions, get_ai_settings, is_feature_enabled
from server.app.logging_utils import log_event

SUMMARIZE_SYSTEM_PROMPT = """你是 Mini-Drop 性能诊断报告撰写助手。

基于以下结构化数据写一段 150 字以内的中文总结。
格式：(1) 主要发现 (2) 最可能原因 (3) 建议下一步。
不要编造数据中没有的事实。"""
MAX_SUMMARY_CHARS = 150


def summarize(
    top_functions: list[dict],
    suggestions: list[str] | None = None,
    flamegraph_summary: str = "",
) -> str:
    """基于诊断结果生成中文总结。

    API Key 未配置时使用规则模板。
    """
    if not top_functions:
        return "当前任务未产出热点函数数据，建议检查采集配置或目标进程状态。"

    if not is_feature_enabled("summarize"):
        return _template_summary(top_functions, suggestions or [])

    data_text = f"热点函数: {json_dumps(top_functions[:5])}"
    if flamegraph_summary:
        data_text += f"\n火焰图概要: {flamegraph_summary}"
    if suggestions:
        data_text += f"\n规则建议: {'; '.join(suggestions[:3])}"

    try:
        resp = chat_completions(
            {
                "model": get_ai_settings().model,
                "messages": [
                    {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": data_text},
                ],
                "temperature": 0.3,
                "max_tokens": 300,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            return _truncate_summary(content)
    except Exception as exc:
        log_event("warning", "ai_summarizer_failed", error=str(exc)[:200])

    return _template_summary(top_functions, suggestions or [])


def _truncate_summary(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Enforce the product contract even when a model ignores prompt length."""
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(1, limit - 1)].rstrip("，,；;。 ") + "…"


def suggest_followup(
    top_functions: list[dict],
    collector_type: str = "perf_cpu",
    ebpf_metrics: dict | None = None,
) -> list[str]:
    """生成追问建议列表。"""
    questions: list[str] = []

    if not top_functions:
        return ["建议使用 perf_cpu 对目标进程进行 CPU 采样以获得热点函数"]

    top1 = top_functions[0].get("name", "")
    top1_pct = top_functions[0].get("percent", 0)

    if top1_pct > 60:
        questions.append(f"热点集中在 {top1}({top1_pct}%)，建议用 py-spy 确认是否在 Python 代码层")

    if collector_type == "perf_cpu" and top1_pct > 40:
        questions.append("建议在同一个 PID 上运行 eBPF IO 采集，确认是否存在 IO 瓶颈的叠加效应")

    if ebpf_metrics:
        metrics = ebpf_metrics.get("io_latency_us", {})
        if metrics and any(int(v) > 0 for v in metrics.values()):
            questions.append("eBPF 显示 IO 延迟有分布，建议进一步用 iostat 查看磁盘队列深度")

    if len(top_functions) >= 3:
        questions.append(f"Top3 热点: {top_functions[0]['name']}/{top_functions[1]['name']}/{top_functions[2]['name']}，"
                         "可创建 continuous profiling 任务持续观察趋势")

    if not questions:
        questions.append("建议增加采样时长或采样率以获取更充分的样本")

    return questions


def _template_summary(top_functions: list[dict], suggestions: list[str]) -> str:
    parts = []
    if top_functions:
        top1 = top_functions[0]
        parts.append(f"主要发现：热点集中在 {top1['name']}（{top1['percent']}%采样数）")
    parts.append("最可能原因：请触发 AI 归因进行深度分析")
    if suggestions:
        parts.append(f"建议下一步：{suggestions[0]}")
    return "。".join(parts)


def json_dumps(obj, **kw):
    import json
    return json.dumps(obj, ensure_ascii=False, **kw)
