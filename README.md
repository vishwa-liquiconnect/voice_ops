# Voice Ops

A multilingual, AI-assisted **driver operations platform** for intercity bus
fleets, built as a Frappe / ERPNext app. Voice Ops turns ordinary phone calls
into structured operational data:

- Pre-departure / post-trip **checklist calls** to drivers, driven by an IVR.
- **Inbound driver query** calls that get summarised and routed to the right
  manager.
- **Voicemail capture** + a periodic digest emailed and WhatsApped to ops.
- **Feedback calls** placed after every completed trip.

Speech is transcribed by **Sarvam AI**, summarised by **Claude** (via the
sibling `fms_ai` app), and every business decision is made deterministically
inside ERPNext — AI never decides blockers or escalations.

> Architectural source of truth: see [`voice_ops/CLAUDE.md`](voice_ops/CLAUDE.md).
> If anything below contradicts CLAUDE.md, CLAUDE.md wins.

---

## Table of contents

- [Why this app exists](#why-this-app-exists)
- [High-level architecture](#high-level-architecture)
- [Runtime flows](#runtime-flows)
  - [1. Outbound pre-departure checklist](#1-outbound-pre-departure-checklist)
  - [2. Post-trip checklist](#2-post-trip-checklist)
  - [3. Post-trip feedback call](#3-post-trip-feedback-call)
  - [4. Inbound driver query](#4-inbound-driver-query)
  - [5. Voicemail capture + digest](#5-voicemail-capture--digest)
- [Telephony providers (Twilio vs Exotel)](#telephony-providers-twilio-vs-exotel)
- [Directory map](#directory-map)
- [DocTypes](#doctypes)
- [Voice Ops Settings — the configuration single](#voice-ops-settings--the-configuration-single)
- [Scheduler events](#scheduler-events)
- [Document hooks](#document-hooks)
- [Installation](#installation)
- [Local development](#local-development)
- [Contributing](#contributing)
- [License](#license)

---

## Why this app exists

Driver and operations teams in intercity bus fleets rely on phone calls — a
driver confirms readiness before a trip, an operations manager calls a driver
back to debrief, a stranded driver calls in with a problem. All of that
historically lives in WhatsApp messages and ops memory.

Voice Ops makes that information **first-class data in ERPNext**:

- Each driver call has a recording, a transcript, a normalized response per
  checklist question, an automatically-evaluated blocker/warning verdict, and
  an audit trail of any manual review.
- Each inbound query becomes a `Driver Query` document plus an ERPNext
  `Issue`, with the route manager and MD notified on WhatsApp and email.
- Each voicemail surfaces in a digest with caller name, vehicle, and an
  AI-written summary — ops doesn't listen back individually.

Everything is built so a junior developer or an ops admin can extend it:
checklist questions are config, business rules are explicit dictionaries in
[`services/rule_evaluator.py`](voice_ops/services/rule_evaluator.py), and
language behaviour is config-driven via Voice Ops Settings.

---

## High-level architecture

```
       Driver phone                      Operations Desk
            │                                   │
            ▼                                   ▼
      ┌─────────────┐                  ┌─────────────────┐
      │ Twilio /    │ ◀── webhooks ──▶ │ ERPNext / Frappe │
      │ Exotel IVR  │                  │   (voice_ops)    │
      └─────────────┘                  └────────┬────────┘
                                                │ enqueues
                                                ▼
                                       ┌─────────────────┐
                                       │ RQ worker queue │
                                       │  (background    │
                                       │   jobs)         │
                                       └────────┬────────┘
                                                │
                          ┌─────────────────────┼──────────────────────┐
                          ▼                     ▼                      ▼
                   ┌────────────┐       ┌──────────────┐        ┌───────────┐
                   │ Sarvam AI  │       │ Claude (via  │        │   S3      │
                   │  (STT)     │       │   fms_ai)    │        │ (recordings)│
                   └────────────┘       └──────────────┘        └───────────┘
```

Principles (full list in `CLAUDE.md`):

| Principle | Where it shows up |
| --- | --- |
| ERPNext is the **system of record** | All trip / crew / checklist / issue data lives in doctypes. |
| Webhook handlers stay **lightweight** | `api/*_webhook.py` only writes state + builds XML, then enqueues. |
| All async work uses **Frappe background jobs (RQ)** | Every heavy step is dispatched via `frappe.enqueue`. |
| **AI does not make business decisions** | Sarvam transcribes, Claude summarises, but blockers / escalations are deterministic dicts in `services/rule_evaluator.py` and `services/escalation.py`. |
| **No silent failures** | External calls log via `frappe.log_error`; low-confidence transcripts route to the review queue (Checklist Run with `Needs Review` status). |
| Telephony is **provider-agnostic** | `services/telephony.py` is the abstraction; both Twilio and Exotel can be plugged into any flow via Voice Ops Settings. |

---

## Runtime flows

### 1. Outbound pre-departure checklist

Triggered automatically by [`jobs/auto_trigger.py`](voice_ops/jobs/auto_trigger.py)
every 5 minutes, or manually from a `Checklist Run` form (the "Trigger Call"
button).

```
auto_trigger ─► initiate_call (Twilio/Exotel) ─► driver picks up
                                                     │
                                                     ▼
                                        IVR plays language menu (DTMF)
                                                     │
                                                     ▼
                                  webhook ─► language_callback (api/*_webhook.py)
                                                     │   stores language,
                                                     │   schedules pretranslate
                                                     ▼
                              IVR plays each question + records the answer
                                                     │
                                                     ▼
                                  webhook ─► recording_callback (per question)
                                                     │   stores response.recording_url
                                                     │
                          ┌──────────────────────────┴──────────────────────────┐
                          ▼ (last question)                                     ▼
              enqueue process_call_recording             ask next question, repeat
                          │
                          ▼
        download recording → Sarvam transcribe → normalize via transcript_processor
                          ▼
            evaluate_checklist (rule_evaluator)  → blockers / warnings flagged
                          ▼
            process_escalations (escalation.py) → block trip / alert ops / etc.
                          ▼
            Checklist Run.status = Completed | Needs Review | Approved | Rejected
```

Key code paths:

- Trigger: `voice_ops.jobs.auto_trigger.check_and_trigger`
- IVR (Twilio): `voice_ops.api.twilio_webhook`
- IVR (Exotel): `voice_ops.api.exotel_webhook`
- Processing: `voice_ops.jobs.process_call_recording`
- Evaluation: `voice_ops.services.rule_evaluator`

### 2. Post-trip checklist

Same pipeline as the pre-departure flow, just triggered by
[`jobs/post_trip_trigger.py`](voice_ops/jobs/post_trip_trigger.py) when a Trip
Roster Assignment moves to a terminal state. Uses a different Checklist
Template (the ~25 post-trip parameters).

### 3. Post-trip feedback call

Triggered by [`jobs/feedback_trigger.py`](voice_ops/jobs/feedback_trigger.py).
Two implementations live side-by-side in
[`services/feedback_call.py`](voice_ops/services/feedback_call.py):

- **App flow** (legacy) — Exotel runs a pre-built App Builder flow keyed by
  `exotel_feedback_flow_app_id`.
- **ExoML flow** (preferred) — that setting is left blank, Exotel fetches
  [`api.feedback.feedback_exoml`](voice_ops/api/feedback.py) at call time and
  plays the dynamic XML.

The resulting recording lands on a `Call Log` with `type_of_call = "Feedback"`
and flows through the same Sarvam → `summary` pipeline as voicemails.

### 4. Inbound driver query

A driver dials the configured DID. The provider hits one of the inbound
webhook entry points:

- Twilio: `voice_ops.api.inbound_webhook.inbound_twiml`
- Exotel: `voice_ops.api.exotel_webhook.inbound_exoml`

The IVR steps the driver through three short recordings: **name → bus → query**.
When all three are captured, [`jobs/process_driver_query.py`](voice_ops/jobs/process_driver_query.py)
runs:

1. Downloads all 3 recordings.
2. Transcribes each via Sarvam (translate mode → English).
3. Identifies the caller from the phone number → Trip Crew Member.
4. Resolves today's trip → route manager (`operations_incharge`).
5. Best-effort vehicle match from the verbal bus info.
6. Generates a 1-line summary via Claude.
7. Notifies MD + route manager over WhatsApp, email, and in-app
   ([`services/query_notification.py`](voice_ops/services/query_notification.py)).

### 5. Voicemail capture + digest

Any call that reaches voicemail (no driver answer, default ERPNext
behaviour) lands on a `Call Log` with `type_of_call = "Voicemail"`. The
`call_log_handler` hooks pick the recording up, transcribe it via Sarvam,
and store the transcript in `summary`.

Every 5 minutes [`jobs/voicemail_digest.py`](voice_ops/jobs/voicemail_digest.py)
checks whether it's time to send a digest. If yes, it creates a
`Voicemail Digest Log` row and calls its `generate()` + `send_now()` methods
— the same methods the desk "Generate" / "Send Now" buttons call. The digest
includes caller name, vehicle, time, and the Claude-written summary; it ships
over email (HTML table + PDF attachment) and WhatsApp (compact text).

Daily at 03:00 [`jobs/recording_retention.py`](voice_ops/jobs/recording_retention.py)
deletes audio files older than 30 days. Transcripts, summaries, and the
Issue records all stay.

---

## Telephony providers (Twilio vs Exotel)

Voice Ops abstracts both providers behind
[`services/telephony.py`](voice_ops/services/telephony.py). You configure
which provider runs which flow independently in **Voice Ops Settings**:

| Setting | Picks provider for | Webhook target |
| --- | --- | --- |
| `outbound_telephony_provider` | Checklist calls (pre + post) | `api/twilio_webhook.py` or `api/exotel_webhook.py` |
| `inbound_telephony_provider` | Driver query calls | `api/inbound_webhook.py` or `api/exotel_webhook.py` |
| `feedback_telephony_provider` | Feedback calls | `api/feedback.py` (Exotel) or static App flow |

Recordings end up on a `Call Log` regardless of provider. The
`Call Log.links` Dynamic Link child table is populated at transcription time
so downstream consumers (digest, reporting, manual review) can read caller /
vehicle context off the Call Log without re-resolving the phone number.

> **Note:** Exotel customisations always live in `voice_ops` as overrides,
> never in the `exotel_integration` repo (see project rule in
> `~/.claude/projects/-home-vishwa-Public-FRAPPE-fms-apps/memory/`).

---

## Directory map

```
voice_ops/
├── README.md                 ← this file
├── CLAUDE.md                 ← architectural source of truth
├── pyproject.toml            ← packaging metadata
├── voice_ops/
│   ├── hooks.py              ← Frappe app manifest (hooks + cron)
│   ├── setup.py              ← `after_install`: seeds templates + call types
│   ├── modules.txt           ← single "Voice Ops" module
│   ├── patches.txt           ← migration patches (idempotent)
│   │
│   ├── api/                  ← HTTP entrypoints (webhooks + whitelisted methods)
│   │   ├── twilio_webhook.py     – outbound checklist (Twilio)
│   │   ├── exotel_webhook.py     – outbound checklist + inbound query (Exotel)
│   │   ├── inbound_webhook.py    – inbound query (Twilio)
│   │   ├── feedback.py           – feedback call ExoML + trigger
│   │   ├── checklist.py          – manual "Trigger Call" button endpoint
│   │   └── call_log.py           – recording proxy + manual transcribe
│   │
│   ├── services/             ← reusable building blocks (called by api/ and jobs/)
│   │   ├── telephony.py          – Twilio/Exotel abstraction, XML builders
│   │   ├── twilio_service.py     – thin backwards-compat wrapper
│   │   ├── sarvam.py             – STT (sync REST + batch SDK)
│   │   ├── summarizer.py         – Claude summaries (via fms_ai)
│   │   ├── transcript_processor.py – yes/no/numeric normalization
│   │   ├── rule_evaluator.py     – deterministic blockers + warnings
│   │   ├── escalation.py         – escalation matrix actions
│   │   ├── feedback_call.py      – outbound Exotel feedback call
│   │   ├── language.py           – BCP-47 resolution + translation
│   │   ├── ack_sender.py         – post-Issue email + WhatsApp acks
│   │   ├── caller_lookup.py      – phone → contact / employee / vehicle
│   │   ├── query_notification.py – driver query alerts to MD + RM
│   │   └── call_log_links.py     – `Call Log.links` Dynamic Link helper
│   │
│   ├── jobs/                 ← background jobs (RQ) and scheduler entry points
│   │   ├── auto_trigger.py             – pre-departure auto-trigger (cron 5 min)
│   │   ├── post_trip_trigger.py        – post-trip auto-trigger    (cron 5 min)
│   │   ├── feedback_trigger.py         – feedback auto-trigger     (cron 5 min)
│   │   ├── retry_calls.py              – retry failed calls        (cron 5 min)
│   │   ├── voicemail_digest.py         – digest scheduler          (cron 5 min)
│   │   ├── recording_retention.py      – delete old recordings     (cron daily 03:00)
│   │   ├── process_call_recording.py   – per-question transcribe + evaluate
│   │   ├── process_driver_query.py     – inbound query pipeline
│   │   ├── call_log_handler.py         – on_update / after_insert hooks
│   │   ├── create_call_log_issue.py    – derive ERPNext Issue from a Call Log
│   │   └── pretranslate_questions.py   – warm translation cache during a call
│   │
│   ├── voice_ops/doctype/    ← DocType definitions (.json) + controllers (.py)
│   │   ├── voice_ops_settings/         – single doctype, all config
│   │   ├── checklist_template/         – question set
│   │   ├── checklist_question/         – child of template
│   │   ├── checklist_run/              – one call attempt
│   │   ├── checklist_response/         – child of run, one per question
│   │   ├── driver_query/               – inbound query record
│   │   ├── voicemail_digest_log/       – generated digest
│   │   ├── voicemail_digest_entry/     – child of digest
│   │   ├── voice_ops_synonym/          – extra yes/no synonyms per language
│   │   └── voice_ops_language_template/ – WhatsApp template per language
│   │
│   ├── patches/              ← migration patches listed in patches.txt
│   │
│   └── public/
│       ├── js/
│       │   ├── timeline_recording_proxy.js  – global desk script
│       │   └── call_log.js                  – per-doctype form script
│       └── css/
│
└── (config/, templates/, www/ — Frappe standard, mostly empty)
```

---

## DocTypes

| DocType | Kind | Purpose |
| --- | --- | --- |
| **Voice Ops Settings** | Single | All app configuration — credentials, toggles, defaults, provider selection. |
| **Checklist Template** | Master | A reusable question set (e.g. Pre-Boarding, Post-Trip). |
| **Checklist Question** | Child of Template | One prompt — text, response type, blocker/warning flags, sequence. |
| **Checklist Run** | Transactional | One attempt to collect a checklist from a driver. Links to a Call Log. |
| **Checklist Response** | Child of Run | One answer — recording URL, raw transcript, normalized value, confidence. |
| **Driver Query** | Transactional | One inbound query — 3 recordings, transcripts, resolved caller + vehicle. |
| **Voicemail Digest Log** | Transactional | A generated digest for one window. |
| **Voicemail Digest Entry** | Child of Digest Log | One voicemail row inside a digest. |
| **Voice Ops Synonym** | Child of Settings | Extra yes / no / maybe synonyms per language. |
| **Voice Ops Language Template** | Child of Settings | Maps a detected language code to a WhatsApp template SID. |

External DocTypes Voice Ops reads or writes:

- **Call Log** (core ERPNext, with custom field `custom_detected_language`)
- **Twilio Call Log** (`twilio_integration` app)
- **Issue** (core)
- **Trip Roster Assignment**, **Trip Crew Member**, **Vehicle**, **Schedule Addition** (your FMS app)
- **WhatsApp Template Reference** (`twilio_integration` app, optional)
- **FMS AI Settings** (`fms_ai` app, optional)

---

## Voice Ops Settings — the configuration single

Every feature in voice_ops is gated by a flag or string on this single
doctype. Highlights (full list lives on the doctype JSON):

**Master switch**
- `enabled` — the kill switch. Webhooks and jobs short-circuit when it's off.

**Telephony providers**
- `outbound_telephony_provider` / `inbound_telephony_provider` / `feedback_telephony_provider` — `Twilio` or `Exotel`.
- Twilio: `account_sid`, `auth_token`, `twilio_from_number`, `twilio_whatsapp_number`.
- Exotel: `account_sid`, `api_key`, `api_token`, `exotel_caller_id`, `exotel_whatsapp_from`, `exotel_feedback_flow_app_id` (leave blank to use ExoML mode).

**Sarvam (STT)**
- `sarvam_api_key`, `sarvam_api_url`, `sarvam_model` (default `saaras:v3`), `sarvam_mode` (`translate` or `transcribe`), `sarvam_language_code` (default detection mode).

**Checklist behaviour**
- `default_checklist_template` — used by auto-trigger when no per-route override exists.
- `default_response_timeout` — seconds of silence the IVR waits per question.
- `call_buffer_minutes` — how far ahead of departure to dial.
- `feedback_buffer_minutes` — how long after trip end to place a feedback call.
- `default_driver_language` — fallback when Sarvam can't detect.

**Feature toggles**
- `enable_escalation`, `enable_voicemail_digest`, `enable_inbound_calls`, `enable_email_notifications`, `enable_whatsapp_notifications`, `enable_exotel_whatsapp_ack`, `enable_driver_ack`, `enable_route_manager_alert`.

**Acknowledgement templates**
- `exotel_whatsapp_template_driver_ack`, `language_template_map` (child table) — map BCP-47 → WhatsApp template SID per language.

**Digest**
- Window cadence, email recipients, WhatsApp recipients, channel selection (Email / WhatsApp / Both).

When adding a new flag, also add a corresponding `if not settings.<flag>: return`
guard at the relevant webhook or job entry point — never assume a setting is
present.

---

## Scheduler events

Configured in [`hooks.py`](voice_ops/hooks.py):

| Cadence | Job |
| --- | --- |
| `*/5 * * * *` | `voice_ops.jobs.auto_trigger.check_and_trigger` — pre-departure checklists |
| `*/5 * * * *` | `voice_ops.jobs.post_trip_trigger.check_and_trigger_post_trip` — post-trip checklists |
| `*/5 * * * *` | `voice_ops.jobs.feedback_trigger.check_and_trigger` — feedback calls |
| `*/5 * * * *` | `voice_ops.jobs.retry_calls.process_pending_retries` — retry failed calls |
| `*/5 * * * *` | `voice_ops.jobs.voicemail_digest.check_and_send` — voicemail digest |
| `0 3 * * *`   | `voice_ops.jobs.recording_retention.purge_old_recordings` — delete recordings > 30 days old |

Every entry point starts with an `is_enabled()` check, so disabling Voice Ops
in Settings is enough to silence the whole scheduler footprint.

---

## Document hooks

Configured in [`hooks.py`](voice_ops/hooks.py):

| DocType | Event | Handler |
| --- | --- | --- |
| Twilio Call Log | `on_update` | `jobs.call_log_handler.on_twilio_call_log_update` |
| Call Log | `before_validate` | `jobs.call_log_handler.fix_exotel_null_status` |
| Call Log | `after_insert` | `jobs.call_log_handler.attach_exotel_recording` |
| Call Log | `on_update` | `attach_exotel_recording` + `on_exotel_call_log_update` |

The Call Log hooks are how we capture **inbound** voicemails and feedback
recordings that the provider drops onto ERPNext's standard Call Log doctype
(no checklist run exists yet on the inbound side).

---

## Installation

You need a working [bench](https://github.com/frappe/bench) and an ERPNext
site. Voice Ops requires `frappe` and `erpnext` (and benefits from
`twilio_integration`, `exotel_integration`, and `fms_ai`, all optional).

```bash
cd $PATH_TO_YOUR_BENCH

# Fetch the app (production branch is `develop`)
bench get-app https://github.com/Liquiconnect/voice_ops.git --branch develop

# Install on a site
bench --site <your-site> install-app voice_ops

# Run any pending migrations (patches in patches.txt are idempotent)
bench --site <your-site> migrate
```

The `after_install` hook will:

1. Seed default Checklist Templates from the Driver Voice System SOP
   (pre-boarding ≈ 50 parameters, post-trip ≈ 25 parameters).
2. Create the required Telephony Call Types (`Voicemail`, `Feedback`).

Re-running install is safe — both steps are idempotent.

Post-install, open **Voice Ops Settings** (`/app/voice-ops-settings`) and:

1. Set provider credentials.
2. Pick a provider per flow.
3. Configure Sarvam credentials + model.
4. Enable the master switch (`enabled = 1`) when you're ready to go live.

---

## Local development

```bash
# from inside your bench
cd apps/voice_ops
pre-commit install      # enables ruff / eslint / prettier / pyupgrade on commit
```

Useful spots when poking around:

- Want to understand a flow end-to-end? Start at the **webhook entry point**
  in `api/`, follow it into `services/`, and see where it `frappe.enqueue`s a
  job in `jobs/`.
- Want to add a new checklist parameter? Add a row to the Checklist Template,
  then add the matching rule to `BLOCKER_RULES` / `WARNING_RULES` in
  `services/rule_evaluator.py`.
- Want to add a new language? Add it to `LANGUAGE_NAMES` in
  `services/language.py`, add synonyms to `services/transcript_processor.py`,
  and (if you ship WhatsApp acks) register the template via the
  `register_driver_ack_whatsapp_templates` patch.
- Want to test against a real call without making a real call? Use the
  whitelisted methods directly from bench console:
  ```python
  import frappe
  frappe.call("voice_ops.api.checklist.trigger_checklist_call",
              checklist_run_name="CR-2026-00001")
  ```

---

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/voice_ops
pre-commit install
```

Hooks configured: **ruff**, **eslint**, **prettier**, **pyupgrade**.

Conventions:

- Webhook handlers stay tiny — write to the Call Log / Checklist Run, enqueue
  a job, return the IVR XML. Don't transcribe, don't summarise, don't call
  Sarvam from inside a webhook.
- All external calls (Twilio / Exotel / Sarvam / Claude) live in `services/`
  and must log on failure via `frappe.log_error`.
- Business rules go in `services/rule_evaluator.py` as explicit dicts. If
  you're writing an `if`-tree, you're probably doing it wrong.
- Read [`voice_ops/CLAUDE.md`](voice_ops/CLAUDE.md) before adding a new
  module — it has the constraints that don't show up in code review.

---

## License

MIT — see [license.txt](license.txt).
