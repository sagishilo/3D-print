# ADR-0006 · Artifacts on a local volume, with URIs that allow S3 later

**Status:** accepted · 2026-11

**Decision.**

* Personalization writes `.scad`, `.stl` and `.gcode` files to a Docker volume.
* Messages reference artifacts only by URI (`file://…`), never by inline content.

**Consequences.**

* Zero extra services on one host.
* Moving to MinIO/S3 means changing the URI scheme and the writer and reader functions; no message contracts change.
* Do this when an agent runs on a second host, e.g. an edge Telemetry node that must fetch plate files.
