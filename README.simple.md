# Chronos Orchestrator — Plain-English Guide

## What is Chronos?

Chronos is an undo system for AI agents.

When an AI coding agent runs a 20-step task (edit files, run tests, fix errors, repeat), things go wrong. Step 12 might break the code, and the agent either crashes or wastes time retrying. Chronos lets the agent instantly roll back to any previous step and try a different approach — like a save system in a video game.

## Why is this needed?

AI models are probabilistic — they don't always get the right answer. If each step has an 85% success rate, a 20-step task only succeeds about 4% of the time without recovery.

Chronos changes this by making every step reversible and allowing the agent to branch out and try multiple solutions at once.

## How does it work?

1. **Virtual filesystem:** Chronos keeps a copy of your project files in memory. When the agent edits a file, it edits the copy, not the real file. If something breaks, Chronos swaps back to the old copy instantly (no disk writes).

2. **Task graph:** Instead of running steps one after another in a straight line, Chronos organizes them as a graph (a DAG). Steps that don't depend on each other can run in parallel. Steps that fail can trigger fallback steps.

3. **Checkpointing:** After every step, Chronos saves a snapshot — the command that ran, its output, and the state of all files. You can always go back.

4. **Context compaction:** Long agent runs generate tons of output. Chronos compresses old logs while keeping the important bits, so the AI doesn't run out of context window.

## What's it built with?

- **Python** — Core orchestration logic
- **SQLite** — Checkpoint storage (Write-Ahead Logging for speed)
- **Blake3** — Cryptographic hashing (integrates with Aegis security receipts)

## Who is it for?

Developers running long, multi-step AI agent tasks who want reliability, rollback, and parallel execution.
