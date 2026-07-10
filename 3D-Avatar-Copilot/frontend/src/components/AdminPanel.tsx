import { Database, MessagesSquare } from "lucide-react";
import { useState } from "react";

import { KnowledgeBase } from "./KnowledgeBase";
import { ScriptManager } from "./ScriptManager";

/** ?admin=1 — administration: knowledge base governance + Q&A content. */
export function AdminPanel() {
  const [tab, setTab] = useState<"knowledge" | "script">("knowledge");

  return (
    <main id="admin">
      <nav className="admin-tabs">
        <button className={tab === "knowledge" ? "tab active" : "tab"} onClick={() => setTab("knowledge")}>
          <Database size={15} /> Knowledge Base
        </button>
        <button className={tab === "script" ? "tab active" : "tab"} onClick={() => setTab("script")}>
          <MessagesSquare size={15} /> Q&amp;A Content
        </button>
      </nav>
      {tab === "knowledge" ? <KnowledgeBase /> : <ScriptManager />}
    </main>
  );
}
