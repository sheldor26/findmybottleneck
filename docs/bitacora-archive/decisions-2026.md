# DECISIONS — 2026

> Archived by bitacora. Still searchable with "recall".

<!-- bitacora:entry
id: D-0002
date: 2026-09-21
tags: [design]
-->
### The collector and the engine meet only in a trace file

**Context.** The machine this is built for runs Windows with a dedicated graphics card. The
machine it is written on does not. Worse, the interesting failures — a card
throttling, memory at a fallback speed, a link negotiated narrow — cannot be
made to happen on demand even on the right machine.

**Decision.** Capture writes one JSON file and stops. Everything that reasons about
performance reads that file and never touches hardware. The reasoning is tested
against traces; the reading is tested against text shaped like what the real
tools emit.

**Consequences.** The half that can be wrong in an interesting way — the reasoning — is testable
anywhere, and the untested remainder shrinks to "do the three programs run and
produce that text", which one real capture settles. It also makes a trace
something a person can send: today someone with a stutter posts a screenshot of
an overlay and waits for a stranger to interpret it, and a trace is that
screenshot except a program can read it. The cost is a schema to keep
compatible, and the discipline of never letting the engine reach for a live
reading it is missing.
