import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AssistantMessageContent from "./AssistantMessageContent";

describe("AssistantMessageContent", () => {
  it("turns common model Markdown into compact semantic content", () => {
    render(<AssistantMessageContent content={`# 调查结论

**CPU 热点**集中在目标进程。

- 检查调用栈
- 对比基线

\`进程 42\`

\`\`\`bash
top -H -p 42
\`\`\`

| 指标 | 当前值 |
| --- | ---: |
| CPU | 92% |
`} />);

    expect(screen.getByRole("heading", { name: "调查结论", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("CPU 热点").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("进程 42").tagName).toBe("CODE");
    expect(screen.getByText("top -H -p 42").closest("pre")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "AI 回复表格" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "92%" })).toBeInTheDocument();
    expect(screen.queryByText("**CPU 热点**")).not.toBeInTheDocument();
  });

  it("does not execute raw HTML, dangerous links or remote images", () => {
    const { container } = render(<AssistantMessageContent content={`
<script>window.__unsafe = true</script>

<img src="x" onerror="window.__unsafe = true">

[危险操作](javascript:alert(1))

[运行手册](https://example.com/runbook)

![监控截图](https://example.com/chart.png)

[![拓扑图](https://example.com/topology.png)](https://example.com/topology)
`} />);

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByRole("link", { name: "危险操作" })).not.toBeInTheDocument();
    expect(screen.getByText("危险操作")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "运行手册" })).toHaveAttribute("href", "https://example.com/runbook");
    expect(screen.getByRole("link", { name: "运行手册" })).toHaveAttribute("target", "_blank");
    expect(screen.getByText("图片：监控截图")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "图片：监控截图" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "图片：拓扑图" })).toHaveAttribute("href", "https://example.com/topology");
    expect(container.querySelector("a a")).toBeNull();
  });

  it("does not add an empty message container", () => {
    const { container } = render(<AssistantMessageContent content="   " />);
    expect(container).toBeEmptyDOMElement();
  });
});
