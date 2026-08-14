"""Mini-Drop 智能归因模块。

7 层架构（E9 后保留被新管线复用的确定性/校验层）：
  1. 证据层 (evidence.py)   — 结构化证据采集（orchestrator 复用）
  2. 候选归因 (candidates.py) — 规则引擎自动匹配（orchestrator / rca-analysis Tool 复用）
  3. 置信度校准 (calibrator.py) — 多维加权评分 + 反馈先验（orchestrator 复用）
  4. LLM 推理 (llm_client.py) — DeepSeek API + schema 注入 + few-shot（ai_validation 复用）
  5. prompt (prompt.py)     — 系统提示词模板
  6. models (models.py)     — 证据输入 / 报告 / 候选模型

E9 已删除的重复编排：report.py（run_diagnosis/run_diagnosis_context 一次性
LLM 归因编排）、repair.py（旧修复计划）、tools.py（旧只读工具链）。Task 结果页
入口已收敛为「创建调查 Case」，由持续调查管线（diagnosis/orchestrator +
RulesOnlyReasoner 降级）产出结论。
"""
