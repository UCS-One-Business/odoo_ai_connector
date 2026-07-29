"""ucs-ai-connector: scoped, agent-friendly access to live Odoo ERP data.

Single source of truth for the client version. Everything else derives from
this constant: pyproject.toml (via hatch's version source), the CLI's
``--version`` flag, the ``User-Agent`` header sent to the gateway, and the
MCP adapter's advertised server version.
"""

__version__ = "1.2.0"
