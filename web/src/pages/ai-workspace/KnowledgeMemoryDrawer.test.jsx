import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeMemoryDrawer from "./KnowledgeMemoryDrawer";

const api = vi.hoisted(() => ({
  getCaseMemory: vi.fn(),
  listKnowledgeDocuments: vi.fn(),
  searchKnowledge: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  createKnowledgeText: vi.fn(),
  getCaseMemory: api.getCaseMemory,
  getKnowledgeChunk: vi.fn(),
  getKnowledgeDocument: vi.fn(),
  listKnowledgeDocuments: api.listKnowledgeDocuments,
  promoteCaseMemory: vi.fn(),
  refreshCaseMemory: vi.fn(),
  searchKnowledge: api.searchKnowledge,
  updateCaseMemory: vi.fn(),
  updateKnowledgeDocument: vi.fn(),
  uploadKnowledgeDocument: vi.fn(),
}));

describe("KnowledgeMemoryDrawer", () => {
  beforeEach(() => {
    api.getCaseMemory.mockResolvedValue({
      auto_capture: true,
      summary_text: "已确认 checkout CPU 现象，仍需核验 serializer 热点。",
      evidence_refs: ["ev-1"],
    });
    api.listKnowledgeDocuments.mockResolvedValue([{ document_id: "doc-1", title: "Checkout 手册", scope: "CASE", status: "ACTIVE", chunk_count: 3, index_status: "READY" }]);
    api.searchKnowledge.mockResolvedValue({ items: [{ chunk_id: "chunk-123456789abc", title: "Checkout 手册", content: "serializer 热点需结合 Profile 核验", scope: "CASE", chunk_index: 1 }] });
  });

  it("keeps retrospective memory out of fixed context and exposes real RAG controls", async () => {
    const onOpenEvidence = vi.fn();
    render(<KnowledgeMemoryDrawer open caseId="case-1" onClose={vi.fn()} onOpenEvidence={onOpenEvidence} />);
    expect(await screen.findByText("复盘记忆通过 RAG 按需召回，不会固定加入每轮上下文。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "ev-1" }));
    expect(onOpenEvidence).toHaveBeenCalledWith("ev-1");

    fireEvent.click(screen.getByRole("tab", { name: /知识库/ }));
    expect(await screen.findByText(/3 个片段 · 索引就绪/)).toBeInTheDocument();
    const input = screen.getByPlaceholderText("测试 RAG 检索，例如：序列化热点如何核验");
    fireEvent.change(input, { target: { value: "serializer" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await waitFor(() => expect(api.searchKnowledge).toHaveBeenCalledWith({ case_id: "case-1", query: "serializer", limit: 8 }));
    expect(await screen.findByText("serializer 热点需结合 Profile 核验")).toBeInTheDocument();
  });
});
