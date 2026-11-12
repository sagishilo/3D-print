# ADR-0007 · Human-assisted social capture, human-approved replies

**Status:** accepted · 2026-11

**Context.** Meta removed the Groups API from the Graph API in 2024, and automated scraping of groups violates Meta's terms. It also
risks bans for the business's accounts.

**Decision.**

* Group posts enter the system only when a real group member captures them with a browser extension (`cmd.social.ingest_capture`).
* Also allowed: the business's own Page and Messenger through the official Graph API, and public sources whose terms allow monitoring.
* Every reply is drafted by the agent, approved by a human, and **posted by the human**.

**Consequences.**

* Coverage depends on human participation, which is acceptable because quality beats volume for a local business.
* No automated posting means no automated bans.
* Data stored is minimal: post text and URL only.
