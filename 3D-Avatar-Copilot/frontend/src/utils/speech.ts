/** Speak text with the browser's built-in TTS (used for the advisor's questions in
 *  autoplay — the avatar voice is reserved for answers). Resolves when speech ends. */
export function speakText(text: string): Promise<void> {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        resolve();
      }
    };
    // safety fallback if TTS is unavailable or events never fire (e.g. headless)
    setTimeout(finish, text.length * 90 + 3000);
    if (!("speechSynthesis" in window)) return;

    const utterance = new SpeechSynthesisUtterance(text);
    const voices = speechSynthesis.getVoices();
    // prefer a female English voice so the question sounds distinct from the avatar
    utterance.voice =
      voices.find((v) => v.lang.startsWith("en") && /aria|jenny|zira|sonia|libby|female/i.test(v.name)) ?? null;
    utterance.onend = finish;
    utterance.onerror = finish;
    speechSynthesis.speak(utterance);
  });
}
