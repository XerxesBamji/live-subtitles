// Live English subtitles overlay.
//
// Two sources, chosen automatically:
//   1. On-screen subtitles (Disney+, Netflix, Prime, Max, YouTube CC, native <track>s):
//      the page's own Spanish subtitle text is translated instantly and shown in
//      place of the original, in sync with it. Audio listening is paused meanwhile.
//   2. Voice (fallback): if the page has no subtitles, captions come from the
//      Whisper translation of the tab's audio, coloured by speaker.
(() => {
  const VERSION = 6;
  if (window.__liveSubsVersion === VERSION) return;
  window.__liveSubsVersion = VERSION;
  document.querySelectorAll("#live-subs-overlay").forEach((el) => el.remove());

  const MAX_CHARS = 90;          // voice mode: roughly two lines
  const SUB_TIMEOUT_MS = 15000;  // no subtitles for this long -> back to voice
  const SHOW_SPANISH = false;    // set true to also show the original line (small)

  // ---------------------------------------------------------------- overlay box
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
    whiteSpace: "pre-line",
    zIndex: "2147483647",
    pointerEvents: "none",
    position: "fixed",
  });
  box.style.setProperty("overflow", "hidden");
  box.style.setProperty("display", "-webkit-box");
  box.style.setProperty("-webkit-box-orient", "vertical");
  box.style.setProperty("-webkit-line-clamp", "3");
  box.style.visibility = "hidden";

  const COLORS = ["#ffffff", "#ffe066", "#7fdbff", "#9cff9c", "#ffadd6", "#ffb366"];
  const colorFor = (spk) => (spk == null ? COLORS[0] : COLORS[spk % COLORS.length]);

  // ---------------------------------------------------------------- state
  let running = true;
  // voice mode
  let finalText = "", finalSpeaker = null, prevSpeaker = null;
  let partialText = "", partialSpeaker = null;
  let hideTimer = null;
  // subtitle mode
  let subMode = false;
  let lastSubAt = 0;
  let lastSubText = "";
  let subEnglish = "";
  let subSpanish = "";
  let reqId = 0;
  let pendingId = null;

  // ---------------------------------------------------------------- shadow-DOM aware search
  let rootsCache = [document];
  let rootsAt = 0;

  function allRoots() {
    const now = Date.now();
    if (now - rootsAt < 3000) return rootsCache;
    const roots = [document];
    const visit = (root) => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
      let n = walker.currentNode;
      while (n) {
        if (n.shadowRoot) {
          roots.push(n.shadowRoot);
          visit(n.shadowRoot);
        }
        n = walker.nextNode();
      }
    };
    visit(document);
    rootsCache = roots;
    rootsAt = now;
    return roots;
  }

  function findVideo() {
    let best = null, bestArea = 0;
    for (const r of allRoots()) {
      for (const v of r.querySelectorAll("video")) {
        const rect = v.getBoundingClientRect();
        const area = rect.width * rect.height;
        if (area > bestArea) { best = v; bestArea = area; }
      }
    }
    return best;
  }

  // Known subtitle containers, most specific first.
  const SITE_SELECTORS = [
    ".dss-subtitle-renderer-cue-window",   // Disney+
    ".dss-subtitle-renderer-wrapper",      // Disney+ (older)
    ".player-timedtext-text-container",    // Netflix
    ".atvwebplayersdk-captions-text",      // Prime Video
    "[data-testid='CueBoxContainer']",     // Max
    ".ytp-caption-window-container",       // YouTube CC
    ".shaka-text-container",
    ".vjs-text-track-display",
    ".jw-captions",
  ];
  const GENERIC = "[class*='subtitle' i], [class*='caption' i], [class*='timedtext' i]";

  const hiddenRoots = new WeakSet();
  const hiddenEls = new Set();
  const hiddenTracks = new Set();

  function hideInRoot(root, selector) {
    if (hiddenRoots.has(root)) return;
    hiddenRoots.add(root);
    const style = document.createElement("style");
    style.className = "live-subs-hide";
    style.textContent = `${SITE_SELECTORS.join(",")} { opacity: 0 !important; }`;
    (root === document ? document.head : root).appendChild(style);
  }

  function overlapsVideo(el, vr) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    return r.left < vr.right && r.right > vr.left && r.top < vr.bottom && r.bottom > vr.top;
  }

  function cleanText(t) {
    return t.replace(/\u00a0/g, " ").replace(/[ \t]+/g, " ").replace(/\n\s*\n/g, "\n").trim();
  }

  function readSubtitle() {
    const video = findVideo();

    // 1. Native text tracks (<track> / textTracks API).
    if (video) {
      for (const track of video.textTracks || []) {
        if (!["subtitles", "captions"].includes(track.kind)) continue;
        if (track.mode === "showing") { track.mode = "hidden"; hiddenTracks.add(track); }
        if (!hiddenTracks.has(track)) continue;
        const cues = track.activeCues;
        if (cues && cues.length) {
          const tmp = document.createElement("div");
          return cleanText([...cues].map((c) => { tmp.innerHTML = c.text || ""; return tmp.textContent; }).join("\n"));
        }
        return "";
      }
    }

    // 2. Known site containers (also inside shadow roots).
    for (const root of allRoots()) {
      for (const sel of SITE_SELECTORS) {
        const els = root.querySelectorAll(sel);
        if (!els.length) continue;
        hideInRoot(root, sel);
        return cleanText([...els].map((e) => e.innerText || e.textContent || "").join("\n"));
      }
    }

    // 3. Generic: short text in a subtitle-ish element on top of the video.
    if (video) {
      const vr = video.getBoundingClientRect();
      let best = null;
      for (const root of allRoots()) {
        for (const el of root.querySelectorAll(GENERIC)) {
          if (el === box || el.closest("button, [role=button], [role=menu], [role=dialog]")) continue;
          const t = cleanText(el.innerText || "");
          if (!t || t.length > 300 || !overlapsVideo(el, vr)) continue;
          if (!best || t.length > best.t.length) best = { el, t };
        }
      }
      if (best) {
        best.el.style.setProperty("opacity", "0", "important");
        hiddenEls.add(best.el);
        return best.t;
      }
    }
    return null; // no subtitle container on this page
  }

  function restoreOriginals() {
    for (const root of allRoots()) {
      root.querySelectorAll("style.live-subs-hide").forEach((s) => s.remove());
    }
    hiddenEls.forEach((el) => el.style.removeProperty("opacity"));
    hiddenEls.clear();
    hiddenTracks.forEach((t) => { t.mode = "showing"; });
    hiddenTracks.clear();
  }

  // ---------------------------------------------------------------- positioning
  function mount() {
    let fs = document.fullscreenElement;
    while (fs && fs.shadowRoot && fs.shadowRoot.fullscreenElement) fs = fs.shadowRoot.fullscreenElement;
    let host = fs ? (fs.shadowRoot || fs) : (document.querySelector("#movie_player") || document.body);
    if (box.parentNode !== host) host.appendChild(box);
    if (host === document.body) {
      // Sit near the bottom of the video, wherever it is on the page.
      const v = findVideo();
      const r = v && v.getBoundingClientRect();
      box.style.position = "fixed";
      if (r && r.width > 200) {
        box.style.left = `${r.left + r.width / 2}px`;
        box.style.bottom = `${window.innerHeight - r.bottom + r.height * 0.1}px`;
      } else {
        box.style.left = "50%";
        box.style.bottom = "14%";
      }
    } else {
      box.style.position = "absolute";
      box.style.left = "50%";
      box.style.bottom = "14%";
    }
  }
  document.addEventListener("fullscreenchange", mount);

  // ---------------------------------------------------------------- rendering
  function tail(text) {
    if (text.length <= MAX_CHARS) return text;
    const cut = text.slice(-MAX_CHARS);
    const sp = cut.indexOf(" ");
    return "…" + (sp >= 0 ? cut.slice(sp + 1) : cut);
  }

  function render(resetTimer = true) {
    if (!running) { box.style.visibility = "hidden"; return; }
    if (subMode) {
      const text = SHOW_SPANISH && subEnglish ? `${subEnglish}\n(${subSpanish})` : subEnglish;
      box.textContent = text;
      box.style.color = COLORS[0];
      box.style.opacity = "1";
      box.style.visibility = text ? "visible" : "hidden";
      return; // timing follows the original subtitles, no timer needed
    }
    const isPartial = !!partialText;
    let text = tail(isPartial ? partialText : finalText);
    const spk = isPartial ? partialSpeaker : finalSpeaker;
    if (text && !isPartial && spk != null && prevSpeaker != null && spk !== prevSpeaker) text = "– " + text;
    box.textContent = text;
    box.style.color = colorFor(spk);
    box.style.opacity = isPartial ? "0.85" : "1";
    box.style.visibility = text ? "visible" : "hidden";
    if (!resetTimer) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      finalText = partialText = "";
      prevSpeaker = null;
      if (!subMode) box.style.visibility = "hidden";
    }, 4000);
  }

  function setSubMode(on) {
    if (on === subMode) return;
    subMode = on;
    chrome.runtime.sendMessage({ type: "audio_pause", paused: on }).catch(() => {});
    if (!on) { subEnglish = ""; restoreOriginals(); }
    render();
  }

  // ---------------------------------------------------------------- subtitle polling
  setInterval(() => {
    if (!running) return;
    mount();
    const text = readSubtitle();
    const now = Date.now();
    if (text) lastSubAt = now;
    if (text !== null && text !== lastSubText) {
      lastSubText = text;
      if (text) {
        setSubMode(true);
        subSpanish = text;
        pendingId = `${++reqId}`;
        chrome.runtime.sendMessage({ type: "translate", id: pendingId, text }).catch(() => {});
      } else {
        // Original subtitle cleared: clear ours at the same moment.
        subEnglish = "";
        pendingId = null;
        render();
      }
    }
    if (subMode && now - lastSubAt > SUB_TIMEOUT_MS) setSubMode(false);
  }, 120);

  // ---------------------------------------------------------------- messages
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type !== "caption") return;
    switch (msg.kind) {
      case "started":
        running = true;
        break;
      case "translation":
        if (msg.id !== pendingId) return; // stale: the subtitle already changed
        subEnglish = msg.text;
        break;
      case "partial":
        if (subMode) return;
        partialText = msg.text;
        partialSpeaker = msg.speaker ?? finalSpeaker;
        break;
      case "status":
        if (subMode) return;
        partialText = msg.text;
        partialSpeaker = null;
        break;
      case "final":
        if (subMode) return;
        partialText = "";
        if (!msg.text) { render(false); return; }
        prevSpeaker = finalText ? finalSpeaker : null;
        finalText = msg.text;
        finalSpeaker = msg.speaker ?? finalSpeaker;
        break;
      case "stopped":
        running = false;
        finalText = partialText = subEnglish = lastSubText = "";
        finalSpeaker = prevSpeaker = partialSpeaker = null;
        subMode = false;
        restoreOriginals();
        break;
    }
    render();
  });
})();
