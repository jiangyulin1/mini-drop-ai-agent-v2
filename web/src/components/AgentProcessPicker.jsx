import { useEffect, useRef, useState } from "react";
import { Alert, Input, InputNumber, Select, Typography } from "antd";
import { scanAgentProcesses } from "../api/client";
import styles from "./AgentProcessPicker.module.css";

function processLabel(process) {
  const name = process.comm || process.cmdline || `PID ${process.pid}`;
  const detail = [
    process.cmdline && process.cmdline !== name ? process.cmdline : "",
    process.cpu_percent != null ? `CPU ${process.cpu_percent}%` : "",
    process.rss_mb != null ? `${process.rss_mb} MB` : "",
  ].filter(Boolean).join(" · ");
  return {
    title: `${process.pid} · ${name}`,
    detail,
  };
}

/**
 * Select a process from one explicit Worker. A PID is never reused across
 * Workers: changing agentId clears the remote candidates and the parent is
 * expected to clear its selected value as well.
 */
export default function AgentProcessPicker({
  agentId,
  agentLabel,
  keyword,
  onKeywordChange,
  value,
  onChange,
  disabled = false,
}) {
  const [processes, setProcesses] = useState([]);
  const [scanning, setScanning] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const latestAgentId = useRef(agentId);
  const latestKeyword = useRef(keyword);
  latestAgentId.current = agentId;
  latestKeyword.current = keyword;

  useEffect(() => {
    requestSequence.current += 1;
    setProcesses([]);
    setSearched(false);
    setError("");
    setScanning(false);
  }, [agentId, keyword]);

  useEffect(() => () => {
    requestSequence.current += 1;
  }, []);

  async function search() {
    const query = keyword.trim();
    if (!agentId) {
      setError("请先选择 Worker");
      return;
    }
    if (!query) {
      setError("请输入进程名或服务名");
      return;
    }
    const scannedAgentId = agentId;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setScanning(true);
    setSearched(false);
    setError("");
    try {
      const result = await scanAgentProcesses(agentId, { query, timeoutSec: 15 });
      if (
        requestSequence.current !== requestId
        || latestAgentId.current !== scannedAgentId
        || latestKeyword.current.trim() !== query
      ) return;
      const next = result?.processes || [];
      setProcesses(next);
      setSearched(true);
      if (next.length === 1) onChange(next[0].pid);
    } catch (scanError) {
      if (requestSequence.current !== requestId) return;
      setProcesses([]);
      setSearched(true);
      setError(scanError.message || "进程扫描失败");
    } finally {
      if (requestSequence.current === requestId) setScanning(false);
    }
  }

  return (
    <div className={styles.picker}>
      <div className={styles.searchRow}>
        <Input.Search
          value={keyword}
          onChange={(event) => onKeywordChange(event.target.value)}
          onSearch={search}
          enterButton="在 Worker 上查找"
          loading={scanning}
          disabled={disabled || !agentId}
          placeholder="进程名或服务名，例如 mysqld"
          aria-label="目标进程关键字"
        />
        <InputNumber
          min={1}
          max={4194304}
          value={value}
          onChange={onChange}
          disabled={disabled || !agentId}
          prefix="PID"
          placeholder="手动填写"
          className={styles.pidInput}
          aria-label="目标 PID"
        />
      </div>

      {error && <Alert className={styles.feedback} type="error" showIcon message={error} />}
      {searched && !error && processes.length === 0 && (
        <Alert
          className={styles.feedback}
          type="warning"
          showIcon
          message={`在 ${agentLabel || agentId} 上没有找到匹配进程，可调整关键字或手动填写 PID。`}
        />
      )}
      {processes.length > 0 && (
        <div className={styles.results}>
          <Typography.Text type="secondary">确认该 Worker 上的目标进程</Typography.Text>
          <Select
            value={processes.some((process) => Number(process.pid) === Number(value)) ? Number(value) : undefined}
            onChange={onChange}
            showSearch
            optionFilterProp="searchText"
            placeholder={`找到 ${processes.length} 个候选，请选择`}
            options={processes.map((process) => {
              const label = processLabel(process);
              return {
                value: process.pid,
                searchText: `${process.pid} ${process.comm || ""} ${process.cmdline || ""}`,
                label: (
                  <span className={styles.option}>
                    <strong>{label.title}</strong>
                    {label.detail && <small>{label.detail}</small>}
                  </span>
                ),
              };
            })}
          />
        </div>
      )}
      <Typography.Text type="secondary" className={styles.safetyHint}>
        扫描只读取 {agentLabel || "所选 Worker"} 的进程列表；创建任务前仍需确认 PID。
      </Typography.Text>
    </div>
  );
}
