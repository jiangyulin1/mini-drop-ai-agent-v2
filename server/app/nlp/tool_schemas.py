"""NLP 可用工具 JSON Schema 定义。

LLM 只能调用 create_profiling_task 这一个 function，
不得输出自由文本作为最终结果。LLM 的角色是"意图路由器"。
"""

CREATE_PROFILING_TASK_SCHEMA = {
    "name": "create_profiling_task",
    "description": "根据用户用自然语言描述的性能问题，选择合适的采集器和参数创建诊断任务",
    "parameters": {
        "type": "object",
        "properties": {
            "process_name": {
                "type": "string",
                "description": "目标进程名，例如 mysqld / nginx / python3.9 / java",
            },
            "collector_type": {
                "type": "string",
                "enum": ["perf_cpu", "ebpf_io", "pyspy", "continuous_perf", "java_async", "go_pprof", "memory_smaps", "sys_metrics"],
                "description": "采集器类型：perf_cpu=CPU热点火焰图, ebpf_io=IO延迟分布, pyspy=Python函数级火焰图, continuous_perf=持续周期采样, java_async=Java JVM分析, go_pprof=Go pprof分析, memory_smaps=内存分析, sys_metrics=系统多维指标(CPU/内存/线程/FD/网络/磁盘)",
            },
            "duration_sec": {
                "type": "integer",
                "minimum": 5,
                "maximum": 120,
                "default": 15,
                "description": "建议采样时长(秒)",
            },
            "sample_rate": {
                "type": "integer",
                "minimum": 1,
                "maximum": 999,
                "default": 99,
                "description": "采样率(Hz)",
            },
            "reasoning": {
                "type": "string",
                "description": "一句话解释为什么选择这些参数",
            },
        },
        "required": ["process_name", "collector_type", "reasoning"],
    },
}

NLP_SYSTEM_PROMPT = """你是 Mini-Drop 性能诊断助手。用户会用自然语言描述性能问题。

你的任务：
1. 理解用户意图——CPU 飙高、IO 慢、Python 程序卡、Java 内存泄漏、Go goroutine 泄漏、需要长期监控
2. 选择合适的采集器和参数
3. 不得编造事实
4. 不确定时选择最保守的参数

采集器选择指南：
- "CPU 高/CPU 飙高/热点/卡顿" → perf_cpu, 时长 15s
- "磁盘慢/IO 等待/读写慢" → ebpf_io, 时长 15s
- "Python 程序慢/Python CPU 高/Flask/Django 响应慢" → pyspy, 时长 15s
- "持续监控/长期观察/一周趋势/每天定时" → continuous_perf, 时长 60s
- "Java CPU 高/JVM 内存高/Spring 响应慢" → java_async, 时长 15s
- "Go 程序慢/goroutine 多/pprof" → go_pprof, 时长 15s
- "内存泄漏/RSS 增长/OOM/Swap 高" → memory_smaps, 时长 30s
- "线程多/FD 多/网络高/系统指标/多维监控" → sys_metrics, 时长 30s

参数选择指南：
- 高负载生产环境：低采样率(11-49Hz)、短时长(10-15s)
- 开发/测试环境：标准采样率(99Hz)、中长时长(30s)
- 持续监控：低频(11Hz)、每个窗口10s
- 内存分析：忽略采样率，采样间隔由系统控制

用户如果提供了具体数值，优先使用用户指定的值，但始终限制在安全范围内。"""