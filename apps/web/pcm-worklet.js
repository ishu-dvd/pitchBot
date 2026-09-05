// Cuts the microphone into the exact frames the server's detector accepts.
//
// The browser used to record with `MediaRecorder`, which produces WebM/Opus. Nothing in
// the server decodes Opus, and `WebRtcVoiceActivityDetector` accepts only 320, 640 or 960
// bytes of mono 16-bit PCM - so every frame the browser sent was rejected, counted as a
// detector failure and treated as silence. Measured: 120 frames in, 0 utterances out, the
// turn-taking machine never leaving `idle`. The buyer was never heard.
//
// A worklet runs on the audio thread and is handed 128-sample blocks, which is not a frame
// size anything downstream wants. This regroups them into whole 30 ms frames and converts
// to little-endian int16, so what leaves the browser is already what the detector needs.

const DEFAULT_FRAME_SAMPLES = 480; // 30 ms at 16 kHz
const INT16_MAX = 0x7fff;
const INT16_MIN = -0x8000;

class PcmFrameSplitter extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const requested = options?.processorOptions?.frameSamples;
    this.frameSamples = Number.isInteger(requested) && requested > 0
      ? requested
      : DEFAULT_FRAME_SAMPLES;
    // One frame's worth of samples, filled across however many blocks it takes.
    this.pending = new Float32Array(this.frameSamples);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    // No input yet, or the track ended. Returning true keeps the processor alive; a
    // microphone that goes briefly silent must not tear down the capture graph.
    if (!channel) return true;

    let offset = 0;
    while (offset < channel.length) {
      const take = Math.min(this.frameSamples - this.filled, channel.length - offset);
      this.pending.set(channel.subarray(offset, offset + take), this.filled);
      this.filled += take;
      offset += take;
      if (this.filled === this.frameSamples) {
        const buffer = this.drainFrame();
        // Transferred rather than copied: at 33 frames a second on the audio thread, a
        // structured clone per frame is work this thread cannot afford to do.
        this.port.postMessage(buffer, [buffer]);
      }
    }
    return true;
  }

  drainFrame() {
    const frame = new Int16Array(this.frameSamples);
    for (let index = 0; index < this.frameSamples; index += 1) {
      // Float32 audio is nominally [-1, 1] but is not guaranteed to stay inside it, and a
      // sample that wraps instead of clipping turns a loud vowel into a burst of noise the
      // detector reads as speech.
      const sample = Math.max(-1, Math.min(1, this.pending[index]));
      frame[index] = Math.round(sample < 0 ? sample * -INT16_MIN : sample * INT16_MAX);
    }
    this.filled = 0;
    return frame.buffer;
  }
}

registerProcessor("pcm-frame-splitter", PcmFrameSplitter);
