# Mini-Drop 前端图表审核记录（2026-08-04）

## 结论

已在三节点实验环境的真实 Edge 浏览器中完成图表渲染和控件操作验证。当前前端发布为
`/var/www/mini-drop-release-20260804-charts-v5`，未执行 Git commit 或 push。

## 已修复问题

1. Java async-profiler HTML iframe 原先没有脚本权限，图表及按钮脚本被浏览器阻止。
2. Nginx CSP 原先阻止 async-profiler 4.4 的两个内联查看器脚本；现仅放行两个精确 SHA-256，未启用全局 `unsafe-inline`。
3. async-profiler canvas 在 `srcDoc` 首次解析时宽度可能被锁定为 0；现先完成 iframe 布局，再加载嵌入文档，并稳定 canvas 的 viewport 宽度。
4. Java 火焰图原先在核心区域和专属卡片中重复展示；现保留单一查看器。
5. 仪表盘预览原先没有解析文本产物 API 的 `{text: ...}` 包装，HTML/SVG 预览可能为空。
6. 历史任务的产物元数据存在但对象存储文件已丢失时，前端原先仍请求内容并显示 404；现显示明确的“文件缺失”，禁用下载，不再请求缺失内容。

## 浏览器验证

| 图表 | 验证内容 | 结果 |
| --- | --- | --- |
| perf CPU 火焰图 | 33 个帧；搜索、点击缩放、重置、刷新；TopN canvas | 通过 |
| Java async-profiler | canvas 1272×48、有效图像数据；Invert、Search、Dark mode、Info | 通过 |
| eBPF I/O | ECharts canvas 渲染、hover | 通过 |
| 内存趋势 | ECharts canvas 渲染、hover | 通过 |
| 系统指标 | ECharts canvas 渲染、hover | 通过 |
| 持续采样 | 新建 65 秒任务，窗口 0/1 分别渲染 25/19 个帧；切换、搜索、重置、刷新 | 通过 |
| 历史持续采样 | 5 个缺失产物明确标记，5 个下载按钮禁用，0 次内容 404 | 通过 |
| 历史 pprof | 2 个缺失产物明确标记，2 个下载按钮禁用，0 次内容 404 | 通过 |

持续采样验证任务：`task_20260804_124623_4fdcd5`，状态 `DONE`，窗口索引 `[0, 1]`。
临时 CPU 负载在任务结束后已由测试脚本停止。

## 自动化校验

- 前端单元测试：11 个测试文件、29 个测试通过。
- ESLint：通过，0 warning。
- Vite 生产构建：通过。
- Edge 端到端审核：通过，无未预期 console error。

机器可读结果：`.pytest-work/ui-audit-fixed/report.json`。

## 升级注意事项

Nginx CSP 中的两个脚本哈希对应当前 async-profiler 4.4 查看器。升级 async-profiler 后应重新审核嵌入脚本哈希和四个内置按钮，不能直接放宽为全局 `unsafe-inline`。
