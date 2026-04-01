# CLAUDE.md

## Project identity

This repository contains a multilingual AI-driven driver operations platform for intercity bus fleets.

Primary stack (MVP-focused):

- ERPNext / Frappe = system of record + primary processing engine
- Exotel = telephony
- Sarvam AI = STT/TTS/language handling

AWS is NOT part of the MVP runtime architecture except for optional storage (e.g., S3 for attachments). It may be introduced later for scaling and reliability.

---

## Architecture principles (Revised for MVP)

### Source of truth

ERPNext / Frappe is the source of truth for:

- trip/service/vehicle/route/crew data
- checklist templates and questions
- checklist runs and responses
- issue and escalation records
- final readiness status
- review queue and dashboards

### Runtime execution (MVP)

ERPNext is responsible for:

- triggering calls via Exotel
- receiving Exotel webhooks
- enqueueing background jobs
- calling Sarvam APIs
- processing transcripts
- applying business rules
- storing final results

All asynchronous work MUST be handled using Frappe background jobs (RQ workers).

### External integrations

Exotel handles:

- outbound/inbound telephony
- call events/webhooks
- recording metadata

Sarvam handles:

- speech-to-text
- language detection
- transcript confidence scoring

### AI boundaries

AI is used only for:

- speech understanding
- transcript generation
- confidence scoring

AI must NOT:

- decide blockers
- decide escalation
- make business decisions

All business decisions must be deterministic and implemented inside ERPNext.

---

## Runtime flow (MVP)

1. ERPNext triggers a call via Exotel
2. Driver receives call and interaction is recorded
3. Exotel sends webhook with call metadata and recording URL
4. ERPNext webhook handler immediately enqueues a background job
5. Background job sends recording to Sarvam AI
6. Sarvam returns transcript + confidence
7. ERPNext processes transcript:
   - normalization
   - mapping to structured fields
   - rule evaluation (blockers/warnings)
8. ERPNext stores final result
9. Low-confidence or critical cases are sent to review queue

---

## Failure handling (Critical)

- Webhook handlers must be lightweight and non-blocking
- All external calls must include retry logic
- Failed jobs must be logged and retried
- Low-confidence outputs must trigger manual review
- No silent failures allowed

---

## Business logic rules

### Hard blockers

Examples:

- driver not fit
- key crew missing
- serious mechanical issue
- serious safety issue
- incident/breakdown
- explicit not-ready status

### Soft warnings

Examples:

- partial AC issue
- cleaning incomplete
- low supplies

### Analytics flags

Examples:

- diesel exception
- repeated issues
- route-level patterns

All rules must be:

- explicit
- testable
- auditable
- config-driven where possible

---

## Review workflow

Low-confidence or critical cases must enter manual review.

Review queue must include:

- checklist context
- transcript
- confidence score
- normalized response
- recording link
- actions (approve/edit/reopen)

All manual actions must be audited.

---

## Coding standards (MVP focus)

### General

- Keep implementation simple and production-oriented
- Avoid premature microservices or distributed systems
- Prefer clarity over abstraction
- No hardcoding credentials

### Python / Frappe

- Use background jobs for async work
- Keep business logic modular
- Avoid heavy logic in DocType handlers
- Separate integration logic from domain logic

---

## Future scalability (Explicitly deferred)

AWS services (Lambda, SQS, Step Functions) may be introduced later for:

- high call volume
- retry-heavy workloads
- improved fault tolerance

Do NOT introduce distributed architecture until real bottlenecks are observed.

---

## Constraints

- Do not build a chatbot-style system
- Keep checklist execution deterministic
- Do not let AI make business decisions
- Prioritize working MVP over perfect architecture

---

## What good looks like

A system that:

- reliably processes calls
- handles failures gracefully
- produces structured checklist outputs
- supports manual review
- can later evolve into event-driven architecture if needed
