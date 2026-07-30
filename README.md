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
uv tool install git+https://github.com/UCS-One-Business/odoo_ai_connector@v1.1.0
```

With pip/pipx:

```bash
pipx install git+https://github.com/UCS-One-Business/odoo_ai_connector@v1.1.0
```

Or run without installing at all:

```bash
uvx --from git+https://github.com/UCS-One-Business/odoo_ai_connector@v1.1.0 ucs-ai --help
```

Swap the tag for `@dev` to get the unreleased branch, needed for anything
[CHANGELOG.md](CHANGELOG.md) lists as unreleased (currently the `write` and
`requests` commands). A branch ref is mutable, so re-install with
`--force` to pick up new commits.

## Versioning & upgrading

Releases are git tags (`vX.Y.Z`, [SemVer](https://semver.org/)) with notes in
[CHANGELOG.md](CHANGELOG.md). Check what you are running and upgrade with:

```bash
ucs-ai --version
uv tool install --force git+https://github.com/UCS-One-Business/odoo_ai_connector@v1.1.0
```

For the MCP adapter, the version is pinned in the URL your MCP client is
registered with — re-register with the new tag's URL to upgrade (`uv run`
`--version` on the URL prints it). The connector sends its version as the
`User-Agent` on every request, so your Odoo administrator can see outdated
clients in the gateway audit trail. The gateway API itself is versioned
independently by its URL prefix (`/v1`): within one prefix the server only
evolves backwards-compatibly, so an older connector keeps working until the
prefix itself is retired.

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
ucs-ai note sale.order 42 "Reviewed."           # writes: each needs a scope opt-in
ucs-ai write project.task '{"name": "Fix login"}' \
    --reason "Reported twice in #support today; no task exists yet."
ucs-ai write project.task '{"priority": "1"}' --id 87 \
    --reason "Customer says production is down."   # --id makes it a change
ucs-ai requests                                 # what did the human decide?
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

## Alternative: the MCP adapter

For clients that prefer the MCP protocol over the CLI, the repository also
ships [`ucs_ai_mcp_server.py`](ucs_ai_mcp_server.py) — a thin, resident MCP
server exposing the same six gateway tools. It carries PEP 723 inline
metadata, so with [uv](https://docs.astral.sh/uv/) it runs straight from its
URL, nothing to clone or install:

```bash
claude mcp add ucs-ai \
  --env UCS_AI_URL=https://your-odoo.example.com \
  --env UCS_AI_PAT=<the token> \
  -- uv run https://raw.githubusercontent.com/UCS-One-Business/odoo_ai_connector/v1.1.0/ucs_ai_mcp_server.py
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.ucs_ai]
command = "uv"
args = ["run", "https://raw.githubusercontent.com/UCS-One-Business/odoo_ai_connector/v1.1.0/ucs_ai_mcp_server.py"]
env = { UCS_AI_URL = "https://your-odoo.example.com", UCS_AI_PAT = "<the token>" }
```

Like the CLI, the adapter is an untrusted translator: it makes no access
decision, and every allow/deny happens server-side in Odoo. `UCS_AI_DB` is
only needed on multi-database servers.

## Security model, in one paragraph

The token is shown once at creation, stored hashed in Odoo, expires on a
mandatory date, and is revocable at any time. Effective access is always the
*intersection* of the token's scope with the bound user's native Odoo rights —
the connector can narrow access, never widen it. The gateway exposes six read
operations and two writes, each off by default and opted into per scope: a
bot-authored chatter log note, and `write`, which **modifies nothing** — it
queues a single-record create or update for a person to approve in Odoo.
Approving re-checks the scope from scratch and additionally requires the
approver's own access to the record, so it can only ever narrow what the
proposal could do, never widen it. A proposal may set only fields the scope
marks writable, touches one record, cannot delete or archive anything, and once
applied posts a chatter note naming the token, the bound user and the approver.
There is no generic ORM write, batch write, method call, attachment, or login
path. Every request — allowed or denied — is audited server-side.

## License

Apache 2.0. The connector is intentionally open so anyone can verify exactly
what runs against their ERP. (The server-side `ucs_ai` Odoo module is
licensed separately.)
