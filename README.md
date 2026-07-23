# odoo_ai_connector

Command-line connector for the **UCS AI gateway** — scoped, audited access to
live Odoo ERP data for AI agents and scripts.

The server side is the `ucs_ai` Odoo module: an administrator defines an
allow-list of models and fields (a *scope*), issues a Personal Access Token
bound to one user and one scope, and every request is intersected with that
user's real Odoo permissions and audited. This connector is the untrusted
client half: each command is one HTTPS POST to the gateway, and **no access
decision is ever made on this side**. Replacing or modifying the connector
changes nothing about what it can see.

## What is being run

- One package, [`ucs_ai_connector/cli.py`](ucs_ai_connector/cli.py), using
  **only the Python standard library** — no third-party dependencies, nothing
  else executes.
- Network traffic: HTTPS POSTs to `<your-odoo>/ucs_ai/gateway/v1/...` only.
  The token travels in the `Authorization` header, never in URLs or bodies.
- Local state: an optional `~/.config/ucs-ai/config.json` (chmod 600) holding
  named connection profiles.

## Install

With [uv](https://docs.astral.sh/uv/) (recommended — no setup needed):

```bash
uv tool install git+https://github.com/UCS-One-Business/odoo_ai_connector
```

With pip/pipx:

```bash
pipx install git+https://github.com/UCS-One-Business/odoo_ai_connector
```

Or run without installing at all:

```bash
uvx --from git+https://github.com/UCS-One-Business/odoo_ai_connector ucs-ai --help
```

## Connect

Paste the command shown when your administrator issues the token in Odoo
(*AI ▸ Gateway ▸ Access Tokens ▸ Issue Token*), or:

```bash
ucs-ai connect --url https://your-odoo.example.com --token <PAT>
```

`connect` verifies the token against the gateway before saving anything, then
stores it as a profile. Multiple environments are handled with named profiles:

```bash
ucs-ai connect --url https://staging.example.com --token <PAT> --profile staging
ucs-ai profiles          # list (tokens are never displayed)
ucs-ai use staging       # switch default
ucs-ai disconnect staging
```

Flags (`--url/--token/--db/--profile`) and environment variables
(`UCS_AI_URL`/`UCS_AI_PAT`/`UCS_AI_DB`) override the stored profile, so CI and
ephemeral use need no config file.

## Use

```bash
ucs-ai models                                   # what may this token read?
ucs-ai describe sale.order                      # readable fields + types
ucs-ai read sale.order --fields name,amount_total \
    --domain '[["state", "=", "sale"]]' --limit 5
ucs-ai group sale.order --by partner_id --agg amount_total:sum
ucs-ai count res.partner --domain '[["customer_rank", ">", 0]]'
ucs-ai note sale.order 42 "Reviewed."           # the only write, scope opt-in
```

All output is JSON. Anything outside the token's scope returns an opaque
`403 forbidden`; expired or revoked tokens return `401`.

## For AI agents: the skill

The discovery flow (models → describe → query) ships as an agent skill:

```bash
ucs-ai skill                             # print it
ucs-ai skill --install .claude/skills    # install for Claude Code
```

This is the intended integration: instead of a resident MCP server, the agent
reads [`SKILL.md`](ucs_ai_connector/SKILL.md) and drives the plain CLI. It is
minimal (no long-running process, no extra protocol), transparent (every call
is a visible shell command in the transcript), and the server-side audit trail
still captures everything.

## Security model, in one paragraph

The token is shown once at creation, stored hashed in Odoo, expires on a
mandatory date, and is revocable at any time. Effective access is always the
*intersection* of the token's scope with the bound user's native Odoo rights —
the connector can narrow access, never widen it. The gateway exposes five read
operations and one fixed write (a bot-authored plain-text chatter log note,
off by default per scope); there is no generic ORM write, method call,
attachment, or login path. Every request — allowed or denied — is audited
server-side.

## License

Apache 2.0. The connector is intentionally open so anyone can verify exactly
what runs against their ERP. (The server-side `ucs_ai` Odoo module is
licensed separately.)
