# Mercury Tools

Mercury Finance is one repository-local `stdio` MCP for accounting and ERP
work. It gives an MCP host such as Codex a cited knowledge/catalog layer plus
local FlowAccount, PEAK, and configured-ERP actions. The host remains the AI;
Mercury does not run a local LLM and does not provide a web UI.

## Local Boundary

- The installed plugin exposes exactly one server: `mercury-finance`.
- ERP credentials live only in the selected repository under
  `.mercury/credentials.env`; they are not sent to Mercury Cloud or stored in
  Cloud credential storage.
- ERP request execution, confirmation state, and audit recording happen in
  the local process. Cloud-backed knowledge and catalog retrieval never receive
  ERP request bodies or credential values.
- The local audit ledger is `.mercury/audit/audit.jsonl`. It is append-only and
  redacts credentials, personal data, request inputs, and provider values.
- Dotenv files are not a credential source for local ERP execution. Use the
  `mercury credentials` commands below instead of putting ERP credentials in
  `.env`, plugin configuration, or chat.

## Marketplace Install

The released plugin requires `uvx`, which is included with
[uv](https://docs.astral.sh/uv/). Confirm it is available before installing:

```bash
uvx --version
```

After the release owner creates the immutable `v0.2.0` tag, install the GitHub
marketplace plugin:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref v0.2.0 \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
codex plugin add mercury-finance@mercury-tools
codex mcp list
```

The launcher uses `uvx` and the released package. It does not register a
hosted HTTP MCP, request a Mercury owner token, or configure Cloud credentials.
Before the tag exists, use the local source checkout and the verification
commands in [docs/JUDGE_QUICKSTART.md](docs/JUDGE_QUICKSTART.md).

## Repository Setup

For development or a local judge run:

```bash
uv sync --extra dev
uv run mercury doctor --repo-root .
uv run mercury mcp serve-local
```

Set up credentials in the repository where the ERP work belongs. The commands
prompt locally and print field names/status only.

```bash
uv run mercury credentials setup flowaccount --env production --repo-root .
uv run mercury credentials status --repo-root .
uv run mercury credentials test flowaccount --env production --repo-root .
```

`credentials test` performs the connector's credential validation and safe GET
probe only: FlowAccount uses `GET /company/info`; PEAK uses `GET /user`.
Choose the intended PEAK environment explicitly:

```bash
uv run mercury credentials setup peak --env uat --repo-root .
uv run mercury credentials test peak --env uat --repo-root .
```

Clear a single profile only when it is no longer needed:

```bash
uv run mercury credentials clear flowaccount --env production --repo-root .
```

To remove every local credential profile for the repository:

```bash
uv run mercury credentials clear --all --repo-root .
```

`clear --all` unlinks the local credential file and invalidates pending local
requests. It is not a claim of forensic secure erase from backups, snapshots,
or storage media. See [docs/LOCAL_CREDENTIALS.md](docs/LOCAL_CREDENTIALS.md).

## Catalogs And Writes

Use `import_erp_spec` to import an OpenAPI, Swagger, Postman, or explicit
endpoint document into the active repository. Repository-configured custom
hosts require an interactive trusted-host confirmation before the executor can
call them. Imported actions remain local overlays; they do not publish
credentials or ERP payloads.

`search_erp_actions` and `get_erp_action_schema` identify an action before it
runs. Effective risk tiers govern execution:

- Tier 0 safe GET actions run through `run_erp_read`.
- Tier 1 writes require `preview_erp_write`, one distinct explicit user
  confirmation through `confirm_erp_write`, then one `execute_erp_write`.
- Tier 2 writes, and actions requiring two confirmations, require two distinct
  confirmations for the same fresh preview before one execution.

Never retry an `outcome_unknown` write. Call `get_erp_request_status`, reconcile
the provider result through an approved safe status action or manually, then
create a fresh preview only after the outcome is definite. Full procedures are
in [docs/ACTION_CATALOG.md](docs/ACTION_CATALOG.md).

## Documentation

- [Judge quickstart](docs/JUDGE_QUICKSTART.md)
- [Repository-local credentials](docs/LOCAL_CREDENTIALS.md)
- [Action catalog and confirmation model](docs/ACTION_CATALOG.md)
- [v0.2.0 release checklist](docs/RELEASE_V0.2.0.md)

## Verification

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -m "not integration" -q
uv run pytest tests/integration/test_local_erp_mcp.py -q
uv run python scripts/validate_release_plugin.py
uv run python scripts/smoke_local_plugin.py
```

The Task 18 integration test uses only fake Cloud and fake ERP transports.
Optional live FlowAccount and PEAK probes run only when their explicit
`MERCURY_LIVE_FLOWACCOUNT=1` or `MERCURY_LIVE_PEAK=1` flag is set, and they are
limited to credential validation plus the safe GET probe.
