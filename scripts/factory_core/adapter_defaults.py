"""Default adapter = MarketHawk's current constants. Parity: no adapter file == today."""
DEFAULTS = {
    "schema_version": 1,
    "components": {
        # Verbatim copy of COMPONENT_SECTION_MAP from scripts/architecture_slice.py
        "backend": [
            "Scan Execution Flow",
            "Backend Module Map",
            "Error Tracking System",
            "Celery Task Architecture",
            "Test Architecture",
        ],
        "frontend": [
            "Frontend Architecture",
            "Backend Module Map",
            "Error Tracking System",
        ],
        "dark-factory": [
            "Service Topology",
            "Celery Task Architecture",
            "Metrics and Observability",
        ],
        "infrastructure": [
            "Service Topology",
            "IB Gateway Integration",
            "Live Scanner",
            "Celery Task Architecture",
            "Catch Up Feature (Universe Aggregate Backfill)",
            "Metrics and Observability",
        ],
    },
    "safety": {
        "sensitive_keywords": "trading|ibkr|live order|notional|authentication|authorization|authn|authz|jwt|oauth|rbac|/auth",
        "hard_exclude_paths": [
            "dark-factory/", ".archon/", "scheduler.sh", "factory_core/",
            "app/services/trading", "app/tasks/trading.py", "app/core/auth", "app/routers/auth",
            # Claude Skills / settings / hooks / plugin / MCP surface — the
            # self-modifying-factory mechanism itself (#46). Forward-protects
            # epic_autopilot regardless of its enabled flag.
            ".claude/skills/", ".claude/settings.json", ".claude/settings.local.json",
            ".mcp.json", ".claude/plugins/", ".claude-plugin/", ".factory/hooks/",
        ],
        "dispatch_ceiling_keywords": "migration|migrate|performance|perf|architectur|refactor",
        # Verbatim copy of SAFETY_PATH_PATTERNS patterns from scripts/diff_rank.py
        "critical_diff_paths": [
            r"^alembic/versions/",
            r"^backend/app/routers/auth",
            r"^backend/app/core/auth",
            r"app/services/trading",
            r"app/tasks/trading\.py",
            r"^dark-factory/",
            # Claude Skills / settings / hooks / plugin / MCP surface (#46).
            # SKILL.md is visibility-only here — it is deliberately absent from
            # migration_seed_auth_patterns below (spec Q2/A2).
            r"^\.claude/skills/.*/scripts/",
            r"^\.claude/skills/.*/SKILL\.md$",
            r"^\.claude/settings\.json$",
            r"^\.claude/settings\.local\.json$",
            r"^\.mcp\.json$",
            r"^\.claude/plugins/",
            r"^\.claude-plugin/",
            r"^\.factory/hooks/",
        ],
        "migration_seed_auth_patterns": [
            r"^alembic/versions/", r"^dark-factory/seed/", r"seed.*\.sql$",
            r"^backend/app/routers/auth\.py$",
            # Whole-file-sensitive Claude Skills surface (#46) — surgical subset
            # of the critical_diff_paths set above. SKILL.md is intentionally
            # excluded: a path glob can't tell a frontmatter permission change
            # from a prose edit, so SKILL.md content is judged by the
            # code-review/conformance RUBRIC personas instead (spec Q2/A2).
            r"^\.claude/skills/.*/scripts/",
            r"^\.claude/settings\.json$",
            r"^\.claude/settings\.local\.json$",
            r"^\.mcp\.json$",
            r"^\.claude/plugins/",
            r"^\.claude-plugin/",
            r"^\.factory/hooks/",
        ],
        "main_red_allowed_paths": ["backend/", "frontend/", "alembic/", "dark-factory/smoke_gate.sh"],
    },
    "memory_routing": {
        "backend/app/*": ".archon/memory/backend-patterns.md",
        "frontend/src/*": ".archon/memory/frontend-patterns.md",
    },
    "deconflict": {
        "models_init": "backend/app/models/__init__.py",
        "migrations_dir": "alembic/versions/",
    },
}
