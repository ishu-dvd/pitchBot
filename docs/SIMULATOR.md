# Browser Simulator

## Current implementation

The browser simulator is a same-origin FastAPI/static application available at `/simulator/`. It provides a deterministic environment for UI, API, transport, policy-preview, and future conversation integration work without dialing or messaging anyone.

It currently supports:

- Disclosure-first synthetic sessions.
- English, Hindi, and mixed-language selection.
- Text turns with bounded length.
- Policy-reviewed mock WhatsApp, callback, and structured artifact previews with explicit synthetic consent/contact eligibility.
- Deterministic latency and failure injection.
- Bounded session timelines and session-scoped history with no cross-session `lead_ref` lookup.
- Playback interruption using browser speech synthesis cancellation.
- Deterministic English/Hindi/Hinglish replay fixtures.
- Microphone capture through `MediaRecorder`, preferring Opus where supported.
- Same-origin WebSocket audio transport with bounded browser queues, backpressure, capped reconnects, and chunk limits.
- Explicit session closure and capacity recovery.
- Session-scoped deterministic discovery, requirement revisions, repetition handling, and evidence-grounded Hot/Warm/Cold/Review outcomes.
- Immediate opt-out stop, one neutral abuse redirection, and safe refusal of internal-information, jailbreak, and prompt-injection requests.
- Bounded in-memory callback delay and six-industry sample-deck selection; default policy state blocks previews.

## Not implemented

- No PSTN, WhatsApp call, live WhatsApp message, durable callback, or binary artifact action.
- No speech-to-text or local TTS provider integration.
- No model-backed/free-form extraction; the current conversation rules are deterministic and intentionally bounded.
- No PPTX renderer; sample decks are dependency-free structured previews from fixed templates.
- No durable simulator history; state is process-local and disappears on restart.
- No authenticated public multi-user deployment. Session UUIDs are local simulator capabilities, not production authentication.
- No measured browser-audio delivery, transcription accuracy, or latency guarantee.

## Run locally

```powershell
python -m uvicorn pitchbot.main:app --reload
```

Open `http://127.0.0.1:8000/simulator/`.

Use synthetic data only. The browser page and API share one origin; no CORS middleware is enabled.

## Audio privacy and limits

- The server discards each audio byte message after reading it.
- Only byte count, reported media type, sequence, and `audio_retained=false` metadata enter the bounded in-memory timeline.
- Each message is limited to 256 KiB.
- The browser queue holds at most 24 pending chunks and drops the oldest chunk under pressure to preserve recency.
- Socket buffering pauses sends until acknowledgements reduce pressure.
- Reconnect attempts are capped and use exponential delay.
- Normal, policy, and oversized-message closures do not reconnect.
- Session close cancels pending microphone startup and reconnect timers so audio cannot restart afterward.

The current transport does not claim lossless delivery across disconnects. A later WebRTC/data-call milestone will define authenticated sessions and delivery/recovery requirements.

## Browser fallback speech

The UI may use browser-native speech synthesis for audible replies. Voice availability, language quality, privacy behavior, and latency vary by browser and operating system. It is not the PitchBot TTS baseline and must not be included in model benchmark claims.

## Security controls

- Same-origin API, static assets, and WebSocket.
- WebSocket browser origins must exactly match the request host.
- Content Security Policy limits resources and connections to self.
- Framing is denied and MIME sniffing is disabled.
- UI rendering uses `textContent`, not HTML insertion, for transcript and replay content.
- History exists only inside the current session and requires its UUID; reusing a lead reference cannot expose another session.
- Sessions, events, lead history, audio metadata, text length, audio chunks, reconnects, and simulated latency are bounded.
- Conversation turns, retained facts, evidence, and classification history are bounded; only high-level outcomes enter simulator metadata.

## Cleanup

Use **Close session** to remove process-local session state and stop microphone tracks/sockets. Stopping the API clears all remaining simulator memory. No database record or external provider state is created.
