# Source Register

Last reviewed: 2026-09-01.

Provider capabilities, policies, pricing, free tiers, and regulations change. Revalidate every applicable source and record the review date before implementing or enabling a live adapter. Links are references, not legal approval.

## Communication platforms

- [Meta WhatsApp Platform documentation](https://developers.facebook.com/docs/whatsapp/) — official API capabilities and onboarding.
- [Meta WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api/) — official hosted messaging API documentation.
- [WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy) — platform messaging requirements.
- [Twilio Voice documentation](https://www.twilio.com/docs/voice) — example official PSTN provider integration path; not selected by this ADR.

## India policy and privacy

- [Telecom Regulatory Authority of India](https://www.trai.gov.in/) — official portal from which legal review must retrieve the current commercial-communication and DND instruments.
- [Ministry of Electronics and Information Technology](https://www.meity.gov.in/) — official portal from which legal review must retrieve current Indian digital and data-protection materials. Automated access can be restricted.

These portal links are not legal citations. The project must identify the applicable instruments and obtain qualified legal review for the intended outreach model, jurisdictions, recording, retention, and data-subject handling.

## Hosting and application runtime

- [GitHub Pages documentation](https://docs.github.com/en/pages) — static hosting capabilities.
- [GitHub Actions documentation](https://docs.github.com/en/actions) — CI automation; not an application runtime.
- [Hugging Face Spaces overview](https://huggingface.co/docs/hub/spaces-overview) — candidate constrained demo hosting; current quotas and hardware must be checked.
- [FastAPI documentation](https://fastapi.tiangolo.com/) — API framework used by the foundation.

## Security guidance

- [OWASP Top 10 for Large Language Model Applications](https://genai.owasp.org/llm-top-10/) — model/application threat categories.
- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) — guarded URL-fetching controls.
- [GitHub secret scanning documentation](https://docs.github.com/en/code-security/secret-scanning) — repository secret controls.

## Open-source candidates requiring later benchmark and license review

- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)
- [`whisper.cpp`](https://github.com/ggml-org/whisper.cpp)
- [`silero-vad`](https://github.com/snakers4/silero-vad)
- [`llama.cpp`](https://github.com/ggml-org/llama.cpp)
- [`sqlite-vec`](https://github.com/asg017/sqlite-vec)
- [`hnswlib`](https://github.com/nmslib/hnswlib)
- [FAISS](https://github.com/facebookresearch/faiss)
- [BGE-M3](https://huggingface.co/BAAI/bge-m3)

Inclusion here is not selection. The benchmark PR must record repository revision, model license, model-card restrictions, resource use, accuracy, and latency on labeled hardware.

## Reviewed speech providers

- [`piper1-gpl`](https://github.com/OHF-Voice/piper1-gpl) — text-to-speech runtime, reviewed
  2026-09-03. **GPL-3.0-or-later**; bundles `espeak-ng` data. Landed as the optional
  `piper-tts` extra in PR 33 and never vendored or redistributed. No provider selected.
- [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) — voice weights,
  each with its own license taken from its training data. Reviewed per voice on 2026-09-03;
  **every published `hi_IN` voice is non-commercial or carries a license that could not be
  retrieved**, and `en_US-amy-low` inherits CC BY-NC-SA 4.0 through its RyanSpeech base.
  Commercially usable voices found: `en_US-joe-medium` (CC0-1.0),
  `en_US-libritts_r-medium` and `en_GB-alba-medium` (CC BY 4.0). Full table in
  [BENCHMARKS.md](BENCHMARKS.md).

A voice's license follows its training data **through finetuning**, so a finetuned voice's
base model has to be chased rather than trusting the voice's own card alone. An
unretrievable license is recorded as not permitting commercial use.

## Evaluation and local observability

- [OpenTelemetry documentation](https://opentelemetry.io/docs/what-is-opentelemetry/) — vendor-neutral future trace and metric contracts.
- [Arize Phoenix documentation](https://arize.com/docs/phoenix) — optional local trace/evaluation viewer candidate.
- [MLflow GenAI documentation](https://mlflow.org/docs/latest/genai/) — optional local run/evaluation tracking candidate.

These services are not dependencies and no exporter is enabled. The portable JSON snapshot and static local report remain the baseline.
