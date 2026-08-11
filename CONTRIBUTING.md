# Contributing to KReports public docs

The public repository contains documentation, MCP-facing contracts, examples,
and provenance expectations. The collector, parser, database, release, and
operational implementation lives in the private
[`capitalparser/kreports-core`](https://github.com/capitalparser/kreports-core)
repository.

## What belongs here

- Explain why an MCP capability is needed and how a client invokes it.
- Improve public examples, setup guidance, or the response/data contract.
- Document provenance and explicit availability states.
- Add documentation-only fixtures with no real company data or secrets.

## What does not belong here

Do not commit DART credentials, raw filing archives, runtime databases, release
artifacts, private collector instructions, parser internals, or operational
backfill commands. Route implementation changes to the private core repository.

## Validation

For documentation changes, run:

```bash
git diff --check
```

Keep public claims aligned with the selected release artifact. A feature listed
in the README is a capability description, not proof that every company, year,
or original note is available in every deployment.
