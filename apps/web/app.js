import { AudioTransport } from "/simulator/audio-transport.js";
import { ReplyAudioPlayer } from "/simulator/reply-audio.js";

let sessionId = null;
const timeline = document.getElementById("timeline");
const preview = document.getElementById("action-preview");
const recall = document.getElementById("lead-recall");
const status = document.getElementById("connection-status");
const closeButton = document.getElementById("close-session");
const sendButton = document.getElementById("send-turn");
const interruptButton = document.getElementById("interrupt");
const startAudioButton = document.getElementById("start-audio");
const stopAudioButton = document.getElementById("stop-audio");
const diagnostics = document.getElementById("audio-diagnostics");
const speech = document.getElementById("speech-status");

let speechGeneration = 0;

function speak(text, onFinished) {
  speechGeneration += 1;
  const generation = speechGeneration;
  // Only the newest utterance may report that playback finished. Cancelling an older
  // one fires its end handler, which would otherwise hand the floor back while the
  // reply that replaced it is still being spoken.
  const finish = () => {
    if (generation === speechGeneration && onFinished) onFinished();
  };
  if (!("speechSynthesis" in window)) {
    finish();
    return;
  }
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  // The server holds the floor until playback ends, so this must fire on every exit
  // path or the buyer would be treated as interrupting for the rest of the call.
  utterance.onend = finish;
  utterance.onerror = finish;
  speechSynthesis.speak(utterance);
}

function stopSpeaking() {
  speechGeneration += 1;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  player.stop();
}

// The floor is held by the server until playback ends, so this is the one place that
// hands it back - whichever voice did the speaking.
const player = new ReplyAudioPlayer(() => audio.sendControl("playback-finished"));
// What the server said it would do for the reply currently in flight. Read when
// `reply-audio-end` arrives, so a reply the server could not synthesise can still be
// spoken by the browser rather than being silently dropped.
let pendingReplyText = "";

const OUTCOME_LABELS = {
  "no-speech-recognized": "No speech was recognised in that utterance",
  "low-confidence": "Transcript confidence was too low to use",
  "oversize": "Utterance exceeded its size cap and was discarded",
  "transcriber-unavailable": "Transcription was unavailable for this utterance",
};

function onReplyAudio(payload) {
  if (payload.type === "reply-audio-begin") {
    // `after` for a reply only: it schedules behind a filler that is still draining
    // rather than cutting it off. A filler never queues behind anything, because
    // anything it could queue behind is the previous turn's answer.
    if (!player.begin(payload.sample_rate_hz, { after: !payload.filler })) {
      // No WebAudio here, so the server's voice cannot be played at all. Speaking the
      // text keeps the buyer hearing the answer and keeps the floor accounted for - but
      // only for a reply. There is no text for a filler, and a browser voice saying
      // "hmm" over the top of the answer is worse than the silence it was covering.
      if (!payload.filler) speak(pendingReplyText, () => audio.sendControl("playback-finished"));
    }
    return true;
  }
  if (payload.type !== "reply-audio-end") return false;
  if (payload.aborted) {
    // The buyer interrupted. The server has already handed the floor back, so playback
    // stops without reporting that it finished.
    player.stop();
    return true;
  }
  if (payload.frame_count > 0) {
    // A filler is played but never reported: it never held the floor, and handing back
    // one it does not hold would mute the reply that is about to take it.
    player.end({ report: !payload.filler });
    return true;
  }
  if (payload.filler) {
    // Nothing to play. A missing "hmm" is a missing courtesy, not a missing answer.
    player.stop();
    return true;
  }
  // Synthesis produced nothing - it failed, or the reply was punctuation only. The reply
  // text is already on screen; speaking it locally is strictly better than silence.
  player.stop();
  speak(pendingReplyText, () => audio.sendControl("playback-finished"));
  return true;
}

