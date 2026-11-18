// Runs in a hidden extension page: captures tab audio, keeps it audible,
// streams 16 kHz PCM to the local server, and relays captions back.
// Also forwards on-screen subtitle text from the page for instant translation.

const SERVER = "ws://localhost:8765";
const params = new URLSearchParams(location.search);
const streamId = params.get("streamId");
const tabId = Number(params.get("tabId"));

let ws = null;
let audioPaused = false;

function caption(kind, text, speaker, id) {
  chrome.runtime.sendMessage({ type: "caption", tabId, kind, text, speaker, id });
}

function sendJSON(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function connect() {
  ws = new WebSocket(SERVER);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => sendJSON({ type: "pause", paused: audioPaused });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    caption(m.type, m.text, m.speaker, m.id);
  };
  ws.onclose = () => {
    caption("status", "Translator not running — open the Live Subtitles app (retrying…)");
    setTimeout(connect, 2000);
  };
}

// Subtitle text / pause requests from the overlay in our tab.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!sender.tab || sender.tab.id !== tabId) return;
  if (msg.type === "translate") {
    sendJSON({ type: "translate", id: msg.id, text: msg.text });
  } else if (msg.type === "audio_pause") {
    audioPaused = !!msg.paused;
    sendJSON({ type: "pause", paused: audioPaused });
  }
});

async function startAudio() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });

  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  source.connect(ctx.destination); // capturing mutes the tab; play it back so you still hear it

  await ctx.audioWorklet.addModule("pcm-worklet.js");
  const node = new AudioWorkletNode(ctx, "pcm-capture");
  source.connect(node);
  node.connect(ctx.destination); // outputs silence; keeps the node processing
  node.port.onmessage = (e) => {
    if (!audioPaused && ws && ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };
}

connect(); // subtitle translation works even if audio capture fails
startAudio().catch((e) => caption("status", "Audio capture failed: " + e.message));
