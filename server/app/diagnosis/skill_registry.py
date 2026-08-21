"""Deterministic Skill Registry for the Agent Beta (G7).

Skills are small, versioned procedure prompts.  They are selected by the
domain kernel before a Turn and only shape strategy; they never substitute for
runtime Evidence and never add execution permissions.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel

SKILL_SCHEMA_VERSION = "1.0"


class SkillSpec(StrictModel):
    skill_id: str
    version: str = SKILL_SCHEMA_VERSION
    title: str
    applies_to: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    negative_triggers: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    procedure: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)


SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        skill_id="answer_stability",
        title="稳定可复现诊断回答",
        applies_to=["all"],
        triggers=["always"],
        required_evidence=[],
        procedure=[
            "先读取 Case Snapshot 和已有 Evidence，复用既有结论",
            "没有新 Evidence 或新用户纠正时，不得改变 Primary/Contributing/Propagation 结论",
            "回答使用固定结构：结论 / 证据依据 / 仍缺失的事实 / 下一步",
            "重复问题必须引用相同 Evidence ID 和相同结论措辞",
            "不确定时明确 abstain，不生成多方向猜测",
        ],
        stop_conditions=["已引用 Evidence 且无新事实时停止", "证据不足时输出 Gap 而非新猜测"],
    ),
    SkillSpec(
        skill_id="linux_cpu_diagnosis",
        title="Linux CPU 高诊断",
        applies_to=["cpu", "linux"],
        triggers=["cpu", "用户态", "系统态", "iowait", "调度", "热点", "火焰图"],
        negative_triggers=["内存泄漏", "gc", "锁等待", "网络丢包"],
        required_evidence=["sys_metrics", "perf_cpu", "process_scan"],
        procedure=[
            "区分 user/sys/iowait/steal 和进程级 CPU",
            "用 perf_cpu 或已有火焰图证明热点",
            "结合时间窗和变更记录判断是否新出现",
            "高 CPU 只是现象，没有 Profile 时不得断言具体代码行",
        ],
        stop_conditions=["只有瞬时 CPU 百分比时 abstain", "无法采样时输出 Gap"],
    ),
    SkillSpec(
        skill_id="linux_memory_diagnosis",
        title="Linux 内存/泄漏诊断",
        applies_to=["memory", "linux"],
        triggers=["内存", "rss", "swap", "oom", "泄漏", "增长"],
        negative_triggers=["gc", "网络", "锁等待"],
        required_evidence=["memory_smaps", "sys_metrics", "runtime_snapshot"],
        procedure=[
            "要求时间序列 RSS/PSS/Swap，而不是单点值",
            "检查增长速率、限制和稳定负载",
            "使用 runtime_snapshot 区分堆/缓存/元数据",
            "内存增长不等于泄漏",
        ],
        stop_conditions=["缺少时间序列或基线时 abstain"],
    ),
    SkillSpec(
        skill_id="jvm_gc_diagnosis",
        title="JVM GC 压力诊断",
        applies_to=["jvm", "java"],
        triggers=["gc", "full gc", "暂停", "堆", "老年代", "新生代"],
        negative_triggers=["cpu sys", "磁盘满"],
        required_evidence=["runtime_snapshot", "java_async", "sys_metrics"],
        procedure=[
            "检查暂停 P95/P99、GC 占比、分代占用和分配速率",
            "将 GC 暂停与请求延迟时间窗对齐",
            "区分 GC 根因与下游放大",
        ],
        stop_conditions=["缺少 GC 日志/采样时 abstain"],
    ),
    SkillSpec(
        skill_id="mysql_lock_diagnosis",
        title="MySQL 锁等待诊断",
        applies_to=["mysql", "database"],
        triggers=["mysql", "锁等待", "innodb", "死锁", "慢查询", "事务"],
        negative_triggers=["cpu profile", "ebpf io"],
        required_evidence=["runtime_snapshot", "log_scan", "sys_metrics"],
        procedure=[
            "建立阻塞事务到被阻塞事务的关系",
            "检查隔离级别、访问顺序、事务时长",
            "不得仅凭查询慢就断言锁等待",
        ],
        stop_conditions=["无法获取锁/事务信息时 abstain"],
    ),
    SkillSpec(
        skill_id="tcp_retransmit_diagnosis",
        title="TCP 重传与网络诊断",
        applies_to=["network", "tcp"],
        triggers=["重传", "丢包", "rtt", "超时", "连接", "网络"],
        negative_triggers=["磁盘满", "内存泄漏"],
        required_evidence=["connection_probe", "sys_metrics"],
        procedure=[
            "同一时间窗检查两端 RTT/重传/连接分布",
            "应用变慢也会引起重试与重传，避免倒因为果",
            "跨节点时使用拓扑约束传播方向",
        ],
        stop_conditions=["只有单点重传率时 abstain"],
    ),
    SkillSpec(
        skill_id="cluster_attribution",
        title="集群与拓扑归因",
        applies_to=["cluster", "distributed"],
        triggers=["集群", "跨节点", "下游", "上游", "扇出", "worker", "传播"],
        negative_triggers=["单机"],
        required_evidence=["membership_snapshot", "fanout_run", "sys_metrics"],
        procedure=[
            "使用 Membership Snapshot 约束结论范围",
            "比较同角色节点、上下游和时间窗",
            "先告警节点不一定是根因节点",
        ],
        stop_conditions=["覆盖率不足时拒绝全局结论"],
    ),
    SkillSpec(
        skill_id="unknown_topology_discovery",
        title="未知拓扑依赖发现与边界归因",
        applies_to=["network", "cluster", "distributed"],
        triggers=["未知拓扑", "调用链", "上游", "下游", "跨主机", "依赖发现", "pid"],
        negative_triggers=["已确认完整拓扑"],
        required_evidence=["network_discovery", "dependency_graph", "sys_metrics"],
        procedure=[
            "从已授权的种子 PID/Agent 开始，先读取 network_discovery 和 dependency_graph 投影",
            "只沿中高置信且能映射到已注册 Agent 的边扩展；未注册远端保留 external_unmanaged_endpoint",
            "NAT、负载均衡、代理或 VIP 无法唯一解析时保留 virtual_endpoint，不伪装成真实后端",
            "通信边只能证明依赖和传播路径；根因仍需远端资源、日志、Profile 或失败信号闭合",
            "分别标注 primary cause、contributing factor、amplifier 与 propagation",
        ],
        stop_conditions=[
            "达到跳数、主机、进程、边或采集预算时停止扩展",
            "覆盖不足、时间不对齐或身份冲突时输出 insufficient_coverage",
        ],
    ),
    SkillSpec(
        skill_id="evidence_gap",
        title="证据缺口报告",
        applies_to=["all"],
        triggers=["证据不足", "采集失败", "缺少", "看不到", "无法确认"],
        required_evidence=[],
        procedure=[
            "列出已执行采集、失败原因、当前数据能证明/不能证明什么",
            "给出最小补证动作和下一动作",
            "不得使用空泛的“证据不足”",
        ],
        stop_conditions=["已列出具体 Gap 时停止"],
    ),
)


class SkillRegistry:
    def __init__(self) -> None:
        self._by_id = {item.skill_id: item for item in SKILLS}

    def list_skills(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in SKILLS]

    def select_skills(
        self,
        *,
        goal: str,
        target_scope: dict[str, Any] | None = None,
        evidence_summary: list[dict[str, Any]] | None = None,
        missing_facts: list[str] | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        text = " ".join([
            goal or "",
            json_scope(target_scope),
            " ".join(missing_facts or []),
        ]).lower()
        scored: list[tuple[int, str, SkillSpec]] = []
        for skill in SKILLS:
            positive = sum(1 for term in skill.triggers if term and term.lower() in text)
            negative = sum(1 for term in skill.negative_triggers if term and term.lower() in text)
            if skill.skill_id == "answer_stability":
                score = 1000
            elif skill.skill_id == "evidence_gap" and positive:
                score = 500 + positive
            elif positive:
                score = positive * 10 - negative * 20
            else:
                continue
            if score > 0:
                scored.append((score, skill.skill_id, skill))
        # 稳定回答技能永远排第一
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [
            {
                "skill_id": skill.skill_id,
                "version": skill.version,
                "title": skill.title,
                "procedure": skill.procedure,
                "required_evidence": skill.required_evidence,
                "stop_conditions": skill.stop_conditions,
            }
            for _, _, skill in scored[:limit]
        ]


def json_scope(scope: dict[str, Any] | None) -> str:
    if not scope:
        return ""
    return " ".join(str(value) for key, value in scope.items() if key in {
        "service_id", "cluster_id", "workload", "environment",
    })


SKILL_REGISTRY = SkillRegistry()
