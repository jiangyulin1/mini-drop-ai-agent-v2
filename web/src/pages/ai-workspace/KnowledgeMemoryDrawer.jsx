import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Button, Drawer, Empty, Input, List, Modal, Radio, Segmented, Space,
  Switch, Tabs, Tag, Tooltip, Typography, Upload, message,
} from "antd";
import {
  CloudUploadOutlined, DatabaseOutlined, DeleteOutlined, FileTextOutlined,
  ReloadOutlined, SearchOutlined, StarOutlined,
} from "@ant-design/icons";
import {
  createKnowledgeText, getCaseMemory, getKnowledgeChunk, getKnowledgeDocument,
  listKnowledgeDocuments, promoteCaseMemory, refreshCaseMemory, searchKnowledge,
  updateCaseMemory, updateKnowledgeDocument, uploadKnowledgeDocument,
} from "../../api/client";
import styles from "./KnowledgeMemoryDrawer.module.css";

const { Text, Paragraph, Title } = Typography;

export default function KnowledgeMemoryDrawer({ open, onClose, caseId, initialChunkId, onOpenEvidence }) {
  const [tab, setTab] = useState("memory");
  const [memory, setMemory] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [selected, setSelected] = useState(null);
  const [results, setResults] = useState([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");
  const [noteScope, setNoteScope] = useState("CASE");

  const load = useCallback(async () => {
    if (!caseId) return;
    setLoading(true);
    try {
      const [nextMemory, nextDocuments] = await Promise.all([
        getCaseMemory(caseId), listKnowledgeDocuments(caseId),
      ]);
      setMemory(nextMemory);
      setDocuments(nextDocuments);
    } catch (error) {
      message.error(error.message || "记忆与知识加载失败");
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => { if (open) void load(); }, [load, open]);
  useEffect(() => {
    if (!open || !initialChunkId || !caseId) return;
    setTab("knowledge");
    void getKnowledgeChunk(initialChunkId, caseId)
      .then(setSelected)
      .catch((error) => message.error(error.message || "知识片段无法打开"));
  }, [caseId, initialChunkId, open]);

  const activeDocuments = useMemo(
    () => documents.filter((item) => item.status === "ACTIVE"), [documents],
  );

  async function refreshMemory() {
    setLoading(true);
    try { setMemory(await refreshCaseMemory(caseId)); message.success("会话复盘已更新"); }
    catch (error) { message.error(error.message); }
    finally { setLoading(false); }
  }

  async function toggleMemory(value) {
    try { setMemory(await updateCaseMemory(caseId, { auto_capture: value })); }
    catch (error) { message.error(error.message); }
  }

  async function promote() {
    setLoading(true);
    try { await promoteCaseMemory(caseId); await load(); message.success("已沉淀为团队知识"); }
    catch (error) { message.error(error.message); }
    finally { setLoading(false); }
  }

  async function addNote() {
    setLoading(true);
    try {
      await createKnowledgeText({
        title: noteTitle, content: noteContent, scope: noteScope,
        case_id: noteScope === "CASE" ? caseId : null,
      });
      setNoteOpen(false); setNoteTitle(""); setNoteContent(""); await load();
      message.success("知识笔记已完成分块与向量索引");
    } catch (error) { message.error(error.message); }
    finally { setLoading(false); }
  }

  async function uploadFile(file) {
    setLoading(true);
    try {
      await uploadKnowledgeDocument(file, { caseId, scope: noteScope });
      await load(); message.success("文档已完成解析、分块与向量索引");
    } catch (error) { message.error(error.message); }
    finally { setLoading(false); }
    return false;
  }

  async function openDocument(documentId) {
    try { setSelected(await getKnowledgeDocument(documentId)); }
    catch (error) { message.error(error.message); }
  }

  async function archive(documentId) {
    try { await updateKnowledgeDocument(documentId, { status: "ARCHIVED" }); await load(); }
    catch (error) { message.error(error.message); }
  }

  async function runSearch() {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const response = await searchKnowledge({ case_id: caseId, query: query.trim(), limit: 8 });
      setResults(response.items || []);
    } catch (error) { message.error(error.message); }
    finally { setLoading(false); }
  }

  return <>
    <Drawer
      title="记忆与知识" open={open} onClose={onClose} width={680}
      className={styles.drawer} destroyOnClose={false}
      extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={load} aria-label="刷新记忆与知识" />}
    >
      <Tabs activeKey={tab} onChange={setTab} items={[
        { key: "memory", label: "会话记忆", children: memory ? <div className={styles.stack}>
          <Alert type="info" showIcon message="复盘记忆通过 RAG 按需召回，不会固定加入每轮上下文。" />
          <section className={styles.section}>
            <div className={styles.sectionHead}><div><Title level={5}>自动复盘</Title><Text type="secondary">跟随当前会话持续更新</Text></div><Switch checked={Boolean(memory.auto_capture)} onChange={toggleMemory} /></div>
            <Paragraph className={styles.memoryText}>{memory.summary_text || "当前还没有可复盘内容。"}</Paragraph>
            <Space wrap><Button icon={<ReloadOutlined />} onClick={refreshMemory}>更新复盘</Button><Button type="primary" ghost icon={<StarOutlined />} onClick={promote}>沉淀为团队知识</Button></Space>
          </section>
          {(memory.evidence_refs || []).length > 0 && <section className={styles.section}><Title level={5}>关联 Evidence</Title><div className={styles.chips}>{memory.evidence_refs.map((id) => <Button size="small" key={id} onClick={() => onOpenEvidence?.(id)}>{id}</Button>)}</div></section>}
        </div> : <Empty description="暂无会话记忆" /> },
        { key: "knowledge", label: `知识库 ${activeDocuments.length}`, children: <div className={styles.stack}>
          <Alert type="info" showIcon message="文档会解析、分块并建立向量索引；检索结果是历史知识，不是本次故障 Evidence。" />
          <div className={styles.actionBar}>
            <Radio.Group value={noteScope} onChange={(event) => setNoteScope(event.target.value)} optionType="button" buttonStyle="solid" options={[{ value: "CASE", label: "当前会话" }, { value: "GLOBAL", label: "团队" }]} />
            <Upload beforeUpload={uploadFile} showUploadList={false} accept=".txt,.md,.markdown,.json,.yaml,.yml,.csv,.log,.xml,.docx"><Button icon={<CloudUploadOutlined />} loading={loading}>上传文档</Button></Upload>
            <Button icon={<FileTextOutlined />} onClick={() => setNoteOpen(true)}>添加笔记</Button>
          </div>
          <section className={styles.searchBand}>
            <Input.Search value={query} onChange={(event) => setQuery(event.target.value)} onSearch={runSearch} enterButton={<SearchOutlined />} placeholder="测试 RAG 检索，例如：序列化热点如何核验" loading={loading} />
            {results.length > 0 && <div className={styles.results}>{results.map((item) => <button type="button" key={item.chunk_id} onClick={() => setSelected(item)}><strong>{item.title}</strong><span>{item.content}</span><small>{item.source === "case_memory" ? "会话复盘" : item.scope === "GLOBAL" ? "团队知识" : "会话知识"} · chunk {item.chunk_index ?? 0}</small></button>)}</div>}
          </section>
          <List dataSource={documents} locale={{ emptyText: "还没有知识文档" }} renderItem={(item) => <List.Item actions={item.status === "ACTIVE" ? [<Tooltip title="归档后 Agent 将不再检索" key="archive"><Button type="text" icon={<DeleteOutlined />} onClick={() => archive(item.document_id)} aria-label={`归档 ${item.title}`} /></Tooltip>] : [<Tag key="archived">已归档</Tag>]}>
            <List.Item.Meta avatar={<DatabaseOutlined className={styles.docIcon} />} title={<button type="button" className={styles.titleButton} onClick={() => openDocument(item.document_id)}>{item.title}</button>} description={`${item.scope === "GLOBAL" ? "团队" : "当前会话"} · ${item.chunk_count || 0} 个片段 · ${item.index_status === "READY" ? "索引就绪" : "等待索引"}`} />
          </List.Item>} />
        </div> },
      ]} />
    </Drawer>

    <Drawer title={selected?.title || "知识片段"} open={Boolean(selected)} onClose={() => setSelected(null)} width={560}>
      {selected && <div className={styles.stack}>
        <Space wrap><Tag color="blue">{selected.scope === "GLOBAL" ? "团队知识" : selected.source === "case_memory" ? "会话复盘" : "会话知识"}</Tag><Tag>{selected.chunk_id || `${selected.chunk_count || 0} 个片段`}</Tag></Space>
        <Alert type="warning" showIcon message="该内容用于补充调查背景，不能替代当前 Evidence。" />
        {selected.content ? <Paragraph className={styles.preview}>{selected.content}</Paragraph> : <Paragraph className={styles.preview}>{selected.content_text}</Paragraph>}
        {(selected.chunks || []).length > 0 && <div className={styles.chunkList}>{selected.chunks.map((chunk) => <button type="button" key={chunk.chunk_id} onClick={() => setSelected({ ...chunk, title: selected.title, scope: selected.scope })}><span>片段 {chunk.chunk_index + 1}</span><small>{chunk.start_offset}-{chunk.end_offset}</small><p>{chunk.content}</p></button>)}</div>}
      </div>}
    </Drawer>

    <Modal title="添加知识笔记" open={noteOpen} onCancel={() => setNoteOpen(false)} onOk={addNote} okText="建立索引" confirmLoading={loading} okButtonProps={{ disabled: !noteTitle.trim() || !noteContent.trim() }}>
      <Space direction="vertical" size={14} style={{ width: "100%" }}>
        <Segmented block value={noteScope} onChange={setNoteScope} options={[{ value: "CASE", label: "当前会话" }, { value: "GLOBAL", label: "团队知识" }]} />
        <Input value={noteTitle} onChange={(event) => setNoteTitle(event.target.value)} placeholder="知识标题" maxLength={256} />
        <Input.TextArea value={noteContent} onChange={(event) => setNoteContent(event.target.value)} placeholder="输入操作手册、排查经验或环境约束" rows={10} maxLength={200000} showCount />
      </Space>
    </Modal>
  </>;
}
