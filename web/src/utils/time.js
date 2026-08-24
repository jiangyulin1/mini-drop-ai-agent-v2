/**
 * UI 时间统一按中国标准时间展示。
 * API 仍传输 ISO-8601 UTC；只有展示和 datetime-local 输入在这里转换。
 */
export const DISPLAY_TIME_ZONE = "Asia/Shanghai";

const DATE_PARTS = {
  timeZone: DISPLAY_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
};

export function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatBeijingTime(value, { withDate = false, withSeconds = false } = {}) {
  const date = parseDate(value);
  if (!date) return value ? String(value) : "-";
  return date.toLocaleString("zh-CN", {
    ...DATE_PARTS,
    ...(withDate ? { year: "numeric", month: "2-digit", day: "2-digit" } : {}),
    ...(withSeconds ? { second: "2-digit" } : {}),
  });
}

export function formatBeijingDateTime(value) {
  return formatBeijingTime(value, { withDate: true });
}

export function relativeBeijingTime(value, now = Date.now()) {
  const date = parseDate(value);
  if (!date) return "-";
  const seconds = Math.max(0, Math.floor((now - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

/** datetime-local 没有时区，产品约定它表示北京时间。 */
export function beijingInputToIso(value) {
  if (!value) return "";
  const normalized = String(value).trim();
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(normalized)) return "";
  return new Date(`${normalized.length === 16 ? `${normalized}:00` : normalized}+08:00`).toISOString();
}
