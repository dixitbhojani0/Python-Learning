/** URL-driven demo modes. The page's behavior is controlled entirely by query params. */
const params = new URLSearchParams(location.search);

export const config = {
  /** provider=dry|anam; ?dry=1 kept as alias. Default: whatever the backend session says. */
  forcedProvider: params.has("dry") ? "dry" : params.get("provider"),
  /** ?autoplay=1 runs the script scene-by-scene; ?autoplay=3000 sets the gap in ms. */
  autoplay: params.has("autoplay"),
  autoplayGapMs: Number(params.get("autoplay")) || 2000,
  /** ?debug=1 shows the scene-jump counter (hidden for clean recordings). */
  debug: params.has("debug"),
  /** ?auto=N smoke-test hook: clicks Start; plays scene N when not autoplaying. */
  auto: params.get("auto"),
  /** Re-enable the end-of-demo value screen when needed. */
  showClosing: false,
  /** Show info cards inline in the conversation. Off for now — question/answer only. */
  showCards: false,
};
