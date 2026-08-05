import { useEffect, useState } from "react";

/**
 * Mount the iframe before navigating it to srcDoc. Some embedded viewers
 * measure their canvas during parsing; two animation frames ensure the iframe
 * viewport has a real width before their scripts execute.
 */
export default function SandboxedArtifactFrame({ html, title, style }) {
  const [documentHtml, setDocumentHtml] = useState("");

  useEffect(() => {
    setDocumentHtml("");
    const timer = window.setTimeout(() => setDocumentHtml(html || ""), 50);
    return () => {
      window.clearTimeout(timer);
    };
  }, [html]);

  return (
    <iframe
      srcDoc={documentHtml}
      sandbox="allow-scripts allow-modals"
      referrerPolicy="no-referrer"
      title={title}
      style={style}
    />
  );
}
