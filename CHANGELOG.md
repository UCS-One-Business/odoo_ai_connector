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
- `ucs-ai create <model> <values-json>` and `ucs-ai update <model> <id>
  <values-json>`, plus the matching `create_record` / `update_record` MCP
  tools, for the gateway's new per-model write capability. Each call touches
  exactly one record, may set only fields the scope marks writable, cannot
  delete or archive anything, and leaves a chatter note naming the token.
  Requires ucs_ai 19.0.6.2.0 or later on the server.
- `SKILL.md` documents the write rules an agent needs before it tries: the
  writable set is narrower than the readable one, there is no batch write, and
  the daily write budget returns `429` when spent.

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
