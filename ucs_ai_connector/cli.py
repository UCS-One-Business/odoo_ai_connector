"""ucs-ai: CLI connector for the UCS AI gateway.

Every command translates 1:1 into an HTTPS POST against the scoped ``ucs_ai``
gateway of an Odoo server (``/ucs_ai/gateway/v1/...``). This process is
deliberately UNTRUSTED and makes NO access-control decision: every allow/deny
happens server-side in Odoo, where the token's scope is intersected with the
bound user's rights. Modifying or replacing this connector changes nothing
about the security model — it can only ever see what the gateway already
allows.

The package has zero dependencies beyond the Python standard library, so what
runs is exactly what you can read in this file.

Credentials come from (highest precedence first):

1. ``--url`` / ``--token`` / ``--db`` command-line options
2. ``UCS_AI_URL`` / ``UCS_AI_PAT`` / ``UCS_AI_DB`` environment variables
3. A named profile stored by ``ucs-ai connect`` in
   ``~/.config/ucs-ai/config.json`` (chmod 600)

Output is JSON on stdout, suitable for piping into agents and scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

GATEWAY_PREFIX = "/ucs_ai/gateway/v1"
TIMEOUT = 60

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "ucs-ai",
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


# ---------------------------------------------------------------------------
# Profile storage
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_config(config: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # The token is a bearer credential: keep the file owner-only.
    fd = os.open(
        CONFIG_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        stat.S_IRUSR | stat.S_IWUSR,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str, str | None]:
    """Return (base_url, token, database) from flags, env, or a profile."""
    url = getattr(args, "url", None) or os.environ.get("UCS_AI_URL")
    token = getattr(args, "token", None) or os.environ.get("UCS_AI_PAT")
    database = getattr(args, "db", None) or os.environ.get("UCS_AI_DB")
    if url and token:
        return url, token, database

    config = _load_config()
    profiles = config.get("profiles", {})
    name = getattr(args, "profile", None) or config.get("default")
    profile = profiles.get(name) if name else None
    if profile:
        return (
            url or profile["url"],
            token or profile["token"],
            database or profile.get("db"),
        )
    _fail(
        "not_configured",
        "No credentials. Run 'ucs-ai connect --url <odoo-url> --token <pat>' "
        "first, or set UCS_AI_URL and UCS_AI_PAT.",
    )
    raise AssertionError  # unreachable


def _fail(error: str, detail: str, status: int = 1) -> None:
    json.dump({"error": error, "detail": detail}, sys.stdout, indent=2)
    print()
    sys.exit(status)


# ---------------------------------------------------------------------------
# Gateway transport
# ---------------------------------------------------------------------------

def _call(url: str, token: str, database: str | None,
          operation: str, payload: dict) -> dict:
    """POST one gateway operation; return the parsed JSON response.

    Never interprets or filters the data — errors are passed through as the
    gateway's opaque ``{"error": ...}`` envelope plus the HTTP status.
    """
    base_url = url.rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _fail("not_configured", "The Odoo URL must be an HTTP(S) base URL.")
    headers = {
        "Content-Type": "application/json",
        # The token travels only in this header, never in URL or body.
        "Authorization": "Bearer " + token,
    }
    if database:
        headers["X-Odoo-Database"] = database
    request = urllib.request.Request(  # noqa: S310 -- scheme validated above
        base_url + GATEWAY_PREFIX + "/" + operation,
        data=json.dumps(
            {k: v for k, v in payload.items() if v is not None}
        ).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode())
        except Exception:
            body = {"error": "http_%s" % exc.code}
        body.setdefault("status", exc.code)
        return body
    except urllib.error.URLError as exc:
        return {"error": "connection_failed", "detail": str(exc.reason)}


def _run(args: argparse.Namespace, operation: str, payload: dict) -> None:
    url, token, database = _resolve_credentials(args)
    result = _call(url, token, database, operation, payload)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()
    sys.exit(1 if "error" in result else 0)


def _parse_json_arg(value: str, name: str):
    try:
        return json.loads(value)
    except ValueError:
        _fail("bad_argument", "--%s must be valid JSON, e.g. "
              '\'[["state", "=", "sale"]]\'' % name)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_connect(args: argparse.Namespace) -> None:
    result = _call(args.url, args.token, args.db, "list_models", {})
    if "error" in result:
        json.dump(result, sys.stdout, indent=2)
        print()
        print("Connection failed; profile not saved.", file=sys.stderr)
        sys.exit(1)
    config = _load_config()
    profiles = config.setdefault("profiles", {})
    profiles[args.profile] = {"url": args.url.rstrip("/"), "token": args.token}
    if args.db:
        profiles[args.profile]["db"] = args.db
    config.setdefault("default", args.profile)
    if args.default:
        config["default"] = args.profile
    _save_config(config)
    models = result.get("models", [])
    print(json.dumps({
        "saved": args.profile,
        "config": CONFIG_PATH,
        "default": config["default"],
        "readable_models": len(models),
    }, indent=2))


def _cmd_profiles(args: argparse.Namespace) -> None:
    config = _load_config()
    out = {
        "config": CONFIG_PATH,
        "default": config.get("default"),
        "profiles": {
            name: {"url": p["url"], "db": p.get("db")}
            for name, p in config.get("profiles", {}).items()
        },
    }
    print(json.dumps(out, indent=2))


def _cmd_use(args: argparse.Namespace) -> None:
    config = _load_config()
    if args.name not in config.get("profiles", {}):
        _fail("unknown_profile", "No profile named '%s'. See 'ucs-ai profiles'."
              % args.name)
    config["default"] = args.name
    _save_config(config)
    print(json.dumps({"default": args.name}, indent=2))


def _cmd_disconnect(args: argparse.Namespace) -> None:
    config = _load_config()
    removed = config.get("profiles", {}).pop(args.name, None)
    if removed is None:
        _fail("unknown_profile", "No profile named '%s'." % args.name)
    if config.get("default") == args.name:
        config["default"] = next(iter(config.get("profiles", {})), None)
    _save_config(config)
    print(json.dumps({"removed": args.name,
                      "default": config.get("default")}, indent=2))


def _cmd_skill(args: argparse.Namespace) -> None:
    skill = os.path.join(os.path.dirname(__file__), "SKILL.md")
    with open(skill, encoding="utf-8") as handle:
        content = handle.read()
    if args.install:
        target_dir = os.path.join(args.install, "ucs-ai-erp")
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "SKILL.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)
        print(json.dumps({"installed": target}, indent=2))
    else:
        print(content)


def _cmd_models(args: argparse.Namespace) -> None:
    _run(args, "list_models", {})


def _cmd_describe(args: argparse.Namespace) -> None:
    _run(args, "describe_model", {"model": args.model})


def _cmd_read(args: argparse.Namespace) -> None:
    _run(args, "search_read", {
        "model": args.model,
        "fields": args.fields.split(",") if args.fields else None,
        "domain": _parse_json_arg(args.domain, "domain") if args.domain else None,
        "order": args.order,
        "limit": args.limit,
        "offset": args.offset,
        "with_count": args.count or None,
    })


def _cmd_group(args: argparse.Namespace) -> None:
    _run(args, "read_group", {
        "model": args.model,
        "groupby": args.by.split(","),
        "aggregates": args.agg.split(",") if args.agg else None,
        "domain": _parse_json_arg(args.domain, "domain") if args.domain else None,
        "order": args.order,
        "limit": args.limit,
        "offset": args.offset,
    })


def _cmd_count(args: argparse.Namespace) -> None:
    _run(args, "count", {
        "model": args.model,
        "domain": _parse_json_arg(args.domain, "domain") if args.domain else None,
    })


def _cmd_note(args: argparse.Namespace) -> None:
    _run(args, "post_log_note", {
        "model": args.model,
        "res_id": args.id,
        "body": args.body,
        "notify_self": args.notify_self,
    })


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_credential_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("credentials (optional overrides)")
    group.add_argument("--profile", help="named profile from 'ucs-ai connect'")
    group.add_argument("--url", help="Odoo base URL (overrides profile)")
    group.add_argument("--token", help="personal access token (overrides profile)")
    group.add_argument("--db", help="database name, for multi-database servers")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ucs-ai",
        description="Scoped, agent-friendly access to live Odoo ERP data "
                    "through the UCS AI gateway. All output is JSON.",
        epilog="Discovery flow: 'ucs-ai models' -> 'ucs-ai describe <model>' "
               "-> 'ucs-ai read/group/count'. See 'ucs-ai skill' for the "
               "full agent skill.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("connect", help="verify a token and save it as a profile")
    p.add_argument("--url", required=True, help="Odoo base URL, e.g. https://erp.example.com")
    p.add_argument("--token", required=True, help="personal access token issued in Odoo")
    p.add_argument("--db", help="database name (only for multi-database servers)")
    p.add_argument("--profile", default="default", help="profile name (default: 'default')")
    p.add_argument("--default", action="store_true", help="make this the default profile")
    p.set_defaults(func=_cmd_connect)

    p = sub.add_parser("profiles", help="list saved profiles (tokens are never shown)")
    p.set_defaults(func=_cmd_profiles)

    p = sub.add_parser("use", help="switch the default profile")
    p.add_argument("name")
    p.set_defaults(func=_cmd_use)

    p = sub.add_parser("disconnect", help="remove a saved profile")
    p.add_argument("name")
    p.set_defaults(func=_cmd_disconnect)

    p = sub.add_parser("skill", help="print the agent skill (or install it)")
    p.add_argument("--install", metavar="SKILLS_DIR",
                   help="write SKILL.md into SKILLS_DIR/ucs-ai-erp/ "
                        "(e.g. .claude/skills)")
    p.set_defaults(func=_cmd_skill)

    p = sub.add_parser("models", help="list the Odoo models this token may read")
    _add_credential_options(p)
    p.set_defaults(func=_cmd_models)

    p = sub.add_parser("describe", help="schema of one allowed model")
    p.add_argument("model", help="technical model name, e.g. sale.order")
    _add_credential_options(p)
    p.set_defaults(func=_cmd_describe)

    p = sub.add_parser("read", help="read records of an allowed model")
    p.add_argument("model")
    p.add_argument("--fields", help="comma-separated field names (default: all allowed)")
    p.add_argument("--domain", help='JSON Odoo domain, e.g. \'[["state", "=", "sale"]]\'')
    p.add_argument("--order", help='sort order, e.g. "date_order desc"')
    p.add_argument("--limit", type=int, help="max records (server caps this)")
    p.add_argument("--offset", type=int, help="pagination offset")
    p.add_argument("--count", action="store_true", help="also return the total match count")
    _add_credential_options(p)
    p.set_defaults(func=_cmd_read)

    p = sub.add_parser("group", help="group and aggregate records")
    p.add_argument("model")
    p.add_argument("--by", required=True,
                   help='comma-separated group-by, e.g. "partner_id" or "date_order:month"')
    p.add_argument("--agg", help='comma-separated aggregates, e.g. "amount_total:sum,__count"')
    p.add_argument("--domain", help="JSON Odoo domain")
    p.add_argument("--order", help="sort order")
    p.add_argument("--limit", type=int)
    p.add_argument("--offset", type=int)
    _add_credential_options(p)
    p.set_defaults(func=_cmd_group)

    p = sub.add_parser("count", help="count records matching a domain")
    p.add_argument("model")
    p.add_argument("--domain", help="JSON Odoo domain")
    _add_credential_options(p)
    p.set_defaults(func=_cmd_count)

    p = sub.add_parser(
        "note",
        help="post a plain-text chatter log note on a readable record "
             "(the only write; the token's scope must opt in)")
    p.add_argument("model")
    p.add_argument("id", type=int, help="record id")
    p.add_argument("body", help="plain text, max 10000 characters")
    p.add_argument("--notify-self", action="store_true",
                   help="also notify the token's bound user")
    _add_credential_options(p)
    p.set_defaults(func=_cmd_note)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
