---
name: ucs-ai-erp
description: >
  Query live Odoo ERP data (customers, sales, invoices, projects, ...) through
  the scoped UCS AI gateway using the ucs-ai CLI. Use when the user asks about
  business data held in their Odoo/UCS ERP system.
---

# UCS AI — ERP data access

The `ucs-ai` CLI gives scoped, mostly read-only access to a live Odoo
database. Every command prints JSON. Access control lives entirely on the
server: an administrator granted this token an allow-list of models and
fields, intersected with a real user's permissions. Anything outside that
list returns `403 forbidden` — that is normal, not an error to work around.

## Discovery flow (always start here)

1. `ucs-ai models` — the models this token may read (technical name + label).
   Never guess model names; only these exist for you.
2. `ucs-ai describe <model>` — the readable fields with type, label, help,
   relations and selection values. Only these fields may appear in
   `--fields`, `--domain`, `--order`, `--by` and `--agg`. Dotted/related
   paths (`partner_id.name`) are rejected — read the relation's model
   separately instead.
3. Query with `read`, `group`, or `count`.

## Querying

```bash
# Records (JSON domain, comma-separated fields)
ucs-ai read sale.order --fields name,partner_id,amount_total \
  --domain '[["state", "=", "sale"]]' --order "date_order desc" --limit 10

# Totals / breakdowns
ucs-ai group sale.order --by partner_id --agg amount_total:sum,__count

# Date granularity in group-by
ucs-ai group sale.order --by date_order:month --agg amount_total:sum

# Counting
ucs-ai count res.partner --domain '[["customer_rank", ">", 0]]'
```

- Domains are standard Odoo domains as JSON. Leaves may only reference
  allowed fields of the queried model.
- Results are capped server-side (default 200 per call); paginate with
  `--limit`/`--offset`, and pass `--count` to `read` for the total.
- Many2one fields return `[id, "display name"]` pairs; use the id to read
  the related model if it is in your scope.

## Writing (only one operation)

```bash
ucs-ai note sale.order 42 "Reviewed by the agent." --notify-self
```

Posts a plain-text chatter log note, authored by the UCS AI bot. It works
only if the administrator enabled log notes for this token's scope. There is
no other write: no create, update, delete, or method call exists.

## Errors

| Response | Meaning | What to do |
|---|---|---|
| `401 unauthorized` | Token missing/expired/revoked | Ask the user for a fresh token (`ucs-ai connect`) |
| `403 forbidden` | Model/field outside scope | Use only what `models`/`describe` return; tell the user if the scope is too narrow |
| `400 bad_request` | Malformed domain/params | Fix the query |
| `429 rate_limited` | Too many requests | Back off, then retry |
| `not_configured` | No saved credentials | Ask the user to run `ucs-ai connect --url ... --token ...` |

Denied requests are audited server-side and surface to the administrator as
scope suggestions — so a `403` on a genuinely needed field is worth
mentioning to the user.
