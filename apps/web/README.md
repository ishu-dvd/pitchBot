# Browser Simulator

FastAPI serves these static files at `/simulator/`, keeping UI, API, and WebSocket traffic same-origin.

- `index.html`: accessible simulator controls and timeline.
- `app.js`: sessions, turns, replay, interruption, and safe rendering.
- `audio-transport.js`: bounded PCM/WebSocket transport with backpressure and capped reconnects.
- `pcm-worklet.js`: audio-thread worklet that cuts the microphone into 30 ms frames of mono 16-bit PCM at 16 kHz — the only shape the server's voice-activity detector accepts.
- `styles.css`: local styles without external assets.

See [../../docs/SIMULATOR.md](../../docs/SIMULATOR.md) for capabilities, limitations, privacy, and cleanup.
