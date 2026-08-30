# Browser Simulator

FastAPI serves these static files at `/simulator/`, keeping UI, API, and WebSocket traffic same-origin.

- `index.html`: accessible simulator controls and timeline.
- `app.js`: sessions, turns, replay, interruption, and safe rendering.
- `audio-transport.js`: bounded `MediaRecorder`/WebSocket transport with Opus preference, backpressure, and capped reconnects.
- `styles.css`: local styles without external assets.

See [../../docs/SIMULATOR.md](../../docs/SIMULATOR.md) for capabilities, limitations, privacy, and cleanup.
