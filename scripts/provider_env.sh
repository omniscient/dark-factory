#!/usr/bin/env bash
# Provider env pass-through for dispatched run containers (#489). Sourced by scheduler.sh.
# Do NOT add set -euo pipefail: this file is sourced and must not alter caller shell options.
#
# Run containers read <PROJECT_DIR>/.archon/.env (compose env_file), not the scheduler's
# deploy/instance.env. Provider selection and provider env set only in instance.env are
# forwarded here as `docker compose run -e NAME` flags.
#
# Why not run-compose.yml `environment:`? An `environment:` entry beats env_file: `NAME: ${NAME:-}`
# blanks a value that arrives via .archon/.env, and a bare `NAME:` deletes it (both verified with
# docker compose). `run -e NAME` overrides env_file only when NAME is set in the scheduler's
# environment; unset leaves env_file untouched. provider_env_flags additionally skips empty values,
# so `JIRA_TOKEN=` left blank in instance.env cannot blank a .archon/.env value.
#
# Deliberately NOT listed (secrets already delivered via .archon/.env; forwarding the scheduler's
# copy could change which credential a default deployment uses): GH_TOKEN,
# CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY. tests/test_provider_env_passthrough.py pins this.
#
# Also not listed: platform-injected runtime markers that preflight treats as alternative-auth
# signals (AWS_EXECUTION_ENV, AWS_CONTAINER_CREDENTIALS_*, K_SERVICE, GAE_APPLICATION,
# FUNCTION_TARGET, KUBERNETES_SERVICE_HOST). They describe the scheduler's own platform, and
# forwarding them would make a run container's preflight skip its static-credential check.
#
# Every provider's required_env() and documented optional vars must appear here (that test).

PROVIDER_PASSTHROUGH_VARS=(
  # Provider selection
  FACTORY_TRACKER FACTORY_CODEHOST FACTORY_MODEL_PROVIDER
  # Jira tracker (required_env + optional)
  JIRA_BASE_URL JIRA_PROJECT_KEY JIRA_TOKEN JIRA_EPIC_LINK_FIELD
  JIRA_ISSUE_TYPE JIRA_REQUEST_TIMEOUT_SECONDS
  # GitLab code host
  GITLAB_TOKEN GITLAB_BASE_URL
  # Bedrock model provider
  CLAUDE_CODE_USE_BEDROCK AWS_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  AWS_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_SESSION_TOKEN
  # Vertex model provider
  CLAUDE_CODE_USE_VERTEX ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION
  GOOGLE_APPLICATION_CREDENTIALS GOOGLE_CLOUD_PROJECT
)

# Print one `-e NAME` pair per line for each forwarded var that is set and non-empty.
# Names only, never values: values travel through the environment, not argv.
provider_env_flags() {
  local v
  for v in "${PROVIDER_PASSTHROUGH_VARS[@]}"; do
    if [ -n "${!v:-}" ]; then
      printf -- '-e\n%s\n' "$v"
    fi
  done
}
