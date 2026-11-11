/* The microphone, as the page records from it: what is said, held while a
   recording runs and handed back in a format the dictation model's engine
   takes — the browser's own compressed recording where the engine reads
   it, a WAV written here otherwise — and how loud each band of pitch is
   now, what the level draws. A leaf: it knows the browser's audio and
   nothing of otaku. The terminal's twin is `terminal.tty.microphone`. */

// What speech recognition reads; a WAV is written at this rate.
const _RATE = 16000;
// The bands, nine as the level has bars: equal importance to understanding
// speech — ANSI S3.5-1997's critical-band table (the Speech Intelligibility
// Index), split in equal shares from 200 Hz, above mains hum, to 7.7 kHz.
// Each is read between its own floor and ceiling in the analyser's
// decibels: the terminal's measured ones (`terminal.tty.microphone`), 13.6
// dB down, where the analyser reads a full-scale tone. Measured on the
// author's microphone; the browser's own processing may want them nudged.
const _BANDS = [ // [low Hz, high Hz, floor dB, ceiling dB]
  [200, 478, -86.0, -52.4],
  [478, 713, -89.4, -52.5],
  [713, 996, -89.8, -62.2],
  [996, 1346, -98.1, -73.1],
  [1346, 1789, -102.2, -77.0],
  [1789, 2369, -107.5, -81.7],
  [2369, 3158, -113.0, -86.2],
  [3158, 4329, -113.6, -83.0],
  [4329, 7700, -115.2, -85.4],
];
// The browser's compressed recordings, by the extension an engine names
// each with, tried in this order where the engine takes it.
const _COMPRESSED = [
  ["webm", "audio/webm;codecs=opus"],
  ["ogg", "audio/ogg;codecs=opus"],
  ["m4a", "audio/mp4"],
];
// The PCM a WAV is written from, a block at a time.
const _BLOCK = 4096;

/** Whether this page may ask for the microphone at all: a browser offers
    one to a secure page alone — HTTPS, or otaku opened on this machine. */
export const canRecord = () =>
  Boolean(navigator.mediaDevices?.getUserMedia) && typeof AudioContext !== "undefined";

/** A recording, started: the microphone asked for — the browser's own
    question the first time — and what it hears kept in the first of
    `formats` (extensions, "wav" always among them; or the promise of them,
    awaited once the microphone is open, so the two are asked side by side)
    the browser can write. Rejects as the browser refuses (`NotAllowedError`,
    `NotFoundError`), or as `formats` does. */
export async function record(formats) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const context = new AudioContext();
  // iOS opens a context suspended until a gesture resumes it; this one
  // was asked for by a press, so it may run.
  await context.resume();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.5;
  source.connect(analyser);
  const bins = new Float32Array(analyser.frequencyBinCount);
  const release = () => {
    for (const track of stream.getTracks()) track.stop();
    context.close().catch(() => {});
  };
  let taken;
  try {
    taken = await formats;
  } catch (error) {
    release();
    throw error;
  }
  const compressed =
    typeof MediaRecorder !== "undefined" &&
    _COMPRESSED.find(([format, mime]) => taken.includes(format) && MediaRecorder.isTypeSupported(mime));
  const capture = compressed ? _compressed(stream, ...compressed) : _pcm(context, source);
  return {
    /** How loud each band of pitch is now, low to high, 0 to 1. */
    bands() {
      analyser.getFloatFrequencyData(bins);
      const perBin = context.sampleRate / analyser.fftSize;
      return _BANDS.map(([low, high, floor, ceiling]) => {
        const first = Math.floor(low / perBin);
        const last = Math.max(first + 1, Math.floor(high / perBin));
        let loudest = -Infinity;
        for (let bin = first; bin < last; bin++) loudest = Math.max(loudest, bins[bin]);
        return Math.min(1, Math.max(0, (loudest - floor) / (ceiling - floor)));
      });
    },
    /** What was said, the microphone released: `{blob, format}`. */
    async stop() {
      const kept = await capture.stop();
      release();
      return kept;
    },
    /** The microphone released and what was said dropped. */
    discard() {
      capture.stop().catch(() => {});
      release();
    },
  };
}

/** The browser's own recording, kept as it writes it. */
function _compressed(stream, format, mime) {
  const recorder = new MediaRecorder(stream, { mimeType: mime });
  const chunks = [];
  recorder.ondataavailable = (event) => event.data.size && chunks.push(event.data);
  recorder.start();
  return {
    stop: () =>
      new Promise((resolve) => {
        recorder.onstop = () => resolve({ blob: new Blob(chunks, { type: mime }), format });
        if (recorder.state === "inactive") recorder.onstop();
        else recorder.stop();
      }),
  };
}

/** The raw samples, kept a block at a time and written as a 16 kHz WAV.
    A ScriptProcessor rather than a worklet: every browser runs it, and
    it needs no module of its own. */
function _pcm(context, source) {
  const blocks = [];
  const processor = context.createScriptProcessor(_BLOCK, 1, 1);
  processor.onaudioprocess = (event) => blocks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  source.connect(processor);
  // Connected through, or the browser never runs it; it writes silence.
  processor.connect(context.destination);
  return {
    stop: async () => {
      processor.disconnect();
      source.disconnect(processor);
      return { blob: _wav(_resampled(blocks, context.sampleRate)), format: "wav" };
    },
  };
}

/** The blocks as one run of samples at `_RATE`, each the mean of the
    ones it stands for. */
function _resampled(blocks, rate) {
  const total = blocks.reduce((sum, block) => sum + block.length, 0);
  const joined = new Float32Array(total);
  let at = 0;
  for (const block of blocks) {
    joined.set(block, at);
    at += block.length;
  }
  const step = rate / _RATE;
  const out = new Float32Array(Math.floor(total / step));
  for (let i = 0; i < out.length; i++) {
    const from = Math.floor(i * step);
    const to = Math.max(from + 1, Math.floor((i + 1) * step));
    let sum = 0;
    for (let j = from; j < to; j++) sum += joined[j];
    out[i] = sum / (to - from);
  }
  return out;
}

/** `samples` as a 16-bit mono WAV file. */
function _wav(samples) {
  const bytes = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, value) => [...value].forEach((c, i) => bytes.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF");
  bytes.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  bytes.setUint32(16, 16, true); // the format chunk's size
  bytes.setUint16(20, 1, true); // PCM
  bytes.setUint16(22, 1, true); // mono
  bytes.setUint32(24, _RATE, true);
  bytes.setUint32(28, _RATE * 2, true); // bytes a second
  bytes.setUint16(32, 2, true); // bytes a frame
  bytes.setUint16(34, 16, true); // bits a sample
  text(36, "data");
  bytes.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => {
    const clamped = Math.max(-1, Math.min(1, sample));
    bytes.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  });
  return new Blob([bytes.buffer], { type: "audio/wav" });
}
