# Browser Simulator

## Current implementation

The browser simulator is a same-origin FastAPI/static application available at `/simulator/`. It provides a deterministic environment for UI, API, transport, policy-preview, and future conversation integration work without dialing or messaging anyone.

It currently supports:

- Disclosure-first synthetic sessions.
- English, Hindi, and mixed-language selection.
- Text turns with bounded length and retry-safe client operation identifiers.
- Policy-reviewed mock WhatsApp, callback, and structured artifact previews with explicit synthetic consent/contact eligibility.
- Deterministic latency and failure injection with local state rollback for known action failures.
- Bounded session timelines and session-scoped history with no cross-session `lead_ref` lookup.
- Playback interruption using browser speech synthesis cancellation.
- Deterministic English/Hindi/Hinglish replay fixtures.
- Microphone capture through `MediaRecorder`, preferring Opus where supported.
- Same-origin WebSocket audio transport with bounded browser queues, backpressure, capped reconnects, and chunk limits.
- Explicit session closure and capacity recovery.
- Session-scoped deterministic discovery, requirement revisions, repetition handling, and evidence-grounded Hot/Warm/Cold/Review outcomes.
- Immediate opt-out stop, one neutral abuse redirection, and safe refusal of internal-information, jailbreak, and prompt-injection requests.
- Bounded in-memory callback delay and six-industry sample-deck selection; default policy state blocks previews.
- Default-off durable accepted-turn journaling, restart recovery, and bounded minimized replay.

## Not implemented

- No PSTN, WhatsApp call, live WhatsApp message, durable callback, or binary artifact action.
- No speech-to-text or local TTS provider integration.
- No model-backed/free-form extraction; the current conversation rules are deterministic and intentionally bounded.
- No PPTX renderer; sample decks are dependency-free structured previews from fixed templates.
- No durable simulator timeline, consent/contact policy, callback/action state, audio metadata, or artifact state.
- No authenticated public multi-user deployment. Session UUIDs are local simulator capabilities, not production authentication.
- No measured browser-audio delivery, transcription accuracy, or latency guarantee.

## Run locally

```powershell
python -m uvicorn pitchbot.main:app --reload
```

Open `http://127.0.0.1:8000/simulator/`.

Use synthetic data only. The browser page and API share one origin; no CORS middleware is enabled.

Durable conversation turns remain disabled unless `PITCHBOT_ENABLE_DURABLE_HISTORY=true` and `PITCHBOT_DURABLE_HISTORY_DIGEST_KEY` contains a managed 32-byte hexadecimal key. Apply Alembic migrations before enabling it. Resume with `POST /api/simulator/sessions/{session_id}/resume` and the original `lead_ref`; read at most 100 minimized results from `GET /api/simulator/sessions/{session_id}/durable-history`. All action previews on recovered sessions fail closed because process-local consent, contact policy, and preview details cannot be reconstructed safely.

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
- Conversation turns, retained turn operations (including failures), facts, evidence, classification history, action records, and mock adapter histories are bounded; only high-level outcomes enter simulator metadata.
- Turn API callers must provide and reuse `operation_id` for retries; conflicting reuse and operation-capacity exhaustion fail closed.
- Durable reads require an active session UUID capability, validate the complete lead stream, and expose no raw buyer text, internal lead/source identifiers, operation fingerprints, or turn digests.

## Cleanup

Use **Close session** to remove process-local session, callback, deck, and mock action history and stop microphone tracks/sockets. Scheduled mock callbacks are canceled before removal. A failed or canceled cleanup keeps the session closed to normal work but permits another close request to retry cleanup. Stopping the API clears all remaining simulator memory. When durable history is enabled, accepted minimized conversation transitions remain subject to the lead privacy lifecycle; no external provider state is created.
