"""Default adapter = MarketHawk's current constants. Parity: no adapter file == today."""

# Shared substring tokens used by both scripts/diff_rank.py::_safety_signal() and
# scripts/gate_blast_radius.py::classify_file() to sub-classify a critical_diff_paths /
# migration_seed_auth_patterns match as the Claude Skills / settings / hooks / plugin /
# MCP surface (#46) rather than a generic migration/auth/trading/factory match. Kept as
# a single source of truth here so the two gates can't drift out of sync.
SKILL_SECURITY_TOKENS = (
    "claude/", "settings", "mcp", "claude/plugins", "claude-plugin", "factory/hooks",
)

DEFAULTS = {
    "schema_version": 1,
    "components": {
        # Sole source of truth — scripts/architecture_slice.py re-exports this
        # as COMPONENT_SECTION_MAP.
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
        # Sole source of truth — scripts/diff_rank.py re-exports this compiled
        # as SAFETY_PATH_PATTERNS.
        "critical_diff_paths": [
            r"^alembic/versions/",
            r"^backend/app/routers/auth",
            r"^backend/app/core/auth",
            r"app/services/trading",
            r"app/tasks/trading\.py",
            r"^dark-factory/",
            # Claude Skills / settings / hooks / plugin / MCP surface (#46).
            # SKILL.md is visibility-only here — it is deliberately absent from
            # migration_seed_auth_patterns below (spec Q2/A2). Superseded at the
            # gate by FACTORY_OWNED_MIGRATION_SEED_FLOOR (^\.claude/, #200/OD7):
            # the floor blocks any .claude/** edit; this DEFAULTS list stays as
            # it is.
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
            # Superseded at the gate by FACTORY_OWNED_MIGRATION_SEED_FLOOR
            # (^\.claude/, #200/OD7): the floor blocks any .claude/** edit;
            # this DEFAULTS list stays as it is.
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
    "loops": [],
}

# Factory-owned boundary paths (#200/A6): unioned into safety.critical_diff_paths and
# safety.migration_seed_auth_patterns after every adapter.yaml merge (adapter.py::load),
# regardless of what a target's adapter.yaml declares for those two lists. Deliberately
# small and boundary-specific -- not the full DEFAULTS lists, which would re-inject
# MarketHawk-specific paths (e.g. ^alembic/versions/) into every target.
FACTORY_OWNED_CRITICAL_DIFF_FLOOR = [
    r"^\.factory/hooks/",
    r"^\.factory/adapter\.yaml$",
    r"^\.claude/",
    r"^workflows/",
    r"^commands/",
    # Paths that shadow baked factory enforcement when a target tracks them
    # (entrypoint.sh copies the baked pieces into the clone only if absent) -- F2.
    r"^\.archon/commands/",
    r"^\.archon/workflows/",
    r"^dark-factory/scripts/",
]

# migration_seed_auth_patterns is the hard-blocking (HUMAN_REQUIRED) list.
# .factory/adapter.yaml itself is excluded here: its loops: block is target-definable
# for side_effect_level 1-3 (#196), so blanket-blocking the whole file on every edit
# would require human review for benign loop authoring; its escalation risk is caught
# by gate_blast_radius.py's semantic diff instead. workflows/ and commands/ are
# excluded in v1 by Owner decision OD1 (visibility-only) -- promoting them is a
# one-line change to this set. Derived from the list above (not duplicated) so the
# two floors cannot drift apart.
_VISIBILITY_ONLY = {r"^\.factory/adapter\.yaml$", r"^workflows/", r"^commands/"}
FACTORY_OWNED_MIGRATION_SEED_FLOOR = [
    p for p in FACTORY_OWNED_CRITICAL_DIFF_FLOOR if p not in _VISIBILITY_ONLY
]
