# Changelog

All notable changes to the ucs-ai connector (CLI and MCP adapter) are
documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/):

- **MAJOR** — breaking change to the CLI interface, config format, or a move
  to a new gateway API version;
- **MINOR** — new commands, options, or tools, backwards compatible;
- **PATCH** — bug fixes.

The server-side API contract is versioned independently by the gateway URL
prefix (`/ucs_ai/gateway/v1`); the connector only ever speaks one prefix.

## [1.2.0] - unreleased, on branch `dev`

### Added
- `ucs-ai write <model> <values-json> --reason TEXT [--id N]` and
  `ucs-ai requests`, plus the matching `write` / `list_write_requests` MCP
  tools, for the gateway's new human-approved write capability. `--reason` is
  required and reaches the reviewer above the diff: the field values say what
  would change, never why, which is what a person actually has to judge.
  `write` **changes nothing**: it queues a
  proposal for a person to approve in Odoo and returns a pending `request_id`;
  `requests` reads back the decision, including the reviewer's rejection note.
  Omitting `--id` proposes a new record, passing it proposes a change to that
  one. A proposal may set only fields the scope marks writable, cannot delete
  or archive anything, and once approved leaves a chatter note naming the
  token, the bound user and the approver.
  Requires ucs_ai 19.0.6.4.0 or later on the server.
- `SKILL.md` documents the write rules an agent needs before it tries: that a
  queued change must never be reported as done, that the writable set is
  narrower than the readable one, that there is no batch write, and that the
  daily budget returns `429` when spent.

### Changed
- **Breaking, pre-release only.** The `create` and `update` commands (and the
  `create_record` / `update_record` MCP tools) added earlier on this same
  unreleased branch are replaced by the single `write`. They never shipped in a
  tagged release, so no installed client is affected. Merging them is what the
  approval queue makes natural: from the caller's side the only difference left
  is whether a record id is given.

### Fixed
- MCP adapter: constrain the dependency to `mcp>=1.0,<2`. mcp 2.0.0 removed
  `mcp.server.fastmcp`, so the open `>=1.0` range made `uv run` on the
  published URL exit with "the 'mcp' package is required" as soon as 2.x was
  released — v1.1.0 is affected too, since that URL resolves the SDK fresh on
  every run.

## [1.1.0] - 2026-07-24

### Added
- Single-sourced version: `ucs_ai_connector.__version__` drives the package
  version (hatch dynamic version), the new `ucs-ai --version` flag, and a
  `User-Agent: ucs-ai-connector/X.Y.Z` header on every gateway request so the
  server-side audit trail records which client versions are in use.
- MCP adapter: `__version__` constant (kept in lockstep by
  `scripts/release.sh`), `--version` flag, `User-Agent: ucs-ai-mcp/X.Y.Z`
  header, and the version is advertised in the MCP initialize handshake.
- `scripts/release.sh`: verifies version consistency across both artifacts
  and the changelog, then tags and pushes `vX.Y.Z`.
- This changelog.

### Changed
- Recommended MCP registration now pins a release tag instead of tracking
  `main`, so what runs on a client machine is immutable per install and new
  versions are rolled out by updating the pinned URL that Odoo generates.

## [1.0.0] - 2026-07-22

### Added
- Initial release: `ucs-ai` CLI (connect/profiles/use/disconnect, models,
  describe, read, group, count, note, skill) speaking to the ucs_ai gateway
  `/v1`; stdlib-only.
- MCP adapter (`ucs_ai_mcp_server.py`) exposing the same six operations as
  MCP tools; runnable directly from its URL via uv (PEP 723).
