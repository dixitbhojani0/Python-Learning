import { Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";

import { config } from "../config";
import type { ChatMessage, Status } from "../types";
import { CardRenderer } from "./CardRenderer";
import { TypeText } from "./TypeText";

interface ConversationPanelProps {
  messages: ChatMessage[];
  personaName: string;
  status: Status;
}

export function ConversationPanel({ messages, personaName, status }: ConversationPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const scrollToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, []);

  const lastCardId = [...messages].reverse().find((m) => m.card)?.id;
  const lastId = messages[messages.length - 1]?.id;
  // reply pending: the advisor has spoken and the answer hasn't arrived yet
  const waiting = messages[messages.length - 1]?.role === "you" && status !== "idle";

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, waiting]);

  return (
    <aside id="conversation">
      <header className="conv-header">
        <Sparkles className="icon" size={16} />
        Conversation
      </header>
      <div className="conv-scroll" ref={scrollRef}>
        {messages.map((m) => {
          if (m.role === "system") {
            return (
              <div className="msg-system" key={m.id}>
                {m.text}
              </div>
            );
          }
          const you = m.role === "you";
          return (
            <div className={you ? "msg msg-you" : "msg msg-assistant"} key={m.id}>
              <div className="msg-meta">
                <span className="msg-name">{you ? "You" : personaName}</span>
                <span className="msg-time">{m.time}</span>
              </div>
              <div className="msg-bubble">
                {m.id === lastId ? <TypeText text={m.text} onTick={scrollToBottom} /> : m.text}
              </div>
              {config.showCards && m.card && <CardRenderer card={m.card} active={m.id === lastCardId} />}
            </div>
          );
        })}
        {waiting && (
          <div className="msg msg-assistant">
            <div className="msg-meta">
              <span className="msg-name">{personaName}</span>
              {status === "thinking" && <span className="msg-time">searching knowledge base…</span>}
            </div>
            <div className="msg-bubble typing" aria-label={`${personaName} is preparing a reply`}>
              <i />
              <i />
              <i />
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
