import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./AssistantMessageContent.module.css";

const REMARK_PLUGINS = [remarkGfm];

function safeUrl(value) {
  const url = String(value || "").trim();
  if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) return url;
  return "";
}

function MarkdownLink({ href, children, title }) {
  const safeHref = safeUrl(href);
  if (!safeHref) return <span className={styles.unavailableLink}>{children}</span>;
  const external = /^https?:\/\//i.test(safeHref);
  return (
    <a
      href={safeHref}
      title={title}
      target={external ? "_blank" : undefined}
      rel={external ? "noreferrer noopener" : undefined}
    >
      {children}
    </a>
  );
}

function MarkdownImage({ alt }) {
  const label = `图片：${alt || "查看原图"}`;
  return <span className={styles.imageReference}>{label}</span>;
}

function MarkdownTable({ children }) {
  return (
    <div className={styles.tableViewport} role="region" aria-label="AI 回复表格" tabIndex={0}>
      <table>{children}</table>
    </div>
  );
}

function MarkdownHeading2({ children }) { return <h2>{children}</h2>; }
function MarkdownHeading3({ children }) { return <h3>{children}</h3>; }
function MarkdownHeading4({ children }) { return <h4>{children}</h4>; }
function MarkdownHeading5({ children }) { return <h5>{children}</h5>; }
function MarkdownHeading6({ children }) { return <h6>{children}</h6>; }

const COMPONENTS = {
  a: MarkdownLink,
  h1: MarkdownHeading2,
  h2: MarkdownHeading3,
  h3: MarkdownHeading4,
  h4: MarkdownHeading5,
  h5: MarkdownHeading6,
  h6: MarkdownHeading6,
  img: MarkdownImage,
  table: MarkdownTable,
};

export default function AssistantMessageContent({ content }) {
  const source = String(content || "");
  if (!source.trim()) return null;
  return (
    <div className={styles.markdown} data-testid="assistant-markdown">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        components={COMPONENTS}
        skipHtml
        urlTransform={safeUrl}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
