import { Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { fetchAllScenes, saveScript } from "../api/client";
import type { Scene } from "../types";

const BLANK: Scene = {
  id: 0,
  enabled: true,
  topic: "New question",
  question: "",
  answer: "",
  keywords: [],
};

/** Manage the copilot's question/answer content (cards are edited in backend/scenes.json). */
export function ScriptManager() {
  const [scenes, setScenes] = useState<Scene[] | null>(null);
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchAllScenes()
      .then(setScenes)
      .catch((e) => setBanner({ kind: "error", text: `Couldn't load the content: ${e.message}` }));
  }, []);

  const update = (index: number, patch: Partial<Scene>) =>
    setScenes((prev) => prev!.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  const addScene = () =>
    setScenes((prev) => [...(prev ?? []), { ...BLANK, id: Math.max(0, ...(prev ?? []).map((s) => s.id)) + 1 }]);

  const removeScene = (index: number) => setScenes((prev) => prev!.filter((_, i) => i !== index));

  const save = async () => {
    if (!scenes) return;
    setSaving(true);
    setBanner(null);
    try {
      await saveScript(scenes);
      setBanner({ kind: "ok", text: "Saved." });
    } catch (e) {
      setBanner({ kind: "error", text: `Not saved: ${e instanceof Error ? e.message : e}` });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section>
      <div className="admin-head">
        <div>
          <h2>Q&amp;A Content</h2>
          <p className="admin-sub">The questions the copilot recognises and the answers it gives.</p>
        </div>
        <div className="admin-actions">
          <button className="ghost-btn" onClick={addScene}>
            <Plus size={15} /> Add question
          </button>
          <button className="cta admin-save" onClick={() => void save()} disabled={saving || !scenes}>
            <Save size={15} /> {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      {banner && <div className={banner.kind === "ok" ? "admin-banner ok" : "admin-banner error"}>{banner.text}</div>}

      <div className="admin-list">
        {scenes?.map((s, i) => (
          <div className={s.enabled === false ? "card admin-row disabled" : "card admin-row"} key={i}>
            <div className="admin-row-top">
              <input
                className="admin-topic"
                value={s.topic}
                onChange={(e) => update(i, { topic: e.target.value })}
                placeholder="Topic"
              />
              <label className="admin-toggle">
                <input
                  type="checkbox"
                  checked={s.enabled !== false}
                  onChange={(e) => update(i, { enabled: e.target.checked })}
                />
                Enabled
              </label>
              <button className="ghost-btn danger" title="Delete this question" onClick={() => removeScene(i)}>
                <Trash2 size={14} />
              </button>
            </div>
            <label className="admin-label">Question</label>
            <textarea rows={2} value={s.question} onChange={(e) => update(i, { question: e.target.value })} />
            <label className="admin-label">Answer</label>
            <textarea rows={3} value={s.answer} onChange={(e) => update(i, { answer: e.target.value })} />
            <label className="admin-label">Recognition keywords (comma-separated)</label>
            <input
              value={s.keywords.join(", ")}
              onChange={(e) =>
                update(i, { keywords: e.target.value.split(",").map((k) => k.trim().toLowerCase()).filter(Boolean) })
              }
            />
          </div>
        ))}
        {!scenes && !banner && <p className="admin-sub">Loading…</p>}
      </div>
    </section>
  );
}