function onSpeechMessage(payload) {
  if (payload.type === "ready") {
    const listening = payload.speech_input_available
      ? `Listening. Endpoint after ${payload.end_silence_ms} ms of silence.`
      : `Listening for turn-taking only; no transcriber is configured. Endpoint after ${payload.end_silence_ms} ms of silence.`;
    speech.textContent = payload.speech_output_available
      ? `${listening} Replies are spoken by the server.`
      : listening;
    return;
  }
  if (payload.type === "ack") {
    speech.textContent = `Turn-taking state: ${payload.state}`;
    return;
  }
  if (payload.type === "barge-in") {
    // The buyer talked over the agent, so stop playback immediately. The server has
    // already handed the floor back, so no playback-finished frame is sent.
    stopSpeaking();
    speech.textContent = `Interrupted after ${payload.speech_ms} ms of speech`;
    return;
  }
  if (onReplyAudio(payload)) return;
  if (payload.type !== "utterance") return;
  if (payload.reply) {
    speech.textContent = `Heard: ${payload.transcript} (${payload.turn_latency_ms} ms)`;
    pendingReplyText = payload.reply;
    // When the server is synthesising this reply, speaking it here as well would play
    // two voices over each other. Its audio arrives on this same socket.
    if (!payload.reply_audio) {
      speak(payload.reply, () => audio.sendControl("playback-finished"));
    }
    if (sessionId) {
      api(`/api/simulator/sessions/${sessionId}`)
        .then((body) => renderEvents(body.events))
        .catch(() => {});
    }
    return;
  }
  const label = OUTCOME_LABELS[payload.outcome] || payload.outcome;
  speech.textContent = `${label} (${payload.speech_ms} ms speech, ${payload.frame_count} frames)`;
}

const audio = new AudioTransport(
  ({ queueDepth, dropped }) => {
    diagnostics.textContent = `Queue: ${queueDepth}, dropped: ${dropped}`;
  },
  onSpeechMessage,
  (frame) => player.push(frame),
);

// The API key, when the server is enforcing one. Kept in sessionStorage rather than
// localStorage so closing the tab discards it, and read from ?key= once so a link can
// carry it without it staying in the address bar.
const API_KEY_STORAGE = "pitchbot.apiKey";

function readApiKey() {
  const fromQuery = new URLSearchParams(location.search).get("key");
  if (fromQuery) {
    sessionStorage.setItem(API_KEY_STORAGE, fromQuery);
    history.replaceState(null, "", location.pathname);
    return fromQuery;
  }
  return sessionStorage.getItem(API_KEY_STORAGE) || "";
}

