const VALUES = [
  { icon: "⚡", title: "Faster preparation", text: "Client meeting prep in minutes, not hours." },
  { icon: "📚", title: "Grounded answers", text: "RAG-based responses from approved knowledge only." },
  { icon: "🎙️", title: "Natural interaction", text: "Voice conversation with a lifelike 3D avatar." },
  { icon: "🧑‍💼", title: "Advisor in control", text: "Final recommendations always confirmed by the qualified advisor." },
];

export function ClosingScreen() {
  return (
    <div id="closing">
      <h2>
        FinAdvisor <span className="brand-ai">AI</span> — Advisor Copilot
      </h2>
      <div className="value-grid">
        {VALUES.map((v) => (
          <div className="value-tile" key={v.title}>
            {v.icon}
            <h3>{v.title}</h3>
            <p>{v.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
