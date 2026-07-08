import { BookOpenCheck, CalendarDays, FileText, Mail, Scale, UserRound, type LucideIcon } from "lucide-react";

import type { CardData } from "../types";

/** Backend sends icon *names* (keeps the API pure JSON); the frontend owns the rendering. */
const ICONS: Record<string, LucideIcon> = {
  calendar: CalendarDays,
  user: UserRound,
  book: BookOpenCheck,
  scale: Scale,
  mail: Mail,
};

/** Renders structured card JSON from the script API — no raw HTML ever. */
export function CardRenderer({ card, active }: { card: CardData; active: boolean }) {
  const Icon = (card.icon && ICONS[card.icon]) || FileText;
  return (
    <div className={active ? "card active" : "card"}>
      <h3>
        <Icon className="icon" size={17} />
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
