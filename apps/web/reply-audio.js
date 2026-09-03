// Plays the reply audio the server synthesised, instead of the browser's own voice.
//
// The server sends mono 16-bit little-endian PCM at the voice's own sample rate, framed
// into fixed-size binary messages between a `reply-audio-begin` and a `reply-audio-end`.
// Each frame is scheduled to start exactly where the previous one ended, so the reply is
// gapless even though it arrives far faster than it plays: synthesis was measured at
// roughly 19x realtime, so a twenty-second reply is fully delivered in about a second.
//
// That speed is also why stopping matters. By the time the buyer interrupts, the whole
// reply is usually already queued in this player, so barge-in has to stop *scheduled*
// audio - the server cancelling its stream is necessary but nowhere near sufficient.

const MAX_QUEUED_SECONDS = 120;

export class ReplyAudioPlayer {
  constructor(onFinished) {
    this.onFinished = onFinished || (() => {});
    this.context = null;
    this.sources = [];
    this.nextStartTime = 0;
    this.sampleRate = 0;
    this.active = false;
    // Only the newest reply may report that playback finished. A stopped reply still
    // fires its scheduled `onended` handlers, which would otherwise hand the floor back
    // while the reply that replaced it is still being spoken.
    this.generation = 0;
  }

  get supported() {
    return typeof (window.AudioContext || window.webkitAudioContext) === "function";
  }

  begin(sampleRateHz) {
    this.stop();
    if (!this.supported || !(sampleRateHz > 0)) return false;
    this.generation += 1;
    if (this.context === null) {
      const Context = window.AudioContext || window.webkitAudioContext;
      this.context = new Context();
    }
    // A context created before the first user gesture starts suspended, and scheduling
    // into a suspended context silently plays nothing at all.
    if (this.context.state === "suspended") this.context.resume().catch(() => {});
    this.sampleRate = sampleRateHz;
    this.nextStartTime = this.context.currentTime;
    this.active = true;
    return true;
  }

  push(arrayBuffer) {
    if (!this.active || this.context === null) return;
    if (arrayBuffer.byteLength === 0 || arrayBuffer.byteLength % 2 !== 0) return;
    const queued = this.nextStartTime - this.context.currentTime;
    if (queued > MAX_QUEUED_SECONDS) return;
    const samples = new Int16Array(arrayBuffer);
    const buffer = this.context.createBuffer(1, samples.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) {
      // Int16 to the [-1, 1) range WebAudio expects. 32768 rather than 32767 so the most
      // negative sample maps to exactly -1 instead of clipping past it.
      channel[index] = samples[index] / 32768;
    }
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    const startAt = Math.max(this.nextStartTime, this.context.currentTime);
    source.start(startAt);
    this.nextStartTime = startAt + buffer.duration;
    this.sources.push(source);
  }

  // Called once the server has sent every frame. The floor is handed back when the audio
  // finishes *playing*, which is seconds after the last frame finishes arriving.
  end() {
    if (!this.active) return false;
    const generation = this.generation;
    const last = this.sources[this.sources.length - 1];
    if (!last) {
      this.active = false;
      this.onFinished();
      return false;
    }
    last.onended = () => {
      if (generation !== this.generation) return;
      this.active = false;
      this.onFinished();
    };
    return true;
  }

  stop() {
    this.generation += 1;
    this.active = false;
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch (error) {
        // Already finished or never started; nothing to stop.
      }
      source.disconnect();
    }
    this.sources = [];
    this.nextStartTime = this.context === null ? 0 : this.context.currentTime;
  }
}
