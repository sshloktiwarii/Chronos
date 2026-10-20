# Project Breakdown: chronos-orchestrator

## 1. Why It Exists (The Problem)
Autonomous agents frequently iterate on code by making speculative edits, compiling, running tests, and fixing bugs. If an agent hallucinates a faulty solution or breaks multiple files, restoring the repository manually is tedious. Copying entire directories on disk is painfully slow, consumes immense disk space, and burns seconds on every compilation cycle.

---

## 2. What It Does (The Solution)
Chronos is a distributed workflow engine featuring instant Apple APFS native Copy-on-Write (CoW) branching:
- **Instant CoW Filesystem Branching:** Calls Darwin `clonefile(2)` syscall directly to create isolated physical workspace directories in sub-millisecond time with zero byte duplication.
- **External Compiler Support:** External tools (`cargo`, `pytest`, `npm`) execute against real isolated inodes.
- **Automatic Failure Rollback:** If a workflow node fails, Chronos rolls the workspace back to the preceding snapshot.
- **Mermaid DAG Exporter:** Exports multi-step execution graphs to standard Mermaid markdown diagrams.

---

## 3. Targeted Niche & Use Cases
Developers and multi-agent systems executing autonomous build/test/fix loops where instant workspace recovery and speculative branching are critical.
