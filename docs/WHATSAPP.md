# WhatsApp: how it works, what is free, and how to test it without an account

Everything here was read from Meta's own documentation on **2026-09-07** and is quoted
rather than recalled. Meta may change pricing *"only on the 1st day of each quarter"*, so
treat every number as dated and re-check it before relying on it.

---

## The one rule that decides everything

**Replying is free. Speaking first is not.**

When a WhatsApp user messages you *or calls you*, a 24-hour **customer service window**
opens. Inside it you may send free-form messages at no charge. Outside it, only an approved
**template** will deliver at all, and templates are what Meta bills for.

> "When a WhatsApp user messages you or calls you, a 24-hour timer called a customer
> service window starts. If the user messages or calls you again before the timer expires,
> the timer resets to 24 hours."
> — [send-messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)

This is why the inbound webhook is **not** an optional half of the integration. Without it
you never know a window is open, and a build with only the outbound half can do nothing
that does not cost money.

---

## What is free

| Free | Rule |
|---|---|
| Any non-template message inside an open 24h window | *"All non-template messages are free... Non-template messages can only be sent within an open customer service window."* |
| A **utility** template inside that window | *"Utility templates delivered within an open customer service window are free."* |
| **Any** message inside a 72h Free Entry Point window | *"FEP windows remain open for 72 hours. While open, you can send any type of message to the user at no charge."* |
| Every **user-initiated** call | *"All user-initiated calls are free."* |
| Service messages, with no monthly cap | *"Effective November 1, 2024 – Service conversations are now free for all businesses."* |

Two of these are counter-intuitive and both were wrong in the first draft of
`pitchbot/whatsapp/pricing.py`:

1. **Not every template costs money.** A *utility* template inside the window is free; a
   *marketing* template in the same window is charged.
2. **There is a second, wider free window.** If the customer arrived via a
   click-to-WhatsApp ad or a Facebook Page button and you answer within 24 hours, a
   72-hour window opens in which *everything* is free — the only circumstance in which a
   marketing template costs nothing. Note the trap: the FEP window and the service window
   run independently, and Meta's Android/iOS apps are supported for this while *"our
   desktop and web apps are not"*.

Pricing moved from per-conversation to **per-message on 1 July 2025**:
> "Effective July 1, 2025, Meta charges on a per-message basis: You are only charged when a
> template message is delivered."

---

## What it costs in India

From Meta's INR rate card, header *"effective July 1, 2026"*, read 2026-09-07:

| Category | INR / message | USD / message |
|---|---|---|
| Marketing | **0.8631** | 0.0118 |
| Utility | **0.1150** | 0.0014 |
| Authentication | **0.1150** | 0.0014 |
| Authentication-International | 2.4971 | 0.0304 |
| **Service** | **free** | **free** |

Business-initiated calling: **INR 0.3885 per minute** (0–50,000 min/month tier), billed in
six-second pulses, rounded up — *"a 56-second call (9.33 pulses) would be counted as 10
pulses"* — and *"a valid payment method is required to place calls."*

**India billing note:** billing localisation started 1 Jan 2026 for India-based customers;
WABAs must migrate to INR by **31 Dec 2026** or messages stop being delivered from 1 Jan
2027.

---

## Calling: the short answer

| | Free? |
|---|---|
| User calls you | **Yes, always.** No payment method needed. |
| You call the user | **No.** Charged per minute, and a payment method is mandatory. |

There is a further gate on outbound calling in production: *"The business must have a daily
messaging limit of at least 2,000 unique recipients."* New business portfolios start at
**250**, so a solo developer cannot reach it without verification or volume. That gate is
waived on test numbers for testing only.

Worth knowing: even *asking* permission to call costs money — *"Call permission request
messages are subject to per-messaging pricing."*

And a useful free lever: **a call opens or refreshes the free messaging window**
— *"the customer service window now also starts or refreshes for calls."* An inbound call
costs nothing and buys 24 hours of free messaging.

**Verdict: messaging-only can be entirely free. Outbound calling cannot be.**

---

## The zero-cost path

### Phase 0 — offline, no account, indefinite (this is where 90% of the work happens)

Everything in `pitchbot.whatsapp` runs with no Meta app, no business account, no phone
number and no network:

```bash
pitchbot-whatsapp status     # what is configured, and what would be billed
pitchbot-whatsapp demo       # a full send through the local fake, with every gate firing
pitchbot-whatsapp inbound    # feed a webhook payload in, watch the free window open
```

`FakeGraphApi` implements the parts of the Graph API PitchBot uses, and
`WhatsAppCloudAdapter` is the *real* client pointed at it through httpx's in-process ASGI
transport — so the HTTP layer, auth header, request body and error handling are all
genuinely executed and no socket is opened.

The fake's contract is taken from the published spec rather than from the client, because a
fake and a client written from the same recollection agree with each other and prove
nothing. `recipient_type` is the clearest example: **required by the spec, omitted by
almost every published example.**

### Phase 1 — real webhooks, still no messages (free)

Create a Meta app with the *"Connect with customers through WhatsApp"* use case. A test
business phone number is issued automatically:

> "When you complete the steps in the Get Started document, a test business phone number is
> generated and registered for you automatically."

Prerequisites are only a Facebook account, developer registration and a WhatsApp-enabled
device — **no payment method, no business verification**. A Meta Business Portfolio is
created during app creation.

