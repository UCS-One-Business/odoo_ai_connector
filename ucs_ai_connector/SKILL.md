---
name: ucs-ai-erp
description: >
  Query live Odoo ERP data (customers, sales, invoices, projects, ...) through
  the scoped UCS AI gateway using the ucs-ai CLI. Use when the user asks about
  business data held in their Odoo/UCS ERP system.
---

# UCS AI — ERP data access

The `ucs-ai` CLI gives scoped access to a live Odoo database: reads by
default, plus a few narrow write operations where an administrator opted in. Every command prints JSON. Access control lives entirely on the
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

## Writing (three operations, each opted in separately)

Everything below is off by default and granted per model by an administrator.
A `403` here means the capability was not granted — say so, do not retry.

```bash
# Chatter note (scope-wide opt-in)
ucs-ai note sale.order 42 "Reviewed by the agent." --notify-self

# Create ONE record
ucs-ai create project.task '{"name": "Fix login redirect", "description": "..."}'

# Update ONE record by id
ucs-ai update project.task 87 '{"priority": "1"}'
```

Rules worth knowing before you try:

- **Only writable fields.** They are a subset of the readable ones, chosen per
  model. `describe <model>` lists what is readable, not what is writable, so a
  readable field can still be refused.
- **One record per call.** There is no batch or domain-based write. Loop
  deliberately, and stay well under the daily write budget (default 50 per
  token per day; exceeding it returns `429`).
- **Nothing is ever deleted or archived.** `active` cannot be written, and
  relational values may only link or unlink existing records, never create or
  delete them.
- **Every write is visible.** Creating or updating posts a chatter note naming
  this token, and updates list each field's previous and new value. Write as if
  the record's owner will read it, because they will.
- No delete, no archiving, no method calls, no workflow buttons exist.

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
