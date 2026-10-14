# Chronos orchestrator for dummies

## What is this?
Chronos is a rewind button and task manager for autonomous AI agents.

When you ask an AI model to build a feature or fix a bug, it usually needs to perform multiple steps: read a file, edit code, run tests, and check results. If the AI gets confused on step 7, it often hallucinates broken code, damages your project files, or gets stuck in an expensive loop trying to fix its own errors.

Chronos stops that by turning agent work into a step-by-step checklist with automatic checkpoints. If any step fails, Chronos restores your project files to the exact state they were in before that step started.

## The problem in plain English
Suppose an AI agent attempts a 15-step refactoring task.

On steps 1 through 6, it successfully reads your code and adds a new function. On step 7, it writes broken syntax into a core file. On step 8, it runs tests and sees failures. Because standard agent loops have no undo feature, the AI tries to patch its mistake on step 9, which introduces two more bugs. By step 12, the entire codebase is broken, the AI has run out of tokens, and you have to manually run `git checkout` to clean up the mess.

Chronos gives the agent an automated safety net:
1. Before step 7 runs, Chronos saves a snapshot of your files.
2. Step 7 runs and fails.
3. Chronos rolls back the workspace to step 6 immediately. The broken code is erased, and the agent can try a different approach from a known working baseline.

## How it works

### 1. Directed Acyclic Graph (DAG) checklist
Instead of one massive, unstructured conversation prompt, Chronos organizes tasks into a checklist with clear prerequisites. Step 3 cannot start until Step 1 and Step 2 finish successfully.

### 2. Instant Git snapshots
Before any tool executes, Chronos captures a snapshot of all tracked workspace files using Git tree plumbing (`git write-tree`). These snapshots live in an isolated directory (`.chronos/git`) and do not pollute your normal git commit history. Chronos also records the action, inputs, outputs, and timestamps in a local SQLite database (`.chronos/checkpoints.db`).

### 3. Automatic workspace rollback
If a task fails or an error is detected, Chronos runs an atomic rollback. It restores the exact directory state from the previous snapshot. Files that were modified revert to their previous state, and temporary files created by the failed step are deleted.

### 4. Context compaction
As an agent works, terminal logs and file dumps fill up the model context window. This slows down the model and increases API costs. Chronos compresses noisy older outputs into short summaries while preserving the active objective and current code changes.

### 5. Terminal DAG visualizer
You can inspect the execution state of your agent workflow in the terminal using the `visualize` command. It displays a tree showing which steps passed, which failed, and which rolled back.

## Quick start

### 1. Run the test suite
Verify that all unit tests pass:
```bash
python3 tests/run_tests.py
```
**Expected output:**
```text
Ran 7 tests in 0.045s
OK
```

### 2. Run the full triad integration demo
Run the automated end-to-end demo connecting Chronos, Aegis, and Inferno:
```bash
python3 examples/unified_triad_demo.py
```
This script runs a multi-step workflow, simulates a failure, and demonstrates an instant file rollback.

### 3. View checkpoint history
Inspect the recorded checkpoints in the local SQLite ledger:
```bash
python3 -m chronos.cli history --db .chronos/checkpoints.db
```

### 4. Visualize workflow steps in your terminal
Render the task status tree in your terminal:
```bash
python3 -m chronos.cli visualize --db .chronos/checkpoints.db
```

## Summary of what it does
- Prevents runaway retry loops when an AI agent makes a coding error.
- Erases broken code changes with an instant rollback to the last working checkpoint.
- Keeps your working tree clean by isolating snapshots in `.chronos/git`.
- Prevents context window exhaustion by compacting historical conversation logs.
- Gives you a full audit trail of every file change and command in SQLite.