const apiKey = readApiKey();

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(apiKey ? { "X-API-Key": apiKey } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    sessionStorage.removeItem(API_KEY_STORAGE);
    throw new Error("This server requires an API key. Reload with ?key=<your-key>.");
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function renderEvents(events) {
  timeline.replaceChildren();
  for (const event of events) {
    const item = document.createElement("li");
    const text = event.text || JSON.stringify(event.metadata);
    item.textContent = `${event.sequence}. ${event.event_type}: ${text}`;
    timeline.appendChild(item);
  }
}

function renderRecall(payload) {
  recall.replaceChildren();
  const item = document.createElement("li");
  if (!payload) {
    item.textContent = "Not attempted for this turn";
    recall.appendChild(item);
    return;
  }
  if (payload.timed_out) {
    item.textContent = "Recall exceeded its budget; no results";
    recall.appendChild(item);
    return;
  }
  if (!payload.claims.length) {
    item.textContent = `Nothing recalled from ${payload.indexed_claim_count} known claims`;
    recall.appendChild(item);
    return;
  }
  for (const claim of payload.claims) {
    const entry = document.createElement("li");
    // The response carries no session identifier, by design. `prior_session_ordinal`
    // is a position within this response, so two earlier calls stay distinguishable
    // without the browser ever holding a capability for either of them.
    const origin = claim.from_current_session
      ? "this call"
      : `earlier call ${claim.prior_session_ordinal}`;
    const confirmed = claim.confirmed_by_customer ? ", confirmed" : "";
    entry.textContent = `${claim.rank}. ${claim.key}: ${claim.value} (${claim.status}, ${origin}${confirmed})`;
    recall.appendChild(entry);
  }
}

function setError(error) {
  status.textContent = error instanceof Error ? error.message : String(error);
  status.className = "error";
}

document.getElementById("create-session").addEventListener("click", async () => {
  try {
    const body = await api("/api/simulator/sessions", {
      method: "POST",
      body: JSON.stringify({
        lead_ref: document.getElementById("lead-ref").value,
        language: document.getElementById("language").value,
        preview_consent_granted: document.getElementById("preview-consent").checked,
        contact_policy: document.getElementById("preview-eligible").checked ? {
          outreach_allowed: true,
          allowlisted: true,
          dnd_check_passed: true,
          calling_hours_check_passed: true,
          opted_out: false,
        } : {},
      }),
    });
    sessionId = body.session_id;
    status.textContent = `Connected: ${sessionId}`;
    status.className = "";
    [closeButton, sendButton, interruptButton, startAudioButton].forEach((button) => { button.disabled = false; });
    renderRecall(null);
    renderEvents(body.events);
  } catch (error) { setError(error); }
});

closeButton.addEventListener("click", async () => {
  if (!sessionId) return;
  try {
    const response = await fetch(`/api/simulator/sessions/${sessionId}`, { method: "DELETE" });
    if (!response.ok) throw new Error(`Close failed (${response.status})`);
    audio.stop();
    sessionId = null;
    status.textContent = "Session closed";
    renderRecall(null);
    [closeButton, sendButton, interruptButton, startAudioButton, stopAudioButton].forEach((button) => { button.disabled = true; });
  } catch (error) { setError(error); }
});

sendButton.addEventListener("click", async () => {
  if (!sessionId) return;
  try {
    const body = await api(`/api/simulator/sessions/${sessionId}/turns`, {
      method: "POST",
      body: JSON.stringify({
        operation_id: crypto.randomUUID(),
        text: document.getElementById("turn-text").value,
        language: document.getElementById("language").value,
        preview_action: document.getElementById("preview-action").value,
        callback_delay_minutes: Number(document.getElementById("callback-delay").value),
        deck_industry: document.getElementById("deck-industry").value,
        simulated_latency_ms: Number(document.getElementById("latency").value),
        inject_failure: document.getElementById("inject-failure").checked,
      }),
    });
    preview.textContent = body.preview ? JSON.stringify(body.preview, null, 2) : "None";
    renderRecall(body.recall);
    renderEvents(body.events);
    speak(body.reply);
  } catch (error) { setError(error); }
});

interruptButton.addEventListener("click", async () => {
  if (!sessionId) return;
  stopSpeaking();
  try {
    const body = await api(`/api/simulator/sessions/${sessionId}/interrupt`, { method: "POST" });
    renderEvents(body.events);
  } catch (error) { setError(error); }
});

startAudioButton.addEventListener("click", async () => {
  if (!sessionId) return;
  try {
    const started = await audio.start(sessionId);
    if (!started) return;
    startAudioButton.disabled = true;
    stopAudioButton.disabled = false;
  } catch (error) { setError(error); }
});

stopAudioButton.addEventListener("click", () => {
  audio.stop();
  startAudioButton.disabled = false;
  stopAudioButton.disabled = true;
});

document.getElementById("load-replay").addEventListener("click", async () => {
  try {
    const scenario = document.getElementById("scenario").value;
    const body = await api(`/api/simulator/replay/${scenario}`);
    timeline.replaceChildren();
    body.turns.forEach((turn, index) => {
      const item = document.createElement("li");
      item.textContent = `${index + 1}. ${turn.speaker} (${turn.language}): ${turn.text}`;
      timeline.appendChild(item);
    });
  } catch (error) { setError(error); }
});

window.addEventListener("beforeunload", () => audio.stop());
