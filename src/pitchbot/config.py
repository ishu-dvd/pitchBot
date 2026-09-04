from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PITCHBOT_", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    # JSON lines carrying session and turn correlation ids. Off in `local` would be nicer to
    # read by hand, but a format that differs between a developer's machine and production is
    # a format whose redaction nobody has actually seen work, so it is on everywhere.
    log_json: bool = True
    database_url: str = "sqlite:///./data/pitchbot.db"

    enable_telephony: bool = False
    enable_whatsapp: bool = False
    enable_external_network: bool = False
    # INERT: nothing reads this flag. The audio socket in the simulator is always
    # available, so the README's "real-time audio disabled by default" claim is a
    # documented intention, not a code-enforced gate. Before any live channel ships
    # this must either gate the audio socket or be removed. See docs/PROGRESS.md
    # (PR 29, Deferred). The safety-relaxation knobs (require_ai_disclosure,
    # require_dnd_check, require_calling_hours, allowlist_enabled) were removed in
    # PR 29 because the ActionPolicy enforces those gates unconditionally and a
    # switch that disables a mandatory safety gate is strictly less safe than none.
    enable_real_time_audio: bool = False
    enable_hosted_demo: bool = False
    enable_durable_history: bool = False
    durable_history_digest_key: str = ""

    allowed_contacts: str = Field(default="")

    # --- API access control -----------------------------------------------------------
    # Comma-separated "<name>:<secret>" pairs, for example
    # "web:a-long-random-string,ops:another-one". Empty means no authentication, which is
    # accepted ONLY when app_env is "local" - see validate_api_access below. Until this
    # existed the API had ten unauthenticated HTTP endpoints and an unauthenticated
    # WebSocket.
    api_keys: str = ""
    # A turn costs seconds of CPU, so the limit that matters is small and per-credential.
    # Burst covers a client opening a session and immediately taking a turn; the sustained
    # rate is what stops a loop.
    api_rate_limit_burst: int = 20
    api_rate_limit_per_second: float = 2.0

    lead_recall_top_k: int = 3
    lead_recall_deadline_ms: int = 150
    lead_recall_failure_budget: int = 3

    timezone: str = "Asia/Kolkata"
    max_call_minutes: int = 12
    max_turns: int = 80

    # --- Speech providers -------------------------------------------------------------
    # Both default to the behaviour that shipped before PR 33/34 existed: a byte-size mock
    # detector, and NO transcriber at all, so spoken utterances are reported as
    # `transcriber-unavailable` rather than invented. No provider has been benchmarked and
    # selected (ADR-0004), so turning one on is a deliberate local act.
    #
    # A provider named here that is not installed is a **startup error**, not a silent
    # downgrade. Falling back to the mock would leave an operator believing speech works
    # when it does not, which is worse than refusing to start.
    speech_vad_provider: str = "mock"
    speech_vad_mode: int = 2
    speech_vad_sample_rate_hz: int = 16_000

    speech_stt_provider: str = "none"
    speech_stt_model: str = "small"
    speech_stt_device: str = "cpu"
    speech_stt_compute_type: str = "int8"
    speech_stt_beam_size: int = 1
    # Empty means auto-detect. Whisper labels anything, including silence, so the adapter
    # reports UNKNOWN below its detection-probability floor rather than guessing.
    speech_stt_language: str = ""
    # Weights are never fetched by PitchBot unless this is explicitly enabled.
    speech_stt_download_root: str = ""
    speech_stt_allow_download: bool = False
    # Identify the language while the buyer is still talking, so transcription does not
    # have to. Measured 2026-09-05: the shipped auto-detect default runs the Whisper encoder
    # twice - once to find the language, once to decode it - costing 3,857 ms against
    # 2,235 ms when the language is already known. Detection cost is flat regardless of how
    # much audio it is given (Whisper pads to 30 s), so the only way to win is to start it
    # sooner, overlapping the buyer's own speech.
    #
    # 2.0 s is the shortest measured-SAFE prefix, not merely the shortest useful one: at
    # 1.5 s Telugu was identified as Malayalam at probability 0.90. Set to 0 to disable and
    # get exactly the behaviour that shipped before this existed.
    speech_stt_early_detection_seconds: float = 2.0
    # Stricter than the reporting floor because this decides whether to *impose* a language
    # on the decoder rather than whether to report one. A wrong imposed language does not
    # produce a broken transcript, it produces a fluent translation, and no confidence
    # signal distinguishes the two.
    speech_stt_early_detection_min_probability: float = 0.7
    # Languages this transcriber is known not to be able to transcribe, so an utterance in
    # one is declined instead of transcribed into text nobody should act on.
    #
    # Telugu is the measured case and the default. Whisper `small` returns nonsense in every
    # decoder configuration tried -- "మేము రిటైల్ షాప్ నడుపుతాము" came back as "మరింరIsn
    # claiming the jammals from the charity sponsor" -- and takes 37,533 ms to do it, which
    # is also the worst latency number in this project. Understanding scores at or below
    # guessing in Telugu and is already gated out separately.
    #
    # Only applied when the language was CONFIDENTLY identified by early detection, so an
    # uncertain guess still transcribes. Set to empty to restore the previous behaviour.
    # Telugu TEXT turns are unaffected; this is about speech only.
    speech_stt_unsupported_languages: str = "te"

    # Text-to-speech is off by default, and for a different reason than the other two.
    # The browser client already speaks replies with the Web Speech API, so this is not a
    # missing capability but a *replacement* for one whose voices vary by browser, are
    # frequently absent for Hindi, and on several platforms are synthesised by a remote
    # service. Turning this on moves synthesis onto the server, where the voice, its
    # license and its locality are known. Voices are operator-supplied files; PitchBot
    # never downloads them.
    speech_tts_provider: str = "none"
    speech_tts_voice_dir: str = ""
    # "en=en_US-joe-medium,hi=hi_IN-pratham-medium" - a language maps to exactly one voice
    # and there is no fallback, because an unmapped language served by the wrong voice
    # produces fluent audio in the wrong language rather than an error.
    speech_tts_voices: str = ""
    # Every Piper Hindi voice reviewed on 2026-09-03 is non-commercial or unresolved, so a
    # bilingual local evaluation needs this. It stays off by default: PitchBot is a sales
    # assistant, and shipping a non-commercial voice in one would be a licensing breach.
    speech_tts_allow_non_commercial: bool = False
    speech_tts_deterministic: bool = False

    # --- Local language model ---------------------------------------------------------
    # Off by default, and for a stronger reason than the speech providers: measured on this
    # hardware a correct answer costs ~6.7 s per turn, on top of ~2.8 s of speech latency.
    # The deterministic planner produces a relevant reply with no model at all, so this is
    # an improvement to *understanding* for deployments that can afford it - not a
    # dependency the conversation has.
    llm_provider: str = "none"
    llm_model_dir: str = ""
    # The UPSTREAM model id, not the conversion repository the files came from: a quantised
    # re-upload does not relicense the weights, and the licence gate reads this.
    llm_model_id: str = ""
    llm_allow_non_commercial: bool = False

    @model_validator(mode="after")
    def validate_speech_providers(self) -> Settings:
        """Reject an unrecognised provider name at import, not at first spoken turn.

        Only the *name* is validated here; whether the optional extra is installed is
        checked by :mod:`pitchbot.speech.providers` when the service is built. Settings
        must not import adapters.
        """

        valid_vad = {"mock", "webrtc"}
        if self.speech_vad_provider not in valid_vad:
            raise ValueError(
                f"speech_vad_provider must be one of {sorted(valid_vad)}, "
                f"received {self.speech_vad_provider!r}"
            )
        valid_stt = {"none", "faster-whisper"}
        if self.speech_stt_provider not in valid_stt:
            raise ValueError(
                f"speech_stt_provider must be one of {sorted(valid_stt)}, "
                f"received {self.speech_stt_provider!r}"
            )
        if not 0 <= self.speech_vad_mode <= 3:
            raise ValueError("speech_vad_mode must be between 0 and 3")
        if self.speech_stt_beam_size < 1:
            raise ValueError("speech_stt_beam_size must be positive")
        if self.speech_stt_early_detection_seconds < 0:
            raise ValueError("speech_stt_early_detection_seconds must not be negative")
        if not 0.0 <= self.speech_stt_early_detection_min_probability <= 1.0:
            raise ValueError("speech_stt_early_detection_min_probability must be between 0 and 1")
        for entry in (item.strip() for item in self.speech_stt_unsupported_languages.split(",")):
            if entry and entry not in {"en", "hi", "te", "mixed", "unknown"}:
                raise ValueError(
                    f"speech_stt_unsupported_languages entry {entry!r} must be one of "
                    "'en', 'hi', 'te', 'mixed', 'unknown'"
                )
        if self.speech_stt_language not in {"", "en", "hi", "mixed", "unknown"}:
            raise ValueError(
                "speech_stt_language must be empty (auto-detect) or one of "
                "'en', 'hi', 'mixed', 'unknown'"
            )
        valid_tts = {"none", "piper"}
        if self.speech_tts_provider not in valid_tts:
            raise ValueError(
                f"speech_tts_provider must be one of {sorted(valid_tts)}, "
                f"received {self.speech_tts_provider!r}"
            )
        if self.speech_tts_provider != "none":
            # A synthesiser with no voice mapped can never speak, so accepting the
            # configuration would produce a server that is silently mute rather than one
            # that refuses to start.
            if not self.speech_tts_voice_dir.strip():
                raise ValueError(
                    "speech_tts_voice_dir must name the directory holding the .onnx "
                    "voice files when speech_tts_provider is enabled"
                )
            if not self.speech_tts_voices.strip():
                raise ValueError(
                    "speech_tts_voices must map at least one language to a voice, "
                    "for example 'en=en_US-joe-medium', when a provider is enabled"
                )
        for entry in (item.strip() for item in self.speech_tts_voices.split(",")):
            if not entry:
                continue
            language, separator, voice_id = entry.partition("=")
            if not separator or not language.strip() or not voice_id.strip():
                raise ValueError(
                    f"speech_tts_voices entry {entry!r} must be '<language>=<voice-id>'"
                )
            if language.strip() not in {"en", "hi", "mixed", "unknown"}:
                raise ValueError(
                    f"speech_tts_voices language {language.strip()!r} must be one of "
                    "'en', 'hi', 'mixed', 'unknown'"
                )
        valid_llm = {"none", "onnx-genai"}
        if self.llm_provider not in valid_llm:
            raise ValueError(
                f"llm_provider must be one of {sorted(valid_llm)}, received {self.llm_provider!r}"
            )
        if self.llm_provider != "none":
            # A model with no directory or no id can never answer, so accepting the
            # configuration would produce a server that silently never consults it.
            if not self.llm_model_dir.strip():
                raise ValueError(
                    "llm_model_dir must name the ONNX Runtime GenAI model directory when "
                    "llm_provider is enabled"
                )
            if not self.llm_model_id.strip():
                raise ValueError(
                    "llm_model_id must name the upstream model, for example "
                    "'Qwen/Qwen2.5-0.5B-Instruct', so its licence can be checked"
                )
        return self

    @model_validator(mode="after")
    def validate_api_access(self) -> Settings:
        """Refuse to start an unauthenticated server anywhere but a developer's machine.

        The alternative - defaulting to authentication everywhere - would break the local
        demo for everyone, and a gate people routinely switch off is not a gate. The
        alternative in the other direction, warning and continuing, is how services end up
        publicly readable: the warning scrolls past once at startup and is never seen again.

        So the two states are exactly: `local` may run open, and everything else must have
        a credential or it does not boot.
        """

        from pitchbot.security.credentials import parse_api_keys

        entries = parse_api_keys(self.api_keys)
        if not entries and self.app_env != "local":
            raise ValueError(
                f"api_keys must define at least one '<name>:<secret>' credential when "
                f"app_env is {self.app_env!r}. Only app_env='local' may run without "
                "authentication, because every endpoint - including the audio socket - "
                "is otherwise open to anyone who can reach the port"
            )
        if self.api_rate_limit_burst < 1:
            raise ValueError("api_rate_limit_burst must be at least 1")
        if self.api_rate_limit_per_second <= 0:
            raise ValueError("api_rate_limit_per_second must be positive")
        return self

    @model_validator(mode="after")
    def validate_durable_history(self) -> Settings:
        if not self.enable_durable_history:
            return self
        if len(self.durable_history_digest_key) != 64:
            raise ValueError(
                "durable_history_digest_key must be 64 hexadecimal characters "
                "when durable history is enabled"
            )
        try:
            bytes.fromhex(self.durable_history_digest_key)
        except ValueError as exc:
            raise ValueError(
                "durable_history_digest_key must be 64 hexadecimal characters "
                "when durable history is enabled"
            ) from exc
        return self


settings = Settings()
