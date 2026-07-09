import { Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";

import type { ChatMessage } from "../types";
import { CardRenderer } from "./CardRenderer";

export function ConversationPanel({ messages, personaName }: { messages: ChatMessage[]; personaName: string }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const lastCardId = [...messages].reverse().find((m) => m.card)?.id;

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
              <div className="msg-bubble">{m.text}</div>
              {m.card && <CardRenderer card={m.card} active={m.id === lastCardId} />}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
