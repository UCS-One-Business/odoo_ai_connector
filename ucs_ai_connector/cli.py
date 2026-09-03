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
3. A named profile in ``~/.config/ucs-ai/config.json`` (chmod 600), stored
   either by ``ucs-ai login`` (OAuth device sign-in: no token to copy, the
   profile holds a self-refreshing access token) or by ``ucs-ai connect``
   (a personal access token issued in Odoo)

Output is JSON on stdout, suitable for piping into agents and scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

GATEWAY_PREFIX = "/ucs_ai/gateway/v1"
OAUTH_REGISTER = "/ucs_ai/oauth/register"
OAUTH_DEVICE = "/ucs_ai/oauth/device"
OAUTH_TOKEN = "/ucs_ai/oauth/token"  # noqa: S105
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# Refresh an OAuth access token this many seconds before it expires, so a
# command started just before the deadline never hits a 401 mid-flight.
REFRESH_MARGIN = 60
TIMEOUT = 60
# Sent on every request so the gateway's audit trail records which client
# versions are in the wild (informs when an upgrade campaign is needed).
USER_AGENT = "ucs-ai-connector/%s" % __version__

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
            token or _profile_token(config, name),
            database or profile.get("db"),
        )
    _fail(
        "not_configured",
        "No credentials. Run 'ucs-ai login --url <odoo-url>' first (or "
        "'ucs-ai connect --url <odoo-url> --token <pat>'), or set UCS_AI_URL "
        "and UCS_AI_PAT.",
    )
    raise AssertionError  # unreachable


def _profile_token(config: dict, name: str) -> str:
    """The bearer token of a profile, refreshing an OAuth one when due."""
    profile = config["profiles"][name]
    if "refresh_token" not in profile:
        return profile["token"]
    if profile.get("expires_at", 0) - REFRESH_MARGIN > time.time():
        return profile["access_token"]
    response = _oauth_post(profile["url"], OAUTH_TOKEN, {
        "grant_type": "refresh_token",
        "client_id": profile["client_id"],
        "refresh_token": profile["refresh_token"],
    }, profile.get("db"))
    if "access_token" not in response:
        _fail("unauthorized",
              "The sign-in for profile '%s' has expired or was disconnected "
              "in Odoo. Run 'ucs-ai login --url %s --profile %s' again."
              % (name, profile["url"], name), status=1)
    _store_oauth_tokens(profile, response)
    _save_config(config)
    return profile["access_token"]


