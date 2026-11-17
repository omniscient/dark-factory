# Fixture features

| Feature | Where |
|---|---|
| **Build.** | Nodes `fetch` (`workflow:3`) and `build-step` (`:5`, via `scripts/tool.sh`). Node `report` (`workflow:8-10`). |
| **Tooling.** | `scripts/tool.sh:1,4-6`, the `scripts/` directory, run artifact `implementation.md`, absolute `/opt/x.md`, local `.archon/.env`, continuation `:586-591`. |
| **Hooks.** | The `smoke-gate` hook and the `direct-to-pr` label are not nodes. |
