import { useEffect, useState } from "react";

const CHARS_PER_TICK = 2;
const TICK_MS = 35;

/** ChatGPT-style typewriter for the newest chat message. */
export function TypeText({ text, onTick }: { text: string; onTick?: () => void }) {
  const [shown, setShown] = useState(0);

  useEffect(() => {
    setShown(0);
    const interval = setInterval(() => {
      setShown((prev) => {
        if (prev >= text.length) {
          clearInterval(interval);
          return prev;
        }
        onTick?.();
        return prev + CHARS_PER_TICK;
      });
    }, TICK_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only when the text changes
  }, [text]);

  return <>{text.slice(0, shown)}</>;
}
