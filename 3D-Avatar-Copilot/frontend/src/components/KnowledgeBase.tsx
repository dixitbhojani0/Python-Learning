import { CircleCheck, Cpu, Database, FileText, Layers, RefreshCw, Search, Trash2, UploadCloud } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { deleteDocument, fetchKnowledge, reindexDocument, uploadDocument } from "../api/client";
import type { KnowledgeInfo } from "../types";

const formatSize = (kb: number) => (kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`);
const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

/** Approved knowledge base registry: documents, sources, embedding status. */
export function KnowledgeBase() {
  const [info, setInfo] = useState<KnowledgeInfo | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pollTimer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchKnowledge();
      setInfo(data);
      setError(null);
      // while anything is still processing, keep the view live
      clearTimeout(pollTimer.current);
      if (data.documents.some((d) => d.status === "processing")) {
        pollTimer.current = window.setTimeout(() => void refresh(), 4000);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => clearTimeout(pollTimer.current);
  }, [refresh]);

  const onUpload = async (file: File | undefined) => {
    if (!file) return;
    try {
      await uploadDocument(file.name, Math.round(file.size / 1024));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const docs = info?.documents.filter(
    (d) =>
      d.name.toLowerCase().includes(query.toLowerCase()) || d.category.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <section>
      <div className="admin-head">
        <div>
          <h2>Knowledge Base</h2>
          <p className="admin-sub">Approved documents grounding the copilot's answers.</p>
        </div>
        <div className="admin-actions">
          <input
            ref={fileInputRef}
            type="file"
            hidden
            onChange={(e) => {
              void onUpload(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <button className="cta admin-save" onClick={() => fileInputRef.current?.click()}>
            <UploadCloud size={15} /> Upload document
          </button>
        </div>
      </div>

      {error && <div className="admin-banner error">{error}</div>}

      <div className="kb-stats">
        <div className="kb-tile">
          <Database className="icon" size={18} />
          <div>
            <div className="kb-tile-value">{info?.documents.length ?? "—"}</div>
            <div className="kb-tile-label">Documents</div>
          </div>
        </div>
        <div className="kb-tile">
          <Layers className="icon" size={18} />
          <div>
            <div className="kb-tile-value">{info?.totalChunks ?? "—"}</div>
            <div className="kb-tile-label">Chunks indexed</div>
          </div>
        </div>
        <div className="kb-tile">
          <Cpu className="icon" size={18} />
          <div>
            <div className="kb-tile-value">{info?.embeddingModel ?? "—"}</div>
            <div className="kb-tile-label">Embedding model</div>
          </div>
        </div>
        <div className="kb-tile">
          <CircleCheck className="icon" size={18} />
          <div>
            <div className="kb-tile-value">Healthy</div>
            <div className="kb-tile-label">Vector index</div>
          </div>
        </div>
      </div>

      <div className="kb-toolbar">
        <div className="kb-search">
          <Search size={15} />
          <input placeholder="Search documents or categories…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </div>

      <div className="card kb-table-card">
        <table className="kb-table">
          <thead>
            <tr>
              <th>Document</th>
              <th>Category</th>
              <th>Chunks</th>
              <th>Size</th>
              <th>Updated</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {docs?.map((d) => (
              <tr key={d.id}>
                <td className="kb-name">
                  <FileText className="icon" size={15} />
                  {d.name}
                </td>
                <td>{d.category}</td>
                <td>{d.chunks}</td>
                <td>{formatSize(d.sizeKb)}</td>
                <td>{formatDate(d.indexedAt)}</td>
                <td>
                  {d.status === "processing" ? (
                    <span className="badge amber kb-processing">Processing…</span>
                  ) : (
                    <span className="badge success">Indexed</span>
                  )}
                </td>
                <td className="kb-actions">
                  <button
                    className="ghost-btn"
                    title="Re-index document"
                    onClick={() => void reindexDocument(d.id).then(refresh)}
                  >
                    <RefreshCw size={13} />
                  </button>
                  <button
                    className="ghost-btn danger"
                    title="Remove from knowledge base"
                    onClick={() => void deleteDocument(d.id).then(refresh)}
                  >
                    <Trash2 size={13} />
                  </button>
                </td>
              </tr>
            ))}
            {docs?.length === 0 && (
              <tr>
                <td colSpan={7} className="kb-empty">
                  No documents match your search.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
