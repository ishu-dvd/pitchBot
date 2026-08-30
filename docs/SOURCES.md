# Source Register

Last reviewed: 2026-08-30.

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

Inclusion here is not selection. The benchmark PR must record repository revision, model license, model-card restrictions, resource use, accuracy, and latency on labeled hardware.
