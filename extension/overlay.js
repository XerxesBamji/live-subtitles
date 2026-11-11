// Draws subtitles over the YouTube player (works in fullscreen too).
// Shows only the newest text, capped at two lines.
(() => {
  const VERSION = 5;
  if (window.__liveSubsVersion === VERSION) return;
  window.__liveSubsVersion = VERSION;
  // Remove any overlay left behind by an older version of the extension.
  document.querySelectorAll("#live-subs-overlay").forEach((el) => el.remove());

  const MAX_CHARS = 90; // roughly two lines; older words are dropped from the front

  const box = document.createElement("div");
  box.id = "live-subs-overlay";
  Object.assign(box.style, {
    left: "50%",
    bottom: "14%",
    transform: "translateX(-50%)",
    width: "max-content",
    maxWidth: "70%",
    padding: "4px 12px",
    borderRadius: "4px",
    background: "rgba(0,0,0,0.7)",
    color: "#fff",
    font: "500 clamp(13px, 1.5vw, 24px)/1.35 -apple-system, Helvetica, Arial, sans-serif",
    textAlign: "center",
    zIndex: "2147483647",
    pointerEvents: "none",
  });
  // Hard cap at 2 lines no matter what.
  box.style.setProperty("overflow", "hidden");
  box.style.setProperty("display", "-webkit-box");
  box.style.setProperty("-webkit-box-orient", "vertical");
  box.style.setProperty("-webkit-line-clamp", "2");
  box.style.visibility = "hidden";

  // One colour per speaker (white, yellow, cyan, green, pink, orange).
  const COLORS = ["#ffffff", "#ffe066", "#7fdbff", "#9cff9c", "#ffadd6", "#ffb366"];

  let finalText = "";
  let finalSpeaker = null;
  let prevSpeaker = null;     // speaker of the line before the current one
  let partialText = "";
  let partialSpeaker = null;
  let hideTimer = null;

  const colorFor = (spk) => (spk == null ? COLORS[0] : COLORS[spk % COLORS.length]);

  function mount() {
    const host =
      document.fullscreenElement || document.querySelector("#movie_player") || document.body;
    if (box.parentElement !== host) host.appendChild(box);
    box.style.position = host === document.body ? "fixed" : "absolute";
  }
  document.addEventListener("fullscreenchange", mount);
  setInterval(mount, 1000);

  function tail(text) {
    if (text.length <= MAX_CHARS) return text;
    const cut = text.slice(-MAX_CHARS);
    const sp = cut.indexOf(" ");
    return "…" + (sp >= 0 ? cut.slice(sp + 1) : cut);
  }

  function render(resetTimer = true) {
    const isPartial = !!partialText;
    let text = tail(isPartial ? partialText : finalText);
    const spk = isPartial ? partialSpeaker : finalSpeaker;
    // Subtitle convention: a dash marks a change of speaker.
    if (text && !isPartial && spk != null && prevSpeaker != null && spk !== prevSpeaker) {
      text = "– " + text;
    }
    box.textContent = text;
    box.style.color = colorFor(spk);
    box.style.opacity = isPartial ? "0.85" : "1";
    box.style.visibility = text ? "visible" : "hidden";
    if (!resetTimer) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      finalText = partialText = "";
      prevSpeaker = null;
      box.style.visibility = "hidden";
    }, 4000);
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type !== "caption") return;
    mount();
    switch (msg.kind) {
      case "partial":
        partialText = msg.text;
        partialSpeaker = msg.speaker ?? finalSpeaker;
        break;
      case "status":
        partialText = msg.text;
        partialSpeaker = null;
        break;
      case "final":
        partialText = "";
        if (!msg.text) {
          // Nothing new was said (laughter/noise): don't bring an old line back.
          render(false);
          return;
        }
        prevSpeaker = finalText ? finalSpeaker : null;
        finalText = msg.text;
        finalSpeaker = msg.speaker ?? finalSpeaker;
        break;
      case "stopped":
        finalText = partialText = "";
        finalSpeaker = prevSpeaker = partialSpeaker = null;
        break;
    }
    render();
  });
})();