def _store_oauth_tokens(profile: dict, tokens: dict) -> None:
    profile["access_token"] = tokens["access_token"]
    profile["refresh_token"] = tokens["refresh_token"]
    profile["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 0))


def _oauth_post(url: str, path: str, form: dict,
                database: str | None = None) -> dict:
    """Form-encoded POST to an OAuth endpoint; the JSON body on any status."""
    headers = {"User-Agent": USER_AGENT,
               "Content-Type": "application/x-www-form-urlencoded"}
    if database:
        headers["X-Odoo-Database"] = database
    request = urllib.request.Request(  # noqa: S310 -- caller validated scheme
        url.rstrip("/") + path,
        data=urllib.parse.urlencode(form).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            return {"error": "http_%s" % exc.code}
    except urllib.error.URLError as exc:
        return {"error": "connection_failed", "detail": str(exc.reason)}


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
        "User-Agent": USER_AGENT,
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


def _cmd_login(args: argparse.Namespace) -> None:
    """OAuth device sign-in (RFC 8628): no token to copy, no browser needed
    on this machine. The person approves on any device; the tokens land in
    the profile and refresh themselves from then on."""
    url = args.url.rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _fail("not_configured", "The Odoo URL must be an HTTP(S) base URL.")
    # Register this CLI as a public device-flow client (RFC 7591). The
    # registration authorises nothing; the person's approval does.
    request = urllib.request.Request(  # noqa: S310 -- scheme validated above
        url + OAUTH_REGISTER,
        data=json.dumps({
            "client_name": "ucs-ai connector",
            "grant_types": [DEVICE_GRANT, "refresh_token"],
            "token_endpoint_auth_method": "none",
        }).encode(),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json",
                 **({"X-Odoo-Database": args.db} if args.db else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            client_id = json.loads(response.read().decode())["client_id"]
    except urllib.error.HTTPError as exc:
        _fail("login_failed", "Registration refused (HTTP %s). Is the AI "
              "gateway enabled on that Odoo, and is it version 19.0.8.10 or "
              "later?" % exc.code)
    except (urllib.error.URLError, KeyError, ValueError) as exc:
        _fail("connection_failed", str(exc))
    started = _oauth_post(url, OAUTH_DEVICE, {"client_id": client_id}, args.db)
    if "device_code" not in started:
        _fail("login_failed", json.dumps(started))
    print("Open this link on any device and log in to Odoo:",
          file=sys.stderr)
    print("    " + started["verification_uri"], file=sys.stderr)
    print("then enter the code:", file=sys.stderr)
    print("    " + started["user_code"], file=sys.stderr)
    print("Waiting for approval (Ctrl-C to cancel)...", file=sys.stderr)
    interval = int(started.get("interval", 5))
    deadline = time.time() + int(started.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        tokens = _oauth_post(url, OAUTH_TOKEN, {
            "grant_type": DEVICE_GRANT,
            "client_id": client_id,
            "device_code": started["device_code"],
        }, args.db)
        error = tokens.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error:
            _fail("login_failed", {
                "access_denied": "The sign-in was declined in Odoo.",
                "expired_token": "The code expired before it was entered.",
            }.get(error, error))
        break
    else:
        _fail("login_failed", "The code expired before it was entered.")
    config = _load_config()
    profiles = config.setdefault("profiles", {})
    profile = {"url": url, "client_id": client_id}
    if args.db:
        profile["db"] = args.db
    _store_oauth_tokens(profile, tokens)
    profiles[args.profile] = profile
    config.setdefault("default", args.profile)
    if args.default:
        config["default"] = args.profile
    _save_config(config)
    result = _call(url, profile["access_token"], args.db, "list_models", {})
    print(json.dumps({
        "saved": args.profile,
        "config": CONFIG_PATH,
        "default": config["default"],
        "readable_models": len(result.get("models", [])),
    }, indent=2))


def _cmd_profiles(args: argparse.Namespace) -> None:
    config = _load_config()
    out = {
        "config": CONFIG_PATH,
        "default": config.get("default"),
        "profiles": {
            name: {"url": p["url"], "db": p.get("db"),
                   "auth": "oauth" if "refresh_token" in p else "token"}
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


def _cmd_write(args: argparse.Namespace) -> None:
    # No --id means create; --id means update that record. The gateway infers
    # the same way, so this command has no mode flag to get wrong.
    payload = {
        "model": args.model,
        "values": _parse_json_arg(args.values, "values"),
        "reason": args.reason,
    }
    if args.id is not None:
        payload["res_id"] = args.id
    _run(args, "write", payload)


def _cmd_requests(args: argparse.Namespace) -> None:
    payload: dict = {}
    if args.state:
        payload["states"] = [s.strip() for s in args.state.split(",") if s.strip()]
    if args.limit:
        payload["limit"] = args.limit
    _run(args, "list_write_requests", payload)


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
    parser.add_argument("--version", action="version",
                        version="ucs-ai-connector %s" % __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "login",
        help="sign in with a code shown here and entered in Odoo from any "
             "device (no token, no browser on this machine); saves a "
             "self-refreshing profile")
    p.add_argument("--url", required=True, help="Odoo base URL, e.g. https://erp.example.com")
    p.add_argument("--db", help="database name (only for multi-database servers)")
    p.add_argument("--profile", default="default", help="profile name (default: 'default')")
    p.add_argument("--default", action="store_true", help="make this the default profile")
    p.set_defaults(func=_cmd_login)

    p = sub.add_parser("connect", help="verify a personal access token and save it as a profile")
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
        "write",
        help="REQUEST a record change — creates a new record without --id, or "
             "changes that one with --id. Nothing is written: a human approves "
             "it in Odoo first. Check the outcome with 'ucs-ai requests'.")
    p.add_argument("model")
    p.add_argument("values",
                   help='JSON object of writable fields, e.g. \'{"name": "Fix login"}\'')
    p.add_argument("--id", type=int,
                   help="record id to change; omit to propose a NEW record")
    p.add_argument("--reason", required=True,
                   help="why you are asking, for the person who approves it. "
                        "They can already see the values; say what those do "
                        "not, such as what prompted the change.")
    _add_credential_options(p)
    p.set_defaults(func=_cmd_write)

    p = sub.add_parser(
        "requests",
        help="list this token's own write requests and what a human decided "
             "about them (pending, applied, rejected, failed)")
    p.add_argument("--state",
                   help="comma-separated states to show, e.g. 'pending'")
    p.add_argument("--limit", type=int, help="max entries (server caps this)")
    _add_credential_options(p)
    p.set_defaults(func=_cmd_requests)

    p = sub.add_parser(
        "note",
        help="post a plain-text chatter log note on a readable record "
             "(the token's scope must opt in)")
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
