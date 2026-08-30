const MAX_QUEUE_ITEMS = 24;
const MAX_BUFFERED_BYTES = 512 * 1024;
const MAX_CHUNK_BYTES = 256 * 1024;

export class AudioTransport {
  constructor(onDiagnostics) {
    this.onDiagnostics = onDiagnostics;
    this.queue = [];
    this.dropped = 0;
    this.socket = null;
    this.recorder = null;
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
    this.socket = new WebSocket(`${protocol}//${location.host}/api/simulator/sessions/${sessionId}/audio?media_type=${encodedType}`);
    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.flush();
    };
    this.socket.onmessage = () => this.flush();
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
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      throw new Error("Microphone recording is not supported by this browser.");
    }
    this.stop();
    const generation = this.generation;
    this.stopped = false;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      this.stopped = true;
      throw error;
    }
    if (this.stopped || generation !== this.generation) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    this.stream = stream;
    const preferred = "audio/webm;codecs=opus";
    const options = MediaRecorder.isTypeSupported(preferred) ? { mimeType: preferred } : undefined;
    this.recorder = new MediaRecorder(this.stream, options);
    this.recorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.enqueue(event.data);
    };
    try {
      this.connect(sessionId, this.recorder.mimeType, generation);
      this.recorder.start(250);
    } catch (error) {
      this.stop();
      throw error;
    }
    return true;
  }

  enqueue(blob) {
    if (blob.size > MAX_CHUNK_BYTES) {
      this.dropped += 1;
      this.report();
      return;
    }
    if (this.queue.length >= MAX_QUEUE_ITEMS) {
      this.queue.shift();
      this.dropped += 1;
    }
    this.queue.push(blob);
    this.report();
    this.flush();
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
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
    if (this.socket) this.socket.close(1000, "stopped");
    this.recorder = null;
    this.stream = null;
    this.socket = null;
    this.queue = [];
    this.report();
  }

  report() {
    this.onDiagnostics({ queueDepth: this.queue.length, dropped: this.dropped });
  }
}
