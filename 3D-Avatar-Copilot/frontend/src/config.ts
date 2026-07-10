/** URL-driven demo modes. The page's behavior is controlled entirely by query params. */
const params = new URLSearchParams(location.search);

export const config = {
  /** provider=dry|anam; ?dry=1 kept as alias. Default: whatever the backend session says. */
  forcedProvider: params.has("dry") ? "dry" : params.get("provider"),
  /** ?autoplay=1 runs the script scene-by-scene; ?autoplay=3000 sets the gap in ms. */
  autoplay: params.has("autoplay"),
  autoplayGapMs: Number(params.get("autoplay")) || 1000,
  /** ?debug=1 shows the scene-jump counter (hidden for clean recordings). */
  debug: params.has("debug"),
  /** ?auto=N smoke-test hook: clicks Start; plays scene N when not autoplaying. */
  auto: params.get("auto"),
  /** ?admin=1 opens the question/answer management panel instead of the demo. */
  admin: params.has("admin"),
  /** End-of-demo value screen (brief step 6). */
  showClosing: true,
  /** Info cards inline in the conversation (brief steps 2/3/4/5). */
  showCards: true,
};
