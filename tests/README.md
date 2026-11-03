# What these tests establish

Tests are not evidence that an undocumented API behavior is guaranteed.

## Contract sources

- OpenAlex OQL API: https://help.openalex.org/api/oql/
  Translation uses POST `/query`; execution uses POST `/`. OQL and OQO
  are sibling body alternatives. Query-validation failures carry `validation`;
  classic errors may instead carry `error` and `message`. Nullable classic URLs
  are valid when a query cannot be represented in classic syntax.
- OpenAlex authentication: https://help.openalex.org/api/authentication/
  Bearer authentication and usage headers.
- OpenAlex OpenAPI: https://help.openalex.org/openapi.json
  Entity models, aliases, parameter constraints, and response mappings.
- OQO JSON Schema: https://api.openalex.org/query/spec/oqo
  Entity enum, query-tree structure, and query limits.
- HTTP Retry-After: RFC 9110 section 10.2.3.
  https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3
  A valid header gives a minimum wait, as seconds or an HTTP date.
- Project requirements: the parent project's TODO calls for rejecting unknown
  request parameters rather than silently discarding them. Client metadata and
  error reporting must not expose the configured authentication secret.

## How to interpret the suites

- `test_request_contracts.py`: externally visible request/authentication and
  project input-safety requirements. Failures are not marked as expected merely
  because the current implementation lacks the behavior.
- Query/model/routing tests check wire shape, field constraints, preservation,
  and dispatch; they do not prove server-side search semantics.
- Retry tests include project-selected policy (one-second minimum, exponential
  jitter ceiling, configured cap and attempt budget). Those exact values are
  not OpenAlex requirements. Retry-After and cancellation are separate checks.
- Environment singleton/default/path tests describe the current project policy;
  they are regression checks, not requirements imposed by OpenAlex. Paths use the shared parser: `~` expands and absolute paths are preserved.
- Codegen tests check that generation preserves model contracts, existing schema
  definitions, and endpoint-specific differences. Temporary files only are used.
- Calculation fixtures are upstream production examples, not a complete response
  schema. See `fixtures/calculation/README.md`. Strict scalar extras are a local
  model policy, not an established restriction on all future OpenAlex fields.
- Tests of the installed cache check isolation and exclusions; they do not claim
  all library versions have identical behavior.

No live API calls are made. Mock responses test client behavior against the
specified example; they cannot verify that an upstream service honors its docs.

## Shared helper coverage

Generic filters, retry/backoff/deadlines, environment parsing, HTTP lifecycle,
SQLite cache behavior, parsing/pagination mechanics, endpoint models/factory, and
shared code generation are tested in `../raq_helpers/tests`. Their migrated cases
were removed here. This suite retains OpenAlex configuration and overrides: credit
budgets, auth/redaction, OQL/OQO JSON requests, response models, calculations, schema
supplements, generated API routes, and adapter integration.

Run helper tests separately with `uv run --project ../raq_helpers python -m pytest`.
