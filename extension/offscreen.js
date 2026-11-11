// Runs in a hidden extension page: captures tab audio, keeps it audible,
// streams 16 kHz PCM to the local server, and relays captions back.

const SERVER = "ws://localhost:8765";
const params = new URLSearchParams(location.search);
const streamId = params.get("streamId");
const tabId = Number(params.get("tabId"));

let ws = null;

function caption(kind, text, speaker) {
  chrome.runtime.sendMessage({ type: "caption", tabId, kind, text, speaker });
}

function connect() {
  ws = new WebSocket(SERVER);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    caption(m.type, m.text, m.speaker);
  };
  ws.onclose = () => {
    caption("status", "Translator not running — start it with ./live.sh (retrying…)");
    setTimeout(connect, 2000);
  };
}

async function start() {
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
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };

  connect();
}

start().catch((e) => caption("status", "Capture failed: " + e.message));

