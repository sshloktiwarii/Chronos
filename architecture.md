# Architecture Specification: chronos-orchestrator

## 1. System Architecture Diagram

```mermaid
graph TD
    User["Agent / Workflow Engine"] -->|Defines Steps| DAG["DAGWorkflow Engine"]
    
    subgraph "Execution & State Management"
        DAG --> Solver["Topological Dependency Solver"]
        DAG --> Store["CheckpointStore (SQLite WAL)"]
        DAG --> Recovery["RecoveryManager"]
    end
    
    subgraph "Filesystem Storage Engine"
        DAG --> VFS["Virtual Filesystem (vfs.py)"]
        VFS -->|Darwin / macOS| Clonefile["APFS clonefile(2) CoW Syscall"]
        VFS -->|Linux / Fallback| Reflink["cp --reflink CoW Branch"]
    end
    
    subgraph "External Toolchains"
        Clonefile --> RealInode["Physical Workspace Inodes"]
        RealInode --> Compilers["cargo / pytest / npm Execution"]
    end
```

---

## 2. Component Blueprint
- **`chronos/dag.py`**: Multi-step graph scheduler with concurrency backpressure, automated rollbacks, and Mermaid export.
- **`chronos/vfs.py`**: Copy-on-Write branch manager leveraging `libc.clonefile` for sub-millisecond physical clones.
- **`chronos/recovery.py`**: State restoration coordinator applying snapshot trees back to disk.
- **`chronos/checkpoint.py`**: Persistent snapshot ledger tracking file manifests and SHA hashes.
