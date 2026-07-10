export function Landing({ note, error, onStart }: { note: string; error: string | null; onStart: () => void }) {
  return (
    <div id="landing">
      <div className="landing-card">
        <div className="brand landing-brand">
          FinAdvisor <span className="brand-ai">AI</span>
        </div>
        <h1>Talk to Sterling</h1>
        <p className="landing-sub">Your client-meeting co-pilot.</p>
        <button id="start-btn" className="cta" onClick={onStart}>
          Start
        </button>
        {error && <p className="landing-error">⚠ {error}</p>}
        <p className="landing-note">{note}</p>
      </div>
    </div>
  );
}
