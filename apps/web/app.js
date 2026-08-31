import { AudioTransport } from "/simulator/audio-transport.js";

let sessionId = null;
const timeline = document.getElementById("timeline");
const preview = document.getElementById("action-preview");
const status = document.getElementById("connection-status");
const closeButton = document.getElementById("close-session");
const sendButton = document.getElementById("send-turn");
const interruptButton = document.getElementById("interrupt");
const startAudioButton = document.getElementById("start-audio");
const stopAudioButton = document.getElementById("stop-audio");
const diagnostics = document.getElementById("audio-diagnostics");

const audio = new AudioTransport(({ queueDepth, dropped }) => {
  diagnostics.textContent = `Queue: ${queueDepth}, dropped: ${dropped}`;
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
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
    [closeButton, sendButton, interruptButton, startAudioButton, stopAudioButton].forEach((button) => { button.disabled = true; });
  } catch (error) { setError(error); }
});

sendButton.addEventListener("click", async () => {
  if (!sessionId) return;
  try {
    const body = await api(`/api/simulator/sessions/${sessionId}/turns`, {
      method: "POST",
      body: JSON.stringify({
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
    renderEvents(body.events);
    if ("speechSynthesis" in window) {
      speechSynthesis.cancel();
      speechSynthesis.speak(new SpeechSynthesisUtterance(body.reply));
    }
  } catch (error) { setError(error); }
});

interruptButton.addEventListener("click", async () => {
  if (!sessionId) return;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
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
