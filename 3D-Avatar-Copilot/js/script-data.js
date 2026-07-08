// The 6 scripted scenes, verbatim from the demo brief.
// ponytail: hardcoded script — a real product would route the advisor's question
// to an LLM + RAG pipeline here and stream the grounded answer to talk().

const ALL_SCENES = [
  {
    label: "Greeting",
    advisorLine: "Help me prepare for my meeting with John Davies.",
    triggers: ["john", "davies", "prepare", "meeting"],
    spokenText:
      "Good morning. I found John Davies' annual review meeting scheduled for tomorrow. " +
      "I can help you review client context, identify key planning areas, and prepare follow-up notes.",
    cardHTML: `
      <h3>📅 Meeting Found <span class="badge">Tomorrow</span></h3>
      <div class="kv"><span>Client</span><span>John Davies</span></div>
      <div class="kv"><span>Type</span><span>Annual financial planning review</span></div>
      <div class="kv"><span>Prepared by</span><span>FinAdvisor AI Copilot</span></div>`,
  },
  {
    label: "Client summary",
    advisorLine: "Give me a quick overview.",
    triggers: ["overview", "summary", "quick", "snapshot"],
    spokenText:
      "John Davies is 58 and planning to retire at 62. His key review areas are pension contributions, " +
      "retirement income readiness, ISA utilisation, investment risk alignment, and inheritance planning.",
    cardHTML: `
      <h3>👤 Client Snapshot</h3>
      <div class="kv"><span>Age</span><span>58</span></div>
      <div class="kv"><span>Retirement target</span><span>62</span></div>
      <div class="kv"><span>Review type</span><span>Annual financial planning review</span></div>
      <ul>
        <li>Pension contributions</li>
        <li>ISA utilisation</li>
        <li>Retirement income readiness</li>
        <li>Inheritance (IHT) planning</li>
      </ul>`,
  },
  {
    label: "Knowledge retrieval",
    advisorLine: "What should I consider before discussing pension contributions and retirement income?",
    triggers: ["pension", "contribution", "consider", "income", "discussing"],
    searching: true, // show "Searching knowledge base…" beat before answering
    spokenText:
      "Based on approved planning guidance, consider current pension contribution levels, annual allowance position, " +
      "retirement income target, tax position, and portfolio risk alignment. Also confirm whether there have been any " +
      "changes in employment income, family circumstances, or retirement timeline.",
    cardHTML: `
      <h3>📚 Retrieved from Approved Knowledge Base <span class="badge">Grounded</span></h3>
      <div class="chips">
        <span class="chip">Retirement Planning Guidance</span>
        <span class="chip">Pension Contribution Checklist</span>
        <span class="chip">Annual Review Framework</span>
        <span class="chip">Suitability Review Notes</span>
      </div>`,
  },
  {
    label: "Compliance",
    advisorLine: "Are there any compliance points I should keep in mind?",
    triggers: ["compliance", "keep in mind", "regulation", "points"],
    spokenText:
      "Yes. Please confirm updated client objectives, risk profile, capacity for loss, tax position, and retirement timeline. " +
      "I can support meeting preparation — but the final recommendation must be reviewed and confirmed by you, the qualified advisor.",
    cardHTML: `
      <h3>⚖️ Compliance Checklist <span class="badge amber">Advisor confirms</span></h3>
      <ul>
        <li>Updated client objectives</li>
        <li>Risk profile &amp; capacity for loss</li>
        <li>Tax position</li>
        <li>Retirement timeline</li>
        <li>Final recommendation reviewed by the qualified advisor</li>
      </ul>`,
  },
  {
    label: "Follow-up note",
    advisorLine: "Draft a short follow-up note I can refine after the meeting.",
    triggers: ["draft", "note", "follow", "refine"],
    spokenText:
      "Of course. I have prepared a draft follow-up note for your review.",
    cardHTML: `
      <h3>✉️ Draft Follow-up Note <span class="badge">Editable</span></h3>
      <p class="note-draft">
        Hi John, thank you for meeting today. We reviewed your retirement timeline, pension position,
        investment portfolio, and key planning considerations. The next step is to validate your updated
        income assumptions and review the suitability of your current contribution and investment strategy.
        I will prepare the required documentation and follow up with next steps shortly.
      </p>`,
  },
  {
    label: "Closing",
    advisorLine: "Thanks, that's all for now.",
    triggers: ["thank", "thanks", "done", "that's all", "finish"],
    spokenText:
      "You're welcome. Good luck with the meeting tomorrow — I'll be here when you need me.",
    cardHTML: null, // scene 6 shows the full-screen value overlay instead
  },
];

// Demo trimmed to the first 2 scenes for now — raise this (up to 6) to re-enable the rest.
const ACTIVE_SCENES = 2;
export const SCENES = ALL_SCENES.slice(0, ACTIVE_SCENES);
