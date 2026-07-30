#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.0,<2"]
# ///
"""Thin MCP adapter for the ucs_ai gateway (PRD FR-27..FR-29, NFR-15).

Translates MCP tool calls 1:1 into HTTPS POSTs against the scoped ucs_ai
gateway of an Odoo server. This process is deliberately UNTRUSTED and makes
NO access-control decision (FR-28): every allow/deny happens server-side in
Odoo, where the PAT's scope is intersected with the bound user's rights. If
this adapter is modified or replaced, nothing about the security model
changes — it can only ever see what the gateway already allows.

Configuration (FR-29) — exactly two settings, via environment variables:

    UCS_AI_URL   Base URL of the Odoo server, e.g. https://mycompany.odoo.example
    UCS_AI_PAT   A Personal Access Token issued in Odoo under
                 AI > Configuration > Access Tokens
    UCS_AI_DB    Optional database name. Only needed when the server hosts
                 several databases (sent as X-Odoo-Database); the production
                 stack is single-database and does not need it.

Run (stdio transport, as MCP clients expect). With uv, the PEP 723 header
above resolves the dependency automatically — no venv or pip step:

    UCS_AI_URL=https://... UCS_AI_PAT=... uv run ucs_ai_mcp_server.py

Without uv, install the official MCP python SDK first
(pip install "mcp>=1.0,<2" — mcp 2.x dropped the FastMCP entry point this
adapter builds on):

    UCS_AI_URL=https://... UCS_AI_PAT=... python3 ucs_ai_mcp_server.py

See the addon's docs/connecting.md for Claude Code / Codex registration.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    sys.exit("The MCP python SDK 1.x is required: pip install 'mcp>=1.0,<2' "
             "(2.x removed mcp.server.fastmcp)")

GATEWAY_PREFIX = '/ucs_ai/gateway/v1'
TIMEOUT = 60
# Kept in lockstep with ucs_ai_connector.__version__ (this script must stay
# standalone-runnable from a URL, so the constant is duplicated here; the
# release script asserts they match). Advertised in the MCP initialize
# handshake and sent as User-Agent so the gateway audit trail records it.
__version__ = '1.2.0'
USER_AGENT = 'ucs-ai-mcp/%s' % __version__

mcp = FastMCP(
    "ucs-ai",
    instructions=(
        "Scoped access to live Odoo data through the ucs_ai gateway. Start "
        "with list_models to see what is readable, then describe_model for "
        "the schema of a model. Only the models and fields returned by those "
        "tools are readable; requests outside that scope are denied by the "
        "server. Domains, order, group-by and aggregates may only reference "
        "allowed fields, and dotted/related paths are rejected. Writing is "
        "opted in separately by the server and always goes through human "
        "approval: `write` PROPOSES one record change and returns a pending "
        "request, it does not apply it. Never tell the user a change has been "
        "made — say you have requested it and that it is waiting for their "
        "approval, then use list_write_requests to find out what they decided. "
        "Nothing can be deleted or archived, and every approved change posts a "
        "chatter note naming the token and the approver."
    ),
)


def _call(operation: str, payload: dict) -> dict:
    """POST one gateway operation; return the parsed JSON response.

    Never interprets or filters the data (FR-28) — errors are passed through
    as the gateway's opaque {'error': ...} envelope plus the HTTP status.
    """
    base_url = os.environ.get('UCS_AI_URL', '').rstrip('/')
    pat = os.environ.get('UCS_AI_PAT', '')
    if not base_url or not pat:
        return {'error': 'adapter_not_configured',
                'detail': 'Set the UCS_AI_URL and UCS_AI_PAT environment variables.'}
    parsed_url = urllib.parse.urlparse(base_url)
    if parsed_url.scheme not in ('http', 'https') or not parsed_url.netloc:
        return {'error': 'adapter_not_configured',
                'detail': 'UCS_AI_URL must be an HTTP or HTTPS base URL.'}
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': USER_AGENT,
        # The PAT travels only in this header, never in URL or body.
        'Authorization': 'Bearer ' + pat,
    }
    database = os.environ.get('UCS_AI_DB')
    if database:
        headers['X-Odoo-Database'] = database
    request = urllib.request.Request(  # noqa: S310 -- scheme validated above
        base_url + GATEWAY_PREFIX + '/' + operation,
        data=json.dumps({k: v for k, v in payload.items() if v is not None}).encode(),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 -- scheme validated above
            request, timeout=TIMEOUT,
        ) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode())
        except Exception:
            body = {'error': 'http_%s' % exc.code}
        body.setdefault('status', exc.code)
        return body
    except urllib.error.URLError as exc:
        return {'error': 'connection_failed', 'detail': str(exc.reason)}
    except Exception as exc:
        return {'error': 'adapter_error', 'detail': str(exc)}


@mcp.tool()
def list_models() -> dict:
    """List the Odoo models this token may read (technical name and label)."""
    return _call('list_models', {})


@mcp.tool()
def describe_model(model: str) -> dict:
    """Schema of one allowed model: readable fields with type, label, help,
    relation and selection values. Fields not listed here are not accessible."""
    return _call('describe_model', {'model': model})


@mcp.tool()
def search_read(model: str, fields: list[str] | None = None,
                domain: list | None = None, order: str | None = None,
                limit: int | None = None, offset: int | None = None,
                with_count: bool = False) -> dict:
    """Read records of an allowed model.

    fields: allowed field names (defaults to every allowed field).
    domain: Odoo search domain, e.g. [["state", "=", "sale"]] — leaves may
    only reference allowed fields of THIS model (no dotted paths).
    order: e.g. "date_order desc". limit/offset paginate (server caps limit).
    with_count: also return the total match count.
    """
    return _call('search_read', {
        'model': model, 'fields': fields, 'domain': domain, 'order': order,
        'limit': limit, 'offset': offset,
        'with_count': with_count or None,
    })


@mcp.tool()
def read_group(model: str, groupby: list[str], aggregates: list[str] | None = None,
               domain: list | None = None, order: str | None = None,
               limit: int | None = None, offset: int | None = None) -> dict:
    """Group and aggregate records of an allowed model.

    groupby: e.g. ["partner_id"] or ["date_order:month"] (date granularity).
    aggregates: e.g. ["amount_total:sum", "__count"] — allowed fields only.
    """
    return _call('read_group', {
        'model': model, 'groupby': groupby, 'aggregates': aggregates,
        'domain': domain, 'order': order, 'limit': limit, 'offset': offset,
    })


@mcp.tool()
def count(model: str, domain: list | None = None) -> dict:
    """Count records of an allowed model matching a domain."""
    return _call('count', {'model': model, 'domain': domain})


@mcp.tool()
def post_log_note(model: str, res_id: int, body: str,
                  notify_self: bool = False) -> dict:
    """Post a plain-text chatter log note on a readable record.

    The server must explicitly enable log notes on the token's scope. The note
    is authored by the UCS AI bot; notify_self adds the PAT-bound user as the
    sole recipient.
    """
    return _call('post_log_note', {
        'model': model,
        'res_id': res_id,
        'body': body,
        'notify_self': notify_self,
    })


@mcp.tool()
def write(model: str, values: dict, reason: str,
          res_id: int | None = None) -> dict:
    """Propose ONE record change and send it to a human for approval.

    NOTHING IS CHANGED WHEN THIS RETURNS. It queues the proposal and returns a
    request_id in state 'pending'; a person then approves or rejects it in Odoo.
    Tell the user you have requested the change and that it needs their
    approval — never report it as done. Use list_write_requests for the outcome.

    Omit res_id to propose a NEW record, or pass res_id to change that one.
    ``values`` may set only the model's writable fields, which are a subset of
    the readable ones. one2many values are rejected and many2many values may
    only link or unlink existing records, so no other record is ever touched,
    and `active` cannot be written, so nothing can be archived or deleted.

    ``reason`` is required: one or two sentences for the person who will
    approve this, saying what the field values do not say, such as what
    prompted the change or what you concluded. Do not restate the values.
    """
    payload = {'model': model, 'values': values, 'reason': reason}
    if res_id is not None:
        payload['res_id'] = res_id
    return _call('write', payload)


@mcp.tool()
def list_write_requests(states: list[str] | None = None,
                        limit: int | None = None) -> dict:
    """Your own write proposals and what happened to them.

    States are 'pending', 'applied', 'rejected' and 'failed'. Rejected and
    failed entries carry the reviewer's note or the reason, which is how you
    learn what was wrong with a proposal. Only this token's requests are
    visible.
    """
    payload: dict = {}
    if states:
        payload['states'] = states
    if limit:
        payload['limit'] = limit
    return _call('list_write_requests', payload)


if __name__ == '__main__':
    if '--version' in sys.argv:
        print('ucs-ai-mcp %s' % __version__)
        sys.exit(0)
    # FastMCP has no version parameter; set it on the underlying server so
    # the MCP initialize handshake reports it to clients.
    mcp._mcp_server.version = __version__
    mcp.run()
