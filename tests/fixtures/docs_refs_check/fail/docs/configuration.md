# Fixture configuration reference

## Layer 1: `config/config.yaml`

| Key | Default | Reader |
|---|---|---|
| `scheduler.poll_interval` | `60` | `scripts/tool.sh:4` |
| `scheduler.skip_labels` | `[wontfix, duplicate]` | **unread** |
| `budgets.code-review` | `22000` | `tool.sh:1-2` |

## Layer 2: environment variables

| Key | Default |
|---|---|
| `not_a.config_key` | `x` |
