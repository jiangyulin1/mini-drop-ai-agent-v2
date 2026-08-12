import { useCallback, useState } from "react";
import {
  Alert,
  Button,
  Card,
  InputNumber,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ClearOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import {
  dryRunAction,
  executeAction,
  rollbackAction,
} from "../api/client";

const CLEANUP_ACTION = "mini-drop.cleanup-expired-cache";

function formatBytes(value) {
  if (!value) return "0 B";
  if (value > 1024 * 1024 * 1024) return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (value > 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value > 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

/** 存储维护：诊断过期产物清理（dry-run → 批准 → 执行 → 可回滚）。 */
export default function StorageMaintenance() {
  const [retentionDays, setRetentionDays] = useState(7);
  const [dryRunResult, setDryRunResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  const [lastExecution, setLastExecution] = useState(null);

  const runDryRun = useCallback(async () => {
    setLoading(true);
    try {
      const result = await dryRunAction(CLEANUP_ACTION, {
        parameters: { retention_days: retentionDays },
      });
      setDryRunResult(result);
      message.success(`dry-run 完成：发现 ${result.dry_run?.candidate_count || 0} 个过期产物`);
    } catch (error) {
      message.error(`dry-run 失败：${error.message}`);
    } finally {
      setLoading(false);
    }
  }, [retentionDays]);

  async function runExecute() {
    if (!dryRunResult?.attempt_id) {
      message.warning("请先执行 dry-run");
      return;
    }
    const approvedDryRun = dryRunResult;
    const approvedRetentionDays = approvedDryRun.dry_run?.retention_days ?? retentionDays;
    Modal.confirm({
      title: "确认执行清理？",
      icon: <SafetyCertificateOutlined style={{ color: "#fa8c16" }} />,
      content: (
        <div>
          <p>将把 <strong>{approvedDryRun.dry_run?.candidate_count || 0}</strong> 个超过保留期
            （{approvedRetentionDays} 天）的诊断产物移入隔离区，释放约 {formatBytes(approvedDryRun.dry_run?.total_bytes)}。</p>
          <p style={{ color: "#999", fontSize: 12 }}>
            批准对象：{approvedDryRun.attempt_id}。移动而非删除，所有操作写入审计日志。
          </p>
        </div>
      ),
      okText: "确认执行",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        setExecuting(true);
        try {
          const result = await executeAction(CLEANUP_ACTION, {
            dry_run_attempt_id: approvedDryRun.attempt_id,
            dry_run_passed: true,
            rollback_ready: true,
            environment: "production",
          });
          setLastExecution(result);
          setDryRunResult(null);
          message.success(`已隔离 ${result.executed?.length || 0} 个产物目录`);
        } catch (error) {
          message.error(`执行失败：${error.message}`);
        } finally {
          setExecuting(false);
        }
      },
    });
  }

  async function runRollback() {
    if (lastExecution?.action_id !== CLEANUP_ACTION || lastExecution?.stage !== "COMPLETED") {
      message.warning("本页尚无可回滚的清理执行");
      return;
    }
    Modal.confirm({
      title: "确认恢复全局隔离区？",
      content: "当前后端会恢复隔离区内所有可恢复目录，范围可能包含其他清理批次，并非只恢复最近一次执行。",
      okText: "恢复",
      cancelText: "取消",
      onOk: async () => {
        setRollingBack(true);
        try {
          const result = await rollbackAction(CLEANUP_ACTION, { environment: "production" });
          message.success(`已恢复 ${result.executed?.length || 0} 个目录`);
          setLastExecution(result);
        } catch (error) {
          message.error(`回滚失败：${error.message}`);
        } finally {
          setRollingBack(false);
        }
      },
    });
  }

  const items = dryRunResult?.dry_run?.items || [];
  return (
    <Card
      size="small"
      title={
        <Space>
          <ClearOutlined style={{ color: "#fa8c16" }} />
          存储维护
          <Tag color="purple">低风险可回滚</Tag>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        message="清理 Mini-Drop 自身的过期诊断缓存。执行前必须 dry-run 查看清单，执行后可从隔离区回滚。"
        style={{ marginBottom: 12 }}
      />
      <Space wrap style={{ marginBottom: 12 }}>
        <Space>
          <Typography.Text>保留</Typography.Text>
          <InputNumber
            min={1}
            max={365}
            value={retentionDays}
            onChange={(value) => {
              setRetentionDays(value || 7);
              setDryRunResult(null);
            }}
            addonAfter="天"
            style={{ width: 130 }}
          />
        </Space>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={runDryRun}>
          ① 查看可清理项
        </Button>
        <Button
          type="primary"
          danger
          icon={<ClearOutlined />}
          loading={executing}
          disabled={!dryRunResult?.attempt_id}
          onClick={runExecute}
        >
          ② 批准并执行
        </Button>
        <Button
          icon={<RollbackOutlined />}
          loading={rollingBack}
          disabled={lastExecution?.action_id !== CLEANUP_ACTION || lastExecution?.stage !== "COMPLETED"}
          onClick={runRollback}
        >
          恢复全局隔离区
        </Button>
      </Space>

      {dryRunResult && (
        <Table
          size="small"
          rowKey="task_id"
          pagination={false}
          dataSource={items}
          summary={() => (
            <Table.Summary.Row>
              <Table.Summary.Cell index={0} colSpan={4}>
                <Typography.Text strong>
                  合计 {items.length} 项 · {formatBytes(dryRunResult.dry_run?.total_bytes)}
                </Typography.Text>
              </Table.Summary.Cell>
            </Table.Summary.Row>
          )}
          columns={[
            { title: "任务目录", dataIndex: "task_id", width: 260 },
            { title: "大小", dataIndex: "size_bytes", width: 100, render: formatBytes },
            { title: "已过期（天）", dataIndex: "age_days", width: 110 },
            { title: "位置", dataIndex: "path", ellipsis: true },
          ]}
          locale={{ emptyText: "没有超过保留期的产物" }}
        />
      )}

      {lastExecution && (
        <Alert
          type="success"
          showIcon
          message={`最近一次执行：${lastExecution.stage === "COMPLETED" ? "已完成" : lastExecution.stage}`}
          description={`处理 ${lastExecution.executed?.length || 0} 个目录。${lastExecution.idempotent_replay ? "（幂等重放）" : ""}`}
          style={{ marginTop: 12 }}
        />
      )}
    </Card>
  );
}