Point the webhook at a free HTTPS endpoint and use the Dashboard's **Test** button, which
fires a synthetic webhook without sending anything.

Webhook requirements, all quoted from
[create-webhook-endpoint](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint):

- *"must have a valid TLS or SSL digital security certificate... Self-signed certificates
  are not supported."*
- GET handshake: compare `hub.verify_token`, then *"respond with HTTP status 200 and the
  `hub.challenge` value"*. Anything else and *"webhooks will not be sent."*
- POST authenticity: HMAC-SHA256 over the payload with the app secret, compared against
  `X-Hub-Signature-256`.
- *"delivery is retried immediately, then a few more times with decreasing frequency over
  the next 7 days... Your server should handle deduplication."*
- *"There are no APIs for fetching historical webhook data."*

Free ways to expose a local server:

| Tool | Free | Note |
|---|---|---|
| **Render.com** | yes | Meta officially publishes a test receiver for it. Stable URL, so you verify the token once. Best choice. |
| **Cloudflare Tunnel / TryCloudflare** | yes | `cloudflared tunnel --url http://localhost:8000`. Random URL each run, so re-verify each time. |
| ngrok | unverified | A free tier is listed but the limits could not be read. |

### Phase 2 — real messages to yourself (free)

Message your own phone from the test number, reply from your phone to open the window, then
send free-form messages — free by rule. Generate a System User permanent token immediately:
the default one *"expires quickly and is not suitable for development purposes."*

Limits that apply: new portfolios have a messaging limit of **250** unique recipients/day
and are *"initially capped at two registered business phone numbers."*

> ⚠️ **Could not verify:** the commonly-cited "5 recipient numbers" limit on test numbers.
> No official statement was found in current docs. Nor could the existence of a fixed free
> message allowance, or a test-number expiry, be confirmed.

### Phase 3 — inbound calling only (free)

Enable calling on the test number in call settings — *"Calling is disabled by default on
test numbers."* Test **user-initiated** calls only. Never trigger a business-initiated
call; that needs a payment method.

---

## What NOT to do

**Do not automate the WhatsApp Business app or WhatsApp Web.** The Business Terms are
explicit:

> "Company must not directly, indirectly, or through automated or other means: ... (g)
> develop or use any applications that interact with our Business Services without our
> prior written consent"
> — [WhatsApp Business Terms §5](https://www.whatsapp.com/legal/business-terms)

Any library that drives WhatsApp Web or reimplements the client protocol falls under this.
It is the classic cause of number bans. There is **no** legitimate programmatic access to
the free Business app.

Other constraints worth knowing:

- Registering a number for the Cloud API **destroys its normal WhatsApp use**: *"Numbers
  already in use with WhatsApp cannot be registered unless they are deleted first."*
- Opt-in is mandatory: *"You may only contact people on WhatsApp if: (a) they have given
  you their mobile phone number; and (b) you have received opt-in permission."*
- Automation needs an escape hatch: *"You may use automation when responding during the
  24-hour window, but must also have available prompt, clear, and direct escalation
  paths."*

---

## Business solution providers

Checked for a genuinely free developer sandbox, not a card-gated trial:

| Provider | Free sandbox | Detail |
|---|---|---|
| **Twilio** | **yes — the only real one** | Shared number `+14155238886`, no WABA needed, *"unlimited messages... for as long as you need"*. Catches: recipients must send `join <code>`; *"session expires three days after joining"*; only 3 pre-approved templates, no custom ones; 1 message / 3 seconds; trial accounts get 100 free WhatsApp messages then billing applies. |
| 360dialog | no | €49/number/month minimum. |
| AiSensy | credit only | ₹50 signup credit, *"up to 9 users in one go"*. |
| Wati | no | Paid, priced on monthly active contacts. |
| Gupshup / Interakt | unverified | Pricing pages did not render. |
| Meta Embedded Signup sandbox | **useless here** | *"Cannot send or receive messages."* |
| Meta Calling sandbox | not available | *"Sandbox accounts are only available to Tech Partners."* |

---

## How this repository implements it

| Module | Role |
|---|---|
| `pitchbot.whatsapp.pricing` | Which messages are free. Structural, not a price list — the decision never depends on a number that moves quarterly. |
| `pitchbot.whatsapp.fake_graph` | The local Graph API stand-in. Contract taken from the spec. |
| `pitchbot.whatsapp.webhook` | Inbound: verify handshake, HMAC over **raw** bytes, deduplication, always 200. |
| `pitchbot.adapters.whatsapp_cloud` | The real client, implementing the existing `WhatsAppAdapter` protocol. |
| `pitchbot.whatsapp.cli` | `pitchbot-whatsapp status / demo / inbound`. |

Three independent gates stand between the code and a charge:

1. **Network policy** — `enable_external_network` is off by default and checked first.
2. **Cost posture** — `FREE_ONLY` by default; a chargeable message is *refused with the
   reason*, not sent.
3. **Idempotency** — Graph has no idempotency key, so a retried send would deliver twice.
   ADR-0003 requires idempotent provider retries, so the key is held client-side.

`httpx` is an optional extra (`pip install "pitchbot[whatsapp]"`). The package imports and
the whole suite passes without it, because the fake is driven through the in-process ASGI
transport the dev extra already provides. Only reaching Meta needs it installed
deliberately — which is the point, since that is the only path on which a message can be
billed.
