import type { CardData } from "../types";

/** Renders structured card JSON from the script API — no raw HTML ever. */
export function CardRenderer({ card, active }: { card: CardData; active: boolean }) {
  return (
    <div className={active ? "card active" : "card"}>
      <h3>
        {card.title}
        {card.badge && <span className={card.badgeTone === "amber" ? "badge amber" : "badge"}>{card.badge}</span>}
      </h3>
      {card.keyValues?.map((kv) => (
        <div className="kv" key={kv.label}>
          <span>{kv.label}</span>
          <span>{kv.value}</span>
        </div>
      ))}
      {card.bullets && (
        <ul>
          {card.bullets.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      )}
      {card.chips && (
        <div className="chips">
          {card.chips.map((c) => (
            <span className="chip" key={c}>
              {c}
            </span>
          ))}
        </div>
      )}
      {card.note && <p className="note-draft">{card.note}</p>}
    </div>
  );
}
