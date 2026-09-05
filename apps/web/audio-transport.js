const MAX_QUEUE_ITEMS = 24;
const MAX_BUFFERED_BYTES = 512 * 1024;

// What the server's detector accepts: mono 16-bit PCM at 16 kHz, in 10, 20 or 30 ms frames.
// 30 ms is the largest of those, so it is the fewest messages per second that still fits.
const TARGET_SAMPLE_RATE_HZ = 16_000;
const FRAME_SAMPLES = 480;
const FRAME_BYTES = FRAME_SAMPLES * 2;
const PCM_MEDIA_TYPE = `audio/pcm;rate=${TARGET_SAMPLE_RATE_HZ};channels=1;bits=16`;
const WORKLET_URL = "/simulator/pcm-worklet.js";

export class AudioTransport {
  constructor(onDiagnostics, onMessage, onBinary) {
    this.onDiagnostics = onDiagnostics;
    this.onMessage = onMessage || (() => {});
    this.onBinary = onBinary || (() => {});
    this.queue = [];
    this.dropped = 0;
    this.socket = null;
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.stream = null;
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.stopped = true;
    this.generation = 0;
  }

  connect(sessionId, mediaType, generation) {
    if (this.stopped || generation !== this.generation) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const encodedType = encodeURIComponent(mediaType || "application/octet-stream");
    const url = `${protocol}//${location.host}/api/simulator/sessions/${sessionId}/audio?media_type=${encodedType}`;
    // A browser cannot set a header on a WebSocket, so the key travels as a subprotocol.
    // Not as a query parameter: that would be written to every access log and proxy trace
    // the connection passes through.
    const apiKey = sessionStorage.getItem("pitchbot.apiKey") || "";
    const protocols = apiKey ? ["pitchbot.v1", `pitchbot.key.${apiKey}`] : [];
    this.socket = protocols.length ? new WebSocket(url, protocols) : new WebSocket(url);
    // Reply audio arrives as binary frames. Without this they are delivered as Blobs,
    // which can only be read asynchronously - and reading them out of order would
    // reassemble the reply's PCM scrambled.
    this.socket.binaryType = "arraybuffer";
    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.flush();
    };
    this.socket.onmessage = (event) => {
      if (typeof event.data !== "string") {
        this.onBinary(event.data);
        this.flush();
        return;
      }
      let payload = null;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        payload = null;
      }
      if (payload) this.onMessage(payload);
      this.flush();
    };
    this.socket.onclose = (event) => {
      if ([1000, 1008, 1009].includes(event.code)) this.stopped = true;
      if (!this.stopped && this.reconnectAttempts < 5) {
        const delay = Math.min(250 * (2 ** this.reconnectAttempts), 4000);
        this.reconnectAttempts += 1;
        this.reconnectTimer = setTimeout(
          () => this.connect(sessionId, mediaType, generation),
          delay,
        );
      }
    };
  }

  async start(sessionId) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone capture is not supported by this browser.");
    }
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      throw new Error("Microphone capture is not supported by this browser.");
    }
    this.stop();
    const generation = this.generation;
    this.stopped = false;

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (error) {
      this.stopped = true;
      throw error;
    }
    if (this.stopped || generation !== this.generation) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    this.stream = stream;

    try {
      // Asking the context for 16 kHz makes the browser resample the microphone for us,
      // which is the only resampler here worth trusting.
      const context = new AudioContextClass({ sampleRate: TARGET_SAMPLE_RATE_HZ });
      this.context = context;
      if (!context.audioWorklet) {
        throw new Error(
          "This browser cannot capture raw audio (no AudioWorklet), so the microphone " +
            "cannot be used here. Refusing to fall back to a recorder the server cannot " +
            "decode, which would look like a working microphone that is never heard.",
        );
      }
      // Not every browser honours the requested rate. Sending 16 kHz-shaped frames from a
      // 48 kHz context would put three times as much audio in each frame as its byte count
      // claims, and the server times an utterance by summing frame durations - so it would
      // silently mis-scale every endpointing threshold rather than fail.
      if (context.sampleRate !== TARGET_SAMPLE_RATE_HZ) {
        throw new Error(
          `This browser captures at ${context.sampleRate} Hz and will not resample to ` +
            `${TARGET_SAMPLE_RATE_HZ} Hz, which the server's detector requires.`,
        );
      }
      if (context.state === "suspended") await context.resume();
      await context.audioWorklet.addModule(WORKLET_URL);
      if (this.stopped || generation !== this.generation) return false;

      this.source = context.createMediaStreamSource(stream);
      this.worklet = new AudioWorkletNode(context, "pcm-frame-splitter", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        channelCount: 1,
        processorOptions: { frameSamples: FRAME_SAMPLES },
      });
      this.worklet.port.onmessage = (event) => this.enqueue(event.data);
      this.source.connect(this.worklet);
      // A worklet whose output goes nowhere is not guaranteed to be pulled by the graph.
      // Routing it through a silent gain keeps it running without playing the buyer's own
      // voice back at them.
      const muted = context.createGain();
      muted.gain.value = 0;
      this.worklet.connect(muted);
      muted.connect(context.destination);

      this.connect(sessionId, PCM_MEDIA_TYPE, generation);
    } catch (error) {
      this.stop();
      throw error;
    }
    return true;
  }

  enqueue(frame) {
    const size = frame.byteLength ?? frame.size ?? 0;
    if (size !== FRAME_BYTES) {
      // The server's detector accepts one exact frame length and rejects everything else,
      // so a frame of any other size cannot be classified and would be counted there as a
      // detector fault. Dropping it here instead keeps that visible in the diagnostics
      // rather than as silence the buyer cannot explain.
      this.dropped += 1;
      this.report();
      return;
    }
    if (this.queue.length >= MAX_QUEUE_ITEMS) {
      // Oldest first: a backlog means the socket is behind, and the buyer's most recent
      // speech is worth more than the speech they have already moved on from.
      this.queue.shift();
      this.dropped += 1;
    }
    this.queue.push(frame);
    this.report();
    this.flush();
  }

  sendControl(text) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(text);
  }

  flush() {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    if (this.socket.bufferedAmount > MAX_BUFFERED_BYTES || this.queue.length === 0) return;
    this.socket.send(this.queue.shift());
    this.report();
  }

  stop() {
    this.stopped = true;
    this.generation += 1;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.worklet) {
      // Dropping the handler first: a frame delivered during teardown would otherwise be
      // queued for a socket that is closing, and counted as a drop that never happened.
      this.worklet.port.onmessage = null;
      this.worklet.disconnect();
    }
    if (this.source) this.source.disconnect();
    if (this.context && this.context.state !== "closed") {
      // Closing releases the audio hardware. Failures are ignored on purpose: this runs on
      // every stop, including ones that follow an error, and must not raise a second time.
      this.context.close().catch(() => {});
    }
    if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
    if (this.socket) this.socket.close(1000, "stopped");
    this.worklet = null;
    this.source = null;
    this.context = null;
    this.stream = null;
    this.socket = null;
    this.queue = [];
    this.report();
  }

  report() {
    this.onDiagnostics({ queueDepth: this.queue.length, dropped: this.dropped });
  }
}
