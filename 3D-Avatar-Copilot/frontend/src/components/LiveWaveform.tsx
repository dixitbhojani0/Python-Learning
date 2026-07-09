import { useEffect, useRef, useState, type RefObject } from "react";

const BAR_COUNT = 24;

/**
 * Waveform bars. When the avatar's WebRTC audio stream is available, bar heights
 * follow the real voice amplitude (Web Audio AnalyserNode). Without a stream
 * (dry mode), the CSS keyframe animation in global.css takes over as fallback.
 */
export function LiveWaveform({ videoRef, live }: { videoRef: RefObject<HTMLVideoElement | null>; live: boolean }) {
  const barsRef = useRef<HTMLDivElement | null>(null);
  const [analysing, setAnalysing] = useState(false);

  useEffect(() => {
    if (!live) return;

    let ctx: AudioContext | undefined;
    let raf = 0;
    let retryTimer: number | undefined;
    let cancelled = false;

    const attach = () => {
      if (cancelled) return;
      const stream = videoRef.current?.srcObject;
      if (!(stream instanceof MediaStream) || stream.getAudioTracks().length === 0) {
        retryTimer = window.setTimeout(attach, 500); // stream appears shortly after connect
        return;
      }
      ctx = new AudioContext();
      void ctx.resume();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.75;
      ctx.createMediaStreamSource(stream).connect(analyser); // not routed to output — playback stays on the video element
      const data = new Uint8Array(analyser.frequencyBinCount);
      setAnalysing(true);

      const tick = () => {
        analyser.getByteFrequencyData(data);
        const bars = barsRef.current?.children;
        if (bars) {
          for (let i = 0; i < bars.length; i++) {
            const v = data[Math.floor((i * data.length) / bars.length)] / 255;
            (bars[i] as HTMLElement).style.height = `${4 + v * 18}px`;
          }
        }
        raf = requestAnimationFrame(tick);
      };
      tick();
    };

    attach();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      cancelAnimationFrame(raf);
      void ctx?.close();
      setAnalysing(false);
    };
  }, [live, videoRef]);

  return (
    <div className={analysing ? "waveform live" : "waveform"} aria-hidden="true" ref={barsRef}>
      {Array.from({ length: BAR_COUNT }, (_, i) => (
        <i key={i} />
      ))}
    </div>
  );
}
