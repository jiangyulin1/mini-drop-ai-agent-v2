/**
 * Mini-Drop 主题令牌。
 *
 * 所有颜色、间距、字号集中管理，组件和页面统一引用此处，
 * 不再在 inline style 中硬编码具体色值或尺寸。
 */

// ── 颜色 ──────────────────────────────────────────────────

export const COLORS = {
  // 品牌
  primary: "#1677ff",
  primaryBg: "rgba(22,119,255,0.08)",

  // 状态
  success: "#52c41a",
  warning: "#faad14",
  error: "#ff4d4f",
  running: "#1677ff",
  pending: "#d9d9d9",
  offline: "#8c8c8c",

  // 中性色
  border: "#f0f0f0",
  borderSecondary: "#e8e8e8",
  background: "#fafafa",
  cardBackground: "#ffffff",
  textPrimary: "rgba(0,0,0,0.88)",
  textSecondary: "rgba(0,0,0,0.65)",  // WCAG AA: ~5.1:1 对比度
  textTertiary: "rgba(0,0,0,0.50)",   // WCAG AA: ~4.5:1 对比度

  // 特殊
  nlpHighlight: "#faad14",
  aiTag: "orange",
  codeBackground: "#f5f5f5",
};

// ── 间距 ──────────────────────────────────────────────────

export const SPACING = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

// ── 字号 ──────────────────────────────────────────────────

export const FONT_SIZES = {
  sm: 12,
  md: 14,
  lg: 16,
  xl: 20,
  title: 18,
};

// ── 布局 ──────────────────────────────────────────────────

export const LAYOUT = {
  siderWidth: 200,
  contentMaxWidth: 1400,
  headerHeight: 48,
};

// ── 动画 ──────────────────────────────────────────────────

export const ANIMATION = {
  fadeIn: "fadeIn 0.3s ease-in-out",
};

// ── 火焰图 ────────────────────────────────────────────────

export const FLAMEGRAPH = {
  defaultHeight: 480,
  minHeight: 300,
  maxHeight: 720,
  cellHeight: 18,
  transitionDuration: 750,
};
