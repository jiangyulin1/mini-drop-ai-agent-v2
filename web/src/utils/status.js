/**
 * 状态相关的公共工具函数。
 *
 * 所有页面和组件通过此模块获取状态颜色和标签配置，
 * 避免在 Dashboard / TaskResult 等文件中重复定义。
 */

/**
 * 根据状态值返回对应颜色。
 *
 * @param {"DONE"|"FAILED"|"RUNNING"|"ANALYZING"|"UPLOADING"|"PENDING"|"ONLINE"|"OFFLINE"|string} status
 * @returns {"green"|"red"|"blue"|"default"|"gray"}
 */
export function statusColor(status) {
  if (status === "DONE" || status === "ONLINE") return "green";
  if (status === "FAILED" || status === "OFFLINE") return "red";
  if (status === "CANCELLED") return "orange";
  if (
    status === "RUNNING" ||
    status === "ANALYZING" ||
    status === "UPLOADING"
  )
    return "blue";
  if (status === "PENDING") return "default";
  return "default";
}

/**
 * 任务运行中（非终态）的状态值集合。
 */
export const ACTIVE_TASK_STATUSES = new Set([
  "PENDING",
  "RUNNING",
  "UPLOADING",
  "ANALYZING",
]);

/**
 * 判断任务是否处于活跃状态（尚未进入终态）。
 */
export function isTaskActive(status) {
  return ACTIVE_TASK_STATUSES.has(status);
}
