// Mixes to mono, resamples to 16 kHz, and posts 100 ms Float32 chunks.
const TARGET_RATE = 16000;
const CHUNK = 1600; // 100 ms

class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.pos = 0;      // fractional read position relative to the current block
    this.prev = 0;     // last sample of the previous block (index -1)
    this.out = new Float32Array(CHUNK);
    this.n = 0;
  }

  push(v) {
    this.out[this.n++] = v;
    if (this.n === CHUNK) {
      this.port.postMessage(this.out.buffer, [this.out.buffer]);
      this.out = new Float32Array(CHUNK);
      this.n = 0;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const len = input[0].length;
    const mono = new Float32Array(len);
    for (const ch of input) for (let i = 0; i < len; i++) mono[i] += ch[i] / input.length;

    while (this.pos < len - 1) {
      const i = Math.floor(this.pos);
      const f = this.pos - i;
      const a = i < 0 ? this.prev : mono[i];
      const b = mono[i + 1];
      this.push(a + (b - a) * f);
      this.pos += this.ratio;
    }
    this.pos -= len;
    this.prev = mono[len - 1];
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);

