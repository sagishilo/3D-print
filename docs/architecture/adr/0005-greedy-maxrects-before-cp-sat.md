# ADR-0005 · Greedy MaxRects packing before OR-Tools CP-SAT

**Status:** accepted · 2026-11

**Decision.**

* Plate packing uses MaxRects with Best-Short-Side-Fit, rotation, margins and spacing.
* Plate-to-printer assignment is greedy per free printer, ordered by priority, due date and size, constrained by material, colour, bed
  size and the plate time budget.
* A short batching window lets jobs from one order share a plate.

**Why.**

* With 3-5 printers and mostly identical small parts, greedy assignment is near-optimal and runs in microseconds.
* It is also easy to explain to an operator.

**Revisit when:**

* more than about 8 printers;
* multi-colour (AMS) jobs that need slot planning;
* due-date penalties that must be balanced across the whole fleet;
* operator shift windows for clearing plates.

At that point, model the problem in CP-SAT (assignment, makespan and lateness objective) and keep MaxRects for geometric feasibility.
