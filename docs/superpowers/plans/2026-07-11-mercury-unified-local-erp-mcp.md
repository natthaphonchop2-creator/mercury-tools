# Mercury Unified Local ERP MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one `mercury-finance` Codex plugin whose local stdio MCP can safely execute every cataloged GET, POST, PUT, PATCH, and DELETE ERP action with repository-local credentials and Cloud-hosted accounting intelligence.

**Architecture:** A focused local runtime discovers active MCP roots, reads `.mercury` state, merges Cloud and repository Action Catalogs, applies deterministic policy, and dispatches ERP requests through small connector drivers on the user's machine. Render and Supabase become a read-only Cloud Brain for Skills, immutable catalog versions, connector documents, and RAG; they never receive ERP credentials or business payloads.

**Tech Stack:** Python 3.11-3.13, `mcp==1.26.0` FastMCP, Pydantic 2.13, HTTPX 0.28, Starlette, Supabase Postgres/PostgREST, pytest 9, Ruff, uv/uvx, Codex plugin manifests.

## Global Constraints

- The host-visible plugin and MCP server name is exactly `mercury-finance`.
- The MCP transport used by the plugin is local `stdio`; the hosted MCP is not registered by the plugin.
- Supported ERP methods are exactly `GET`, `POST`, `PUT`, `PATCH`, and `DELETE`.
- ERP requests and credential loading run on the user's machine.
- ERP credentials live only in `<repo>/.mercury/credentials.env` and never enter MCP arguments, chat, Supabase, Render, Git, RAG, Skills, logs, or telemetry.
- There is no manually entered Mercury Owner Token.
- There is no Mercury web application and no local LLM.
- `.mercury/credentials.env`, `.mercury/cache/`, and `.mercury/audit/` are ignored; `.mercury/config.json` and `.mercury/catalog/` may be committed only when secret-free.
- Production internet endpoints require HTTPS, exact trusted hosts, no cross-host redirects, and no link-local/cloud-metadata targets.
- Tier 0 GET actions require zero confirmations; Tier 1 create/update/PUT/PATCH/attachment actions require one; Tier 2 payment/approve/void/DELETE/email/share/invite and unobserved inferred mutations require two.
- Runtime risk classification may raise a catalog tier and must never lower it.
- A mutation is never retried automatically after dispatch; timeout, disconnect, or 5xx after dispatch becomes `outcome_unknown` and blocks replay of the same payload hash.
- Direct MCP specification imports are repository-local; only trusted GitHub CI publishes the global catalog.
- The release launcher is pinned to immutable tag `v0.2.0` and must not execute a moving branch.
- Existing accounting Skills, cited knowledge retrieval, and Mercury Flows remain accessible from the one local MCP.
- Cloud telemetry is disabled in v0.2.0; the repository-local audit ledger is the only runtime execution log.
- The existing `mercury-finance-private` plugin, `/private-mcp` route, private bearer token, and journal-only MCP tools are removed.
- Do not perform live production ERP mutations in automated tests or CI.

---

## File Map

| Area | Files | Responsibility |
| --- | --- | --- |
| Repository state | `src/mercury_tools/local/repository.py` | MCP-root validation, `.mercury` scaffold, ignore rules, trusted host config |
| Credentials | `src/mercury_tools/local/credentials.py`, `credential_cli.py` | Atomic dotenv lifecycle and interactive CLI |
| Catalog core | `src/mercury_tools/catalog/models.py`, `identity.py`, `local_store.py`, `cache.py`, `search.py` | Immutable action models, local overlay, Cloud cache, merge/search |
| Catalog import | `src/mercury_tools/catalog/importers/*.py`, `src/mercury_tools/safety/network.py` | OpenAPI, Swagger, Postman, documentation parsing, sanitization, safe fetching |
| Drivers | `src/mercury_tools/drivers/*.py` | Auth, safe probes, request headers, provider result interpretation |
| Execution | `src/mercury_tools/execution/*.py` | Risk, preview/confirmation state, request construction, dispatch, idempotency |
| Audit | `src/mercury_tools/local/audit.py` | Repository-local redacted JSONL events |
| Cloud Brain | `src/mercury_tools/cloud/client.py`, `api.py`, `src/mercury_tools/db/catalog.py` | Read-only runtime API and service-role catalog publication |
| Local MCP | `src/mercury_tools/mcp/local_server.py`, `local_runtime.py` | Single host-visible tool/resource/prompt surface |
| Supabase | `supabase/migrations/20260711_*.sql` | Catalog schema and server-secret cleanup |
| Plugin | `plugins/mercury-finance/*`, `.agents/plugins/marketplace.json` | One pinned local stdio MCP and routing Skills |
| Operations | `scripts/*.py`, `.github/workflows/publish-catalog.yml`, `render.yaml` | Global publication, cleanup, deploy, smoke checks |

The local runtime, Cloud read API, catalog, and plugin migration are kept in one plan because the approved acceptance boundary is atomic: one installed MCP must be able to discover knowledge and safely execute local ERP actions without the old private server. Each task still ends in an independently reviewable test gate and commit.

### Task 1: Repository Root and `.mercury` State

**Files:**
- Create: `src/mercury_tools/local/__init__.py`
- Create: `src/mercury_tools/local/repository.py`
- Test: `tests/test_local_repository.py`

**Interfaces:**
- Consumes: MCP root URIs supplied later by `Context.session.list_roots()`.
- Produces:
  - `RepositoryContext(repository_id: str, root: Path, mercury_dir: Path, config_path: Path, credentials_path: Path, catalog_dir: Path, cache_dir: Path, audit_dir: Path)`
  - `root_paths(root_uris: Sequence[str]) -> tuple[Path, ...]`
  - `resolve_repository_root(requested: str | Path | None, roots: Sequence[Path]) -> Path`
  - `ensure_repository_state(root: Path) -> RepositoryContext`
  - `load_repository_config(context: RepositoryContext) -> RepositoryConfig`
  - `RepositoryConfig.allow_private_network(connector_id, environment) -> bool`
  - `configure_connector(context, connector_id, environment, driver_id, base_url, auth_settings) -> RepositoryConfig`

- [ ] **Step 1: Write failing root and scaffold tests**

```python
import os
import stat
from pathlib import Path

import pytest

from mercury_tools.local.repository import (
    configure_connector,
    ensure_repository_state,
    resolve_repository_root,
    root_paths,
)


def test_single_root_is_selected_and_scaffolded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("dist/\n")
    selected = resolve_repository_root(None, (root,))
    context = ensure_repository_state(selected)

    assert context.root == root.resolve()
    assert context.repository_id.startswith("repo_")
    assert context.credentials_path == root / ".mercury" / "credentials.env"
    assert context.config_path.read_text() == (
        '{\n  "schema_version": 1,\n  "trusted_hosts": {},\n  "connectors": {}\n}\n'
    )
    ignore_lines = (root / ".gitignore").read_text().splitlines()
    assert ignore_lines[0] == "dist/"
    assert ignore_lines[-3:] == [
        ".mercury/credentials.env",
        ".mercury/cache/",
        ".mercury/audit/",
    ]
    if os.name == "posix":
        assert stat.S_IMODE(context.mercury_dir.stat().st_mode) == 0o700


def test_multiple_roots_require_explicit_selection(tmp_path: Path) -> None:
    roots = (tmp_path / "a", tmp_path / "b")
    for root in roots:
        root.mkdir()

    with pytest.raises(ValueError, match="multiple_mcp_roots"):
        resolve_repository_root(None, roots)


def test_requested_root_must_be_inside_an_mcp_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(ValueError, match="repo_root_outside_mcp_roots"):
        resolve_repository_root(outside, (allowed,))


def test_root_paths_accept_file_uris_only(tmp_path: Path) -> None:
    assert root_paths((tmp_path.as_uri(),)) == (tmp_path.resolve(),)
    with pytest.raises(ValueError, match="unsupported_root_uri"):
        root_paths(("https://example.com/repo",))


def test_custom_connector_configuration_pins_driver_and_host(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    config = configure_connector(
        context,
        connector_id="custom-books",
        environment="production",
        driver_id="api_key_header",
        base_url="https://api.example-books.com/v2",
        auth_settings={"key_name": "X-API-Key"},
    )
    selected = config.connectors["custom-books"]["production"]
    assert selected["driver_id"] == "api_key_header"
    assert selected["base_url"] == "https://api.example-books.com/v2"
    assert config.trusted_hosts["custom-books"]["production"] == ("api.example-books.com",)
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run: `uv run pytest tests/test_local_repository.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'mercury_tools.local'`.

- [ ] **Step 3: Implement root selection and deterministic scaffold**

```python
@dataclass(frozen=True)
class RepositoryContext:
    repository_id: str
    root: Path
    mercury_dir: Path
    config_path: Path
    credentials_path: Path
    catalog_dir: Path
    cache_dir: Path
    audit_dir: Path


@dataclass(frozen=True)
class RepositoryConfig:
    schema_version: int = 1
    trusted_hosts: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    connectors: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)


def resolve_repository_root(
    requested: str | Path | None,
    roots: Sequence[Path],
) -> Path:
    resolved_roots = tuple(Path(root).expanduser().resolve() for root in roots)
    if not resolved_roots:
        raise ValueError("mcp_roots_required")
    if requested is None:
        if len(resolved_roots) != 1:
            raise ValueError("multiple_mcp_roots")
        return resolved_roots[0]

    candidate = Path(requested).expanduser().resolve()
    if not any(candidate == root or candidate.is_relative_to(root) for root in resolved_roots):
        raise ValueError("repo_root_outside_mcp_roots")
    return candidate


def ensure_repository_state(root: Path) -> RepositoryContext:
    root = root.resolve()
    mercury = root / ".mercury"
    for directory in (mercury, mercury / "catalog/sources", mercury / "catalog/actions",
                      mercury / "cache", mercury / "audit"):
        directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        mercury.chmod(0o700)

    config_path = mercury / "config.json"
    if not config_path.exists():
        config_path.write_text(
        json.dumps(
            {"schema_version": 1, "trusted_hosts": {}, "connectors": {}},
            indent=2,
        ) + "\n"
        )

    ignore_path = root / ".gitignore"
    existing = ignore_path.read_text().splitlines() if ignore_path.exists() else []
    required = [".mercury/credentials.env", ".mercury/cache/", ".mercury/audit/"]
    ignore_path.write_text("\n".join([*existing, *(item for item in required if item not in existing)]) + "\n")

    return RepositoryContext(
        repository_id="repo_" + hashlib.sha256(str(root).encode()).hexdigest()[:16],
        root=root,
        mercury_dir=mercury,
        config_path=config_path,
        credentials_path=mercury / "credentials.env",
        catalog_dir=mercury / "catalog",
        cache_dir=mercury / "cache",
        audit_dir=mercury / "audit",
    )
```

Implement `root_paths` with `urllib.parse.urlparse` and `unquote`. Implement config writes through a temporary file in the same directory followed by `os.replace`. `configure_connector` stores only non-secret `driver_id`, exact API/auth hosts, auth parameter names, and network policy. It must validate and pin every host that may receive credentials, including a generic OAuth token host when different from the API host. All internet URLs must use HTTPS unless the user explicitly enables `allow_private_network: true` for a `local` or `gateway` environment.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_local_repository.py -q`

Expected: `5 passed`.

- [ ] **Step 5: Commit the repository-state boundary**

```bash
git add src/mercury_tools/local tests/test_local_repository.py
git commit -m "feat: add repository-local Mercury state"
```

### Task 2: Atomic Repository Credential Store

**Files:**
- Create: `src/mercury_tools/drivers/__init__.py`
- Create: `src/mercury_tools/drivers/models.py`
- Create: `src/mercury_tools/local/credentials.py`
- Test: `tests/test_local_credentials.py`

**Interfaces:**
- Consumes: `RepositoryContext` from Task 1.
- Produces:
  - `CredentialField(name: str, secret: bool, label: str)`
  - `CredentialStatus(connector_id, environment, required_fields, present_fields, missing_fields, configured)`
  - `CredentialStore.status(connector_id, environment, fields) -> CredentialStatus`
  - `CredentialStore.save(connector_id, environment, values, fields) -> CredentialStatus`
  - `CredentialStore.load(connector_id, environment, fields) -> dict[str, str]`
  - `CredentialStore.clear(connector_id=None, environment=None, clear_all=False) -> int`
  - `credential_env_name(connector_id, environment, field) -> str`
  - `CredentialStatus.public_dict() -> dict[str, Any]` containing names and presence flags only

- [ ] **Step 1: Write failing credential lifecycle and redaction tests**

```python
from pathlib import Path

import pytest

from mercury_tools.drivers.models import CredentialField
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import ensure_repository_state


FIELDS = (
    CredentialField("client_id", secret=False, label="Client ID"),
    CredentialField("client_secret", secret=True, label="Client Secret"),
)


def test_save_quotes_values_and_uses_posix_0600(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    status = store.save(
        "flowaccount",
        "production",
        {"client_id": "client one", "client_secret": 'a"b\\nc'},
        FIELDS,
    )

    text = context.credentials_path.read_text()
    assert 'MERCURY_FLOWACCOUNT_PRODUCTION_CLIENT_ID="client one"' in text
    assert 'MERCURY_FLOWACCOUNT_PRODUCTION_CLIENT_SECRET="a\\"b\\\\nc"' in text
    assert status.configured is True
    if os.name == "posix":
        assert stat.S_IMODE(context.credentials_path.stat().st_mode) == 0o600


def test_load_returns_only_requested_profile(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    store.save("flowaccount", "production", {"client_id": "id", "client_secret": "secret"}, FIELDS)
    store.save("flowaccount", "sandbox", {"client_id": "test", "client_secret": "test-secret"}, FIELDS)

    assert store.load("flowaccount", "production", FIELDS) == {
        "client_id": "id",
        "client_secret": "secret",
    }


def test_clear_profile_preserves_other_profiles(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    store.save("flowaccount", "production", {"client_id": "id", "client_secret": "secret"}, FIELDS)
    store.save("flowaccount", "sandbox", {"client_id": "test", "client_secret": "test-secret"}, FIELDS)

    assert store.clear("flowaccount", "production") == 2
    assert store.status("flowaccount", "production", FIELDS).configured is False
    assert store.status("flowaccount", "sandbox", FIELDS).configured is True


def test_control_characters_are_rejected(tmp_path: Path) -> None:
    store = CredentialStore(ensure_repository_state(tmp_path))
    with pytest.raises(ValueError, match="credential_control_character"):
        store.save("flowaccount", "production", {"client_id": "id", "client_secret": "bad\nvalue"}, FIELDS)
    with pytest.raises(ValueError, match="credential_control_character"):
        store.save("flowaccount", "production", {"client_id": "id", "client_secret": "bad\u200bvalue"}, FIELDS)
```

- [ ] **Step 2: Run the tests and verify missing interfaces**

Run: `uv run pytest tests/test_local_credentials.py -q`

Expected: FAIL during collection because `CredentialField` and `CredentialStore` do not exist.

- [ ] **Step 3: Implement the credential naming and atomic writer**

```python
@dataclass(frozen=True)
class CredentialField:
    name: str
    secret: bool
    label: str


def credential_env_name(connector_id: str, environment: str, field: str) -> str:
    parts = (connector_id, environment, field)
    normalized = ["".join(ch if ch.isalnum() else "_" for ch in part).upper() for part in parts]
    return "MERCURY_" + "_".join(normalized)


def _quote_dotenv(value: str) -> str:
    if any(
        ord(char) < 32
        or ord(char) == 127
        or unicodedata.category(char) in {"Cc", "Cf"}
        for char in value
    ):
        raise ValueError("credential_control_character")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temp_name)
```

Use `dotenv_values` to parse the file without exporting values into `os.environ`. Sort output keys lexically, never retain a process-global credential cache, and make `clear(clear_all=True)` unlink the file. `status` returns field names only.

- [ ] **Step 4: Run credential and redaction tests**

Run: `uv run pytest tests/test_local_credentials.py tests/test_redaction.py -q`

Expected: all tests PASS and no assertion output contains `secret` values.

- [ ] **Step 5: Commit the credential store**

```bash
git add src/mercury_tools/drivers src/mercury_tools/local/credentials.py tests/test_local_credentials.py
git commit -m "feat: store ERP credentials per repository"
```

### Task 3: Immutable Action Catalog Models and Local Stores

**Files:**
- Create: `src/mercury_tools/catalog/__init__.py`
- Create: `src/mercury_tools/catalog/models.py`
- Create: `src/mercury_tools/catalog/identity.py`
- Create: `src/mercury_tools/catalog/local_store.py`
- Create: `src/mercury_tools/catalog/cache.py`
- Create: `tests/conftest.py`
- Test: `tests/test_action_catalog_models.py`
- Test: `tests/test_catalog_stores.py`

**Interfaces:**
- Consumes: `RepositoryContext.catalog_dir` and `cache_dir`.
- Produces:
  - `HttpMethod`, `RiskTier`, `ActionConfidence`, `ObservedState` enums
  - `CatalogSource` and `CatalogAction` Pydantic models
  - `build_source_id(connector_id, source_uri, source_hash) -> str`
  - `build_action_id(...) -> str` and `build_version_id(action) -> str`
  - `CatalogSource.from_document(uri, connector_id, document, report) -> CatalogSource`
  - `LocalCatalogStore.write_import(source, actions) -> None`
  - `LocalCatalogStore.list_actions() -> list[CatalogAction]`
  - `CatalogCache.replace_global(actions, etag) -> None`
  - `CatalogCache.list_global() -> list[CatalogAction]`
  - `CatalogCache.conditional_headers() -> dict[str, str]`
  - `merge_actions(global_actions, local_actions) -> list[CatalogAction]`

- [ ] **Step 1: Write failing identity, immutability, and precedence tests**

```python
from mercury_tools.catalog.identity import build_action_id, build_version_id
from mercury_tools.catalog.models import CatalogAction, RiskTier
from mercury_tools.catalog.local_store import merge_actions


def action(*, source_uri: str, description: str) -> CatalogAction:
    base = CatalogAction(
        action_id="",
        version_id="",
        connector_id="flowaccount",
        environments=("production", "sandbox"),
        method="POST",
        path_template="/invoices",
        operation_id="createInvoice",
        variant_id="simple",
        content_type="application/json",
        aliases_th=("สร้างใบแจ้งหนี้",),
        aliases_en=("create invoice",),
        capability="documents.invoice.create",
        input_schema={"path": {}, "query": {}, "headers": {}, "body": {"type": "object"}, "files": {}},
        examples=(),
        risk_tier=RiskTier.STANDARD_WRITE,
        required_confirmations=1,
        side_effects=("creates_document",),
        preflight_action_ids=(),
        idempotency={},
        success_rules={},
        error_rules={},
        response_redaction=(),
        source_uri=source_uri,
        source_hash="a" * 64,
        confidence="exact",
        observed_state="untested",
        description=description,
    )
    action_id = build_action_id(base)
    return base.model_copy(
        update={"action_id": action_id, "version_id": build_version_id(base.model_copy(update={"action_id": action_id}))}
    )


def test_action_id_is_stable_but_version_changes_with_content() -> None:
    first = action(source_uri="global://flow", description="Create invoice")
    second = action(source_uri="local://flow", description="Create invoice with project")
    assert first.action_id == second.action_id
    assert first.version_id != second.version_id


def test_local_action_wins_for_same_action_identity() -> None:
    global_action = action(source_uri="global://flow", description="global")
    local_action = action(source_uri="local://flow", description="local")
    merged = merge_actions([global_action], [local_action])
    assert len(merged) == 1
    assert merged[0].description == "local"
```

- [ ] **Step 2: Run model tests and verify failure**

Run: `uv run pytest tests/test_action_catalog_models.py tests/test_catalog_stores.py -q`

Expected: FAIL during collection with `No module named 'mercury_tools.catalog'`.

- [ ] **Step 3: Implement the catalog contract and canonical IDs**

```python
class RiskTier(IntEnum):
    SAFE_READ = 0
    STANDARD_WRITE = 1
    HIGH_RISK = 2


class CatalogSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    connector_id: str
    source_type: Literal["openapi3", "swagger2", "postman2.1", "documentation"]
    source_uri: str
    source_hash: str
    imported_version: str
    imported_at: datetime
    driver_suggestion: dict[str, Any] = Field(default_factory=dict)
    sanitization: dict[str, Any]


class CatalogAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    version_id: str
    connector_id: str
    environments: tuple[str, ...]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path_template: str
    operation_id: str
    variant_id: str = "default"
    content_type: str = "application/json"
    aliases_th: tuple[str, ...] = ()
    aliases_en: tuple[str, ...] = ()
    capability: str
    input_schema: dict[str, Any]
    examples: tuple[dict[str, Any], ...] = ()
    risk_tier: RiskTier
    required_confirmations: int
    side_effects: tuple[str, ...] = ()
    preflight_action_ids: tuple[str, ...] = ()
    idempotency: dict[str, Any] = Field(default_factory=dict)
    success_rules: dict[str, Any] = Field(default_factory=dict)
    error_rules: dict[str, Any] = Field(default_factory=dict)
    response_redaction: tuple[str, ...] = ()
    source_uri: str
    source_hash: str
    confidence: Literal["exact", "example_derived", "inferred"]
    observed_state: Literal["untested", "success", "failed", "outcome_unknown"]
    description: str = ""


def build_action_id(action: CatalogAction) -> str:
    identity = "|".join(
        (
            action.connector_id.lower(),
            action.method,
            action.path_template,
            action.operation_id,
            action.variant_id,
        )
    )
    return "act_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def build_source_id(connector_id: str, source_uri: str, source_hash: str) -> str:
    identity = f"{connector_id.casefold()}|{source_uri}|{source_hash}"
    return "src_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def build_version_id(action: CatalogAction) -> str:
    data = action.model_dump(mode="json", exclude={"version_id"})
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "av_" + hashlib.sha256(canonical.encode()).hexdigest()
```

`LocalCatalogStore` writes one sanitized `CatalogSource` JSON file under `sources/` and one canonical action JSON file per `action_id` under `actions/` using temporary-file replacement. `CatalogCache` stores Cloud rows and ETag in `.mercury/cache/catalog.sqlite`. `merge_actions` sorts by `(connector_id, capability, method, path_template, variant_id)` after local precedence.

Add `action_factory`, `catalog_source`, and `catalog_action` fixtures to `tests/conftest.py`. `action_factory(**overrides)` must construct a complete valid `CatalogAction`, recompute `action_id` and `version_id` after overrides, and return an immutable model. Later tasks reuse this fixture instead of duplicating incomplete actions.

- [ ] **Step 4: Run catalog tests**

Run: `uv run pytest tests/test_action_catalog_models.py tests/test_catalog_stores.py -q`

Expected: all catalog tests PASS.

- [ ] **Step 5: Commit the catalog foundation**

```bash
git add src/mercury_tools/catalog tests/conftest.py tests/test_action_catalog_models.py tests/test_catalog_stores.py
git commit -m "feat: add immutable ERP action catalog"
```

### Task 4: Specification Sanitization and Four Importers

**Files:**
- Create: `src/mercury_tools/catalog/importers/__init__.py`
- Create: `src/mercury_tools/catalog/importers/sanitize.py`
- Create: `src/mercury_tools/catalog/importers/openapi.py`
- Create: `src/mercury_tools/catalog/importers/postman.py`
- Create: `src/mercury_tools/catalog/importers/markdown.py`
- Create: `src/mercury_tools/catalog/importers/service.py`
- Create: `src/mercury_tools/safety/network.py`
- Create: `tests/fixtures/catalog/openapi3.json`
- Create: `tests/fixtures/catalog/swagger2.yaml`
- Create: `tests/fixtures/catalog/postman21.json`
- Create: `tests/fixtures/catalog/endpoints.md`
- Test: `tests/test_catalog_importers.py`
- Test: `tests/test_catalog_sanitization.py`

**Interfaces:**
- Consumes: `CatalogSource`, `CatalogAction`, ID builders, and `LocalCatalogStore`.
- Produces:
  - `sanitize_spec(value: Any) -> tuple[Any, SanitizationReport]`
  - `parse_openapi(document, source, connector_id) -> list[CatalogAction]`
  - `parse_postman(document, source, connector_id) -> list[CatalogAction]`
  - `parse_markdown(text, source, connector_id) -> list[CatalogAction]`
  - `import_spec(context, connector_id, source_path=None, source_url=None) -> ImportResult`
  - `ImportResult(source: CatalogSource, actions: tuple[CatalogAction, ...], sanitization: SanitizationReport)`

- [ ] **Step 1: Write failing multi-format and secret-removal tests**

```python
from pathlib import Path

from mercury_tools.catalog.importers.service import import_spec
from mercury_tools.local.repository import ensure_repository_state


FIXTURES = Path(__file__).parent / "fixtures" / "catalog"


@pytest.mark.parametrize(
    ("filename", "expected_method", "expected_confidence"),
    [
        ("openapi3.json", "GET", "exact"),
        ("swagger2.yaml", "POST", "exact"),
        ("postman21.json", "POST", "example_derived"),
        ("endpoints.md", "DELETE", "inferred"),
    ],
)
def test_supported_formats_normalize_actions(
    tmp_path: Path,
    filename: str,
    expected_method: str,
    expected_confidence: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    result = import_spec(
        context,
        connector_id="custom",
        source_path=FIXTURES / filename,
    )
    assert result.actions[0].method == expected_method
    assert result.actions[0].confidence == expected_confidence
    assert result.actions[0].version_id.startswith("av_")


def test_postman_secrets_are_removed_from_source_and_examples(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    result = import_spec(context, connector_id="custom", source_path=FIXTURES / "postman21.json")
    serialized = json.dumps(result.model_dump(mode="json"))
    assert "Bearer real-token" not in serialized
    assert "secret@example.com" not in serialized
    assert "[REDACTED]" in serialized


def test_remote_import_blocks_metadata_target(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    with pytest.raises(NetworkPolicyError):
        import_spec(
            context,
            connector_id="custom",
            source_url="https://169.254.169.254/openapi.json",
        )
```

The fixtures must each contain one endpoint, explicit schemas, and fake secret-like values such as `Bearer real-token` so the test proves removal.

- [ ] **Step 2: Run importer tests and verify failure**

Run: `uv run pytest tests/test_catalog_importers.py tests/test_catalog_sanitization.py -q`

Expected: FAIL during collection because the importer package does not exist.

- [ ] **Step 3: Implement format detection, sanitization, and normalization**

```python
SECRET_KEY = re.compile(
    r"(authorization|cookie|client[_-]?secret|api[_-]?key|access[_-]?token|password)",
    re.IGNORECASE,
)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def sanitize_spec(value: Any) -> tuple[Any, SanitizationReport]:
    removed = 0

    def visit(node: Any, key: str = "") -> Any:
        nonlocal removed
        if SECRET_KEY.search(key):
            removed += 1
            return "[REDACTED]"
        if isinstance(node, dict):
            return {str(k): visit(v, str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [visit(item) for item in node]
        if isinstance(node, str):
            clean = EMAIL.sub("[REDACTED_EMAIL]", node)
            clean = redact_json(clean)
            removed += int(clean != node)
            return clean
        return node

    return visit(value), SanitizationReport(redacted_values=removed, safe=True)


def import_spec(
    context: RepositoryContext,
    *,
    connector_id: str,
    source_path: str | Path | None = None,
    source_url: str | None = None,
) -> ImportResult:
    if (source_path is None) == (source_url is None):
        raise ValueError("exactly_one_spec_source_required")
    raw, uri = read_spec_source(context, source_path=source_path, source_url=source_url)
    parsed = parse_json_or_yaml(raw)
    sanitized, report = sanitize_spec(parsed)
    source = CatalogSource.from_document(uri=uri, connector_id=connector_id, document=sanitized, report=report)
    actions = dispatch_parser(sanitized, raw, source, connector_id)
    LocalCatalogStore(context).write_import(source, actions)
    return ImportResult(source=source, actions=tuple(actions), sanitization=report)
```

`read_spec_source` must reject local files outside `context.root`, require HTTPS URLs, disable redirects, enforce the shared `NetworkPolicy` from `safety/network.py`, and cap source size at 10 MiB. OpenAPI takes precedence over Swagger, then Postman 2.1, then documentation text. Parse OpenAPI/Swagger security schemes into non-secret driver suggestions (`bearer`, `api_key_header`, `api_key_query`, `basic`, or `oauth_client_credentials`) and keep them on `CatalogSource`; do not add a host to trusted configuration during import.

Preserve credential field names and schema descriptions, because they are required for setup. Redact only values, examples, defaults, headers, cookies, and collection variables under sensitive keys. A schema property named `client_secret` may remain; a `client_secret` value may not.

- [ ] **Step 4: Run importer tests**

Run: `uv run pytest tests/test_catalog_importers.py tests/test_catalog_sanitization.py -q`

Expected: all importer tests PASS with exact, example-derived, and inferred confidence values.

- [ ] **Step 5: Commit the import pipeline**

```bash
git add src/mercury_tools/catalog/importers src/mercury_tools/safety/network.py tests/fixtures/catalog tests/test_catalog_importers.py tests/test_catalog_sanitization.py
git commit -m "feat: import ERP endpoint specifications"
```

### Task 5: Supabase Global Catalog and Trusted Publisher

**Files:**
- Create: `supabase/migrations/20260711090000_erp_action_catalog.sql`
- Create: `src/mercury_tools/db/catalog.py`
- Create: `scripts/publish_catalog.py`
- Create: `.github/workflows/publish-catalog.yml`
- Test: `tests/test_catalog_migration.py`
- Test: `tests/test_catalog_publisher.py`

**Interfaces:**
- Consumes: canonical `CatalogSource` and `CatalogAction` JSON.
- Produces:
  - Supabase tables `erp_spec_sources`, `erp_action_catalog`, `erp_action_versions`, `erp_action_observations`
  - `SupabaseCatalogStore.publish(source, actions) -> PublishResult`
  - `SupabaseCatalogStore.list_active_actions(filters) -> list[CatalogAction]`
  - `python scripts/publish_catalog.py --path catalog/global` entrypoint

- [ ] **Step 1: Write failing migration contract and publisher idempotency tests**

```python
from pathlib import Path


def test_catalog_migration_has_immutable_versions_and_service_role_only() -> None:
    sql = Path("supabase/migrations/20260711090000_erp_action_catalog.sql").read_text()
    assert "create table if not exists public.erp_spec_sources" in sql
    assert "create table if not exists public.erp_action_catalog" in sql
    assert "create table if not exists public.erp_action_versions" in sql
    assert "create table if not exists public.erp_action_observations" in sql
    assert "unique (action_id, version_id)" in sql
    assert "erp_action_versions_are_immutable" in sql
    assert "revoke all on table public.erp_action_versions from anon, authenticated" in sql
    assert "grant all on table public.erp_action_versions to service_role" in sql


def test_publish_same_version_twice_is_idempotent(fake_supabase, catalog_source, catalog_action) -> None:
    store = SupabaseCatalogStore(fake_supabase.settings)
    first = store.publish(catalog_source, [catalog_action])
    second = store.publish(catalog_source, [catalog_action])
    assert first.created_versions == 1
    assert second.created_versions == 0
    assert second.activated_actions == 1
```

- [ ] **Step 2: Run migration and publisher tests**

Run: `uv run pytest tests/test_catalog_migration.py tests/test_catalog_publisher.py -q`

Expected: FAIL because the migration and `SupabaseCatalogStore` are absent.

- [ ] **Step 3: Add the normalized global schema and upsert store**

```sql
create table if not exists public.erp_spec_sources (
  source_id text primary key,
  connector_id text not null,
  source_type text not null check (source_type in ('openapi3','swagger2','postman2.1','documentation')),
  source_uri text not null,
  source_hash text not null,
  imported_version text not null,
  sanitization jsonb not null,
  metadata jsonb not null default '{}'::jsonb,
  imported_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (connector_id, source_uri, source_hash)
);

create table if not exists public.erp_action_versions (
  id uuid primary key default gen_random_uuid(),
  action_id text not null,
  version_id text not null,
  connector_id text not null,
  method text not null check (method in ('GET','POST','PUT','PATCH','DELETE')),
  path_template text not null,
  definition jsonb not null,
  source_id text not null references public.erp_spec_sources(source_id) on delete restrict,
  created_at timestamptz not null default now(),
  unique (action_id, version_id)
);

create table if not exists public.erp_action_catalog (
  action_id text primary key,
  connector_id text not null,
  capability text not null,
  active_version_id text not null,
  updated_at timestamptz not null default now(),
  foreign key (action_id, active_version_id)
    references public.erp_action_versions(action_id, version_id)
    deferrable initially deferred
);

create table if not exists public.erp_action_observations (
  id uuid primary key default gen_random_uuid(),
  opaque_event_id text not null unique,
  action_id text not null,
  version_id text not null,
  connector_id text not null,
  method text not null check (method in ('GET','POST','PUT','PATCH','DELETE')),
  observed_state text not null check (observed_state in ('success','failed','outcome_unknown')),
  status_class text not null,
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key (action_id, version_id)
    references public.erp_action_versions(action_id, version_id)
    on delete restrict
);

create or replace function public.reject_erp_action_version_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'erp_action_versions_are_immutable';
end;
$$;

drop trigger if exists erp_action_versions_are_immutable on public.erp_action_versions;
create trigger erp_action_versions_are_immutable
before update or delete on public.erp_action_versions
for each row execute function public.reject_erp_action_version_mutation();

alter table public.erp_spec_sources enable row level security;
alter table public.erp_action_catalog enable row level security;
alter table public.erp_action_versions enable row level security;
alter table public.erp_action_observations enable row level security;

revoke all on table public.erp_spec_sources from anon, authenticated;
revoke all on table public.erp_action_catalog from anon, authenticated;
revoke all on table public.erp_action_versions from anon, authenticated;
revoke all on table public.erp_action_observations from anon, authenticated;

grant all on table public.erp_spec_sources to service_role;
grant all on table public.erp_action_catalog to service_role;
grant all on table public.erp_action_versions to service_role;
grant all on table public.erp_action_observations to service_role;
```

Add indexes on `(connector_id, method)`, `(connector_id, capability)`, and observation `(action_id, created_at desc)`. `publish` inserts versions with `on_conflict=action_id,version_id` plus `Prefer: resolution=ignore-duplicates`, then activates catalog rows with merge-duplicates; it never updates version rows. Reject unsanitized source reports. Observation metadata is schema-checked against the keys `source`, `reviewed_by`, and `note`; payload, repository, credential, and provider-record keys are rejected. Only reviewed CI artifacts may add observations in v0.2.0; the local runtime does not upload telemetry.

- [ ] **Step 4: Add the GitHub-only publisher workflow and run tests**

```yaml
name: Publish ERP Action Catalog

on:
  workflow_dispatch:
  push:
    paths:
      - "catalog/global/**"
      - "src/mercury_tools/catalog/**"
      - "scripts/publish_catalog.py"

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --no-dev
      - run: uv run python scripts/publish_catalog.py --path catalog/global
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
```

Run: `uv run pytest tests/test_catalog_migration.py tests/test_catalog_publisher.py -q`

Expected: all tests PASS, and the serialized workflow contains no secret values.

Use the connected Supabase MCP tool `supabase_apply_migration` with `project_id="vbnlkqvauqwnjbxngkas"`, `name="erp_action_catalog_v020"`, and `query` equal to the complete contents of `supabase/migrations/20260711090000_erp_action_catalog.sql`. Then use `supabase_execute_sql` on the same project with:

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'erp_spec_sources',
    'erp_action_catalog',
    'erp_action_versions',
    'erp_action_observations'
  )
order by table_name;
```

Expected: exactly four rows, one for each catalog table.

- [ ] **Step 5: Commit global catalog publication**

```bash
git add supabase/migrations/20260711090000_erp_action_catalog.sql src/mercury_tools/db/catalog.py scripts/publish_catalog.py .github/workflows/publish-catalog.yml tests/test_catalog_migration.py tests/test_catalog_publisher.py
git commit -m "feat: publish immutable ERP action catalog"
```


### Task 6: Deterministic Search and Risk Classification

**Files:**
- Create: `src/mercury_tools/catalog/search.py`
- Create: `src/mercury_tools/execution/__init__.py`
- Create: `src/mercury_tools/execution/policy.py`
- Test: `tests/test_catalog_search.py`
- Test: `tests/test_execution_policy.py`

**Interfaces:**
- Consumes: merged `CatalogAction` rows.
- Produces:
  - `CatalogMatch(action: CatalogAction, rank_bucket: int, score: float, reasons: tuple[str, ...])`
  - `CatalogSearchResponse(matches: tuple[CatalogMatch, ...], ambiguous: bool)`
  - `search_actions(actions, query, connector=None, method=None, risk_tier=None, top_k=8, semantic_scores=None)`
  - `effective_risk(action: CatalogAction) -> RiskDecision`

- [ ] **Step 1: Write failing ranking and policy tests**

```python
def test_exact_capability_beats_alias_and_keywords(action_factory) -> None:
    exact = action_factory(capability="documents.invoice.create", aliases_en=("issue invoice",))
    alias = action_factory(capability="documents.receipt.create", aliases_en=("documents invoice create",))
    result = search_actions([alias, exact], "documents.invoice.create")
    assert result.matches[0].action.action_id == exact.action_id
    assert result.matches[0].rank_bucket == 1
    assert result.ambiguous is False


def test_equal_alias_candidates_are_ambiguous(action_factory) -> None:
    first = action_factory(operation_id="one", aliases_th=("บันทึกชำระเงิน",))
    second = action_factory(operation_id="two", aliases_th=("บันทึกชำระเงิน",))
    result = search_actions([first, second], "บันทึกชำระเงิน")
    assert result.ambiguous is True
    assert len(result.matches) == 2


def test_semantic_score_breaks_keyword_tie_only_inside_same_bucket(action_factory) -> None:
    first = action_factory(operation_id="one", aliases_en=("record document",))
    second = action_factory(operation_id="two", aliases_en=("record document",))
    result = search_actions(
        [first, second],
        "book supplier bill",
        semantic_scores={first.action_id: 0.20, second.action_id: 0.91},
    )
    assert result.matches[0].action.action_id == second.action_id


@pytest.mark.parametrize(
    ("method", "side_effects", "confidence", "observed", "tier", "confirmations"),
    [
        ("GET", (), "exact", "success", 0, 0),
        ("POST", ("creates_document",), "exact", "success", 1, 1),
        ("PATCH", ("updates_document",), "exact", "success", 1, 1),
        ("DELETE", ("deletes_document",), "exact", "success", 2, 2),
        ("POST", ("payment",), "exact", "success", 2, 2),
        ("POST", ("creates_document",), "inferred", "untested", 2, 2),
    ],
)
def test_runtime_risk_floor(action_factory, method, side_effects, confidence, observed, tier, confirmations) -> None:
    action = action_factory(
        method=method,
        side_effects=side_effects,
        confidence=confidence,
        observed_state=observed,
        risk_tier=0,
        required_confirmations=0,
    )
    decision = effective_risk(action)
    assert int(decision.tier) == tier
    assert decision.required_confirmations == confirmations
```

- [ ] **Step 2: Run search and policy tests**

Run: `uv run pytest tests/test_catalog_search.py tests/test_execution_policy.py -q`

Expected: FAIL because `search_actions` and `effective_risk` are missing.

- [ ] **Step 3: Implement the exact ranking buckets and risk floor**

```python
HIGH_RISK_EFFECTS = frozenset(
    {"payment", "approve", "void", "delete", "email", "share", "invite"}
)


def effective_risk(action: CatalogAction) -> RiskDecision:
    runtime_tier = RiskTier.SAFE_READ if action.method == "GET" else RiskTier.STANDARD_WRITE
    reasons: list[str] = []
    if action.method == "DELETE" or HIGH_RISK_EFFECTS.intersection(action.side_effects):
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("high_risk_side_effect")
    if action.method != "GET" and action.confidence == "inferred" and action.observed_state == "untested":
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("inferred_unobserved_mutation")
    tier = max(action.risk_tier, runtime_tier)
    confirmations = max(action.required_confirmations, 0 if tier == 0 else 1 if tier == 1 else 2)
    return RiskDecision(tier=tier, required_confirmations=confirmations, reasons=tuple(reasons))


def rank_bucket(action: CatalogAction, query: str) -> int:
    normalized = query.casefold().strip()
    if normalized in {action.action_id.casefold(), action.capability.casefold()}:
        return 1
    aliases = {item.casefold().strip() for item in (*action.aliases_th, *action.aliases_en)}
    if normalized in aliases:
        return 2
    if action.connector_id.casefold() in normalized or action.capability.split(".")[0] in normalized:
        return 3
    return 4
```

Within each bucket, score normalized token overlap, then optional semantic score, then stable `action_id` order. Semantic scores come from Cloud RAG endpoint-dictionary chunks whose metadata contains `action_id`; they never override exact ID/capability/alias buckets. Mark `ambiguous=True` when the top two candidates share buckets 1-3 or their bucket-4 score difference is less than `0.05`. Never select one action automatically when ambiguous.

- [ ] **Step 4: Run search and policy tests**

Run: `uv run pytest tests/test_catalog_search.py tests/test_execution_policy.py -q`

Expected: all parameterized cases PASS.

- [ ] **Step 5: Commit search and policy**

```bash
git add src/mercury_tools/catalog/search.py src/mercury_tools/execution tests/test_catalog_search.py tests/test_execution_policy.py
git commit -m "feat: rank catalog actions and enforce risk tiers"
```

### Task 7: Connector Driver Contract and Generic Auth Drivers

**Files:**
- Modify: `src/mercury_tools/drivers/models.py`
- Create: `src/mercury_tools/drivers/base.py`
- Create: `src/mercury_tools/drivers/generic.py`
- Create: `src/mercury_tools/drivers/registry.py`
- Test: `tests/test_connector_driver_contract.py`
- Test: `tests/test_generic_drivers.py`

**Interfaces:**
- Consumes: `CredentialField` and `CatalogAction`.
- Produces:
  - `ConnectorDriver` protocol
  - `AuthContext(headers, query, expires_at)`
  - `PreparedFile(field_name, path, filename, content_type)`
  - `ConnectionProbe(status, connector_id, environment, company_name, details)`
  - `ConnectorResult(status, http_status, data, summary, dispatched)`
  - `DriverRegistry.register(driver)` and `get(connector_id)`
  - `build_generic_registry() -> DriverRegistry`
  - Built-ins `bearer`, `api_key_header`, `api_key_query`, `basic`, `oauth_client_credentials`

- [ ] **Step 1: Write failing protocol and generic-driver tests**

```python
async def test_bearer_driver_adds_authorization_only_at_dispatch() -> None:
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.com/v1"},
    )
    auth = await driver.prepare_auth(
        environment="production",
        credentials={"token": "secret-token"},
        client=MockTransportClient(),
    )
    assert auth.headers == {"Authorization": "Bearer secret-token"}
    assert auth.query == {}


async def test_api_key_query_driver_rejects_unknown_environment() -> None:
    driver = GenericApiKeyDriver(
        connector_id="custom",
        placement="query",
        key_name="api_key",
        environments={"production": "https://erp.example.com"},
    )
    with pytest.raises(DriverConfigurationError, match="unsupported_environment"):
        driver.resolve_base_url("sandbox")


def test_registry_lists_credential_field_names_without_values() -> None:
    registry = build_generic_registry()
    summaries = registry.summaries()
    oauth = next(item for item in summaries if item["driver_id"] == "oauth_client_credentials")
    assert oauth["credential_fields"] == ["client_id", "client_secret"]
    assert "secret-token" not in json.dumps(summaries)
```

- [ ] **Step 2: Run driver contract tests**

Run: `uv run pytest tests/test_connector_driver_contract.py tests/test_generic_drivers.py -q`

Expected: FAIL because the driver protocol and registry are absent.

- [ ] **Step 3: Implement the async protocol and result types**

```python
class ConnectorDriver(Protocol):
    driver_id: str
    connector_id: str

    def credential_fields(self, environment: str) -> tuple[CredentialField, ...]: ...
    def resolve_base_url(self, environment: str) -> str: ...
    def safe_probe_action(self, environment: str) -> str: ...
    def prepare_files(
        self,
        *,
        action: CatalogAction,
        inputs: Mapping[str, Any],
        roots: Sequence[Path],
    ) -> tuple[PreparedFile, ...]: ...

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext: ...

    async def validate_credentials(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> ConnectionProbe: ...

    def interpret_response(
        self,
        *,
        action: CatalogAction,
        response: httpx.Response,
        dispatched: bool,
    ) -> ConnectorResult: ...

    def sanitize_response(self, action: CatalogAction, value: Any) -> Any: ...
```

The generic drivers must:
- resolve only configured environment base URLs;
- define exact credential fields;
- keep tokens inside an operation-scoped `AuthContext`;
- never include credential values in `ConnectionProbe` or exceptions;
- parse JSON when available and otherwise return a length-limited text summary;
- use provider body-level error rules from `CatalogAction.error_rules`.
- prepare `multipart/form-data` files only from paths inside active MCP roots;
- apply the action's response-redaction fields before returning provider data.

`build_generic_registry` contains the five generic driver factories without importing provider-specific modules. Imported security schemes may suggest a factory but cannot register or trust it automatically.

- [ ] **Step 4: Run generic driver tests**

Run: `uv run pytest tests/test_connector_driver_contract.py tests/test_generic_drivers.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit driver primitives**

```bash
git add src/mercury_tools/drivers tests/test_connector_driver_contract.py tests/test_generic_drivers.py
git commit -m "feat: add generic ERP connector drivers"
```

### Task 8: FlowAccount and PEAK Drivers

**Files:**
- Create: `src/mercury_tools/drivers/flowaccount.py`
- Create: `src/mercury_tools/drivers/peak.py`
- Modify: `src/mercury_tools/drivers/registry.py`
- Modify: `src/mercury_tools/connectors/setup.py:69-107,315-595`
- Test: `tests/test_flowaccount_driver.py`
- Test: `tests/test_peak_driver.py`
- Modify: `tests/test_connector_setup.py`
- Modify: `tests/test_flowaccount_journal_client.py`

**Interfaces:**
- Consumes: driver protocol and existing `ConnectorManifest` presets.
- Produces:
  - `FlowAccountDriver` with production token `https://openapi.flowaccount.com/v1/token` and safe probe `GET /company/info`
  - `PeakDriver` with HMAC-SHA1 ClientToken flow and safe probe `GET /user`
  - `DriverRegistry.for_repository(config: RepositoryConfig) -> DriverRegistry`
  - Existing `validate_connector_connection_healthcheck` delegates to the new drivers during migration.

- [ ] **Step 1: Write failing provider-specific authentication tests**

```python
async def test_flowaccount_production_uses_v1_token_and_company_probe() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(200, json={"companyName": "Example Co."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await FlowAccountDriver().validate_credentials(
            environment="production",
            credentials={"client_id": "id", "client_secret": "secret"},
            client=client,
        )
    assert probe.status == "connected"
    assert probe.company_name == "Example Co."
    assert calls == [
        ("POST", "https://openapi.flowaccount.com/v1/token"),
        ("GET", "https://openapi.flowaccount.com/v1/company/info"),
    ]


def test_peak_signature_matches_known_timestamp() -> None:
    assert peak_signature("20260711120000", "connect-id") == hmac.new(
        b"connect-id", b"20260711120000", hashlib.sha1
    ).hexdigest()


def test_peak_http_200_with_rescode_failure_is_not_success() -> None:
    response = httpx.Response(200, json={"PeakUser": {"resCode": "400", "resDesc": "invalid"}})
    result = PeakDriver().interpret_response(
        action=peak_user_action(),
        response=response,
        dispatched=True,
    )
    assert result.status == "failed"
    assert result.http_status == 200


def test_peak_fields_and_headers_include_application_code() -> None:
    driver = PeakDriver()
    assert [field.name for field in driver.credential_fields("production")] == [
        "connect_id",
        "connect_key",
        "application_code",
        "user_token",
    ]
    headers = peak_headers(
        timestamp="20260711120000",
        connect_id="connect-id",
        application_code="app-code",
        client_token="client-token",
        user_token="user-token",
    )
    assert headers["Application-Code"] == "app-code"
```

Use the existing mock transport convention in `tests/test_flowaccount_journal_client.py` rather than adding a new HTTP mocking dependency.

- [ ] **Step 2: Run provider driver tests**

Run: `uv run pytest tests/test_flowaccount_driver.py tests/test_peak_driver.py -q`

Expected: FAIL because `FlowAccountDriver` and `PeakDriver` are missing.

- [ ] **Step 3: Extract provider behavior into drivers**

```python
class FlowAccountDriver:
    driver_id = "flowaccount_oauth"
    connector_id = "flowaccount"
    BASE_URLS = {
        "production": "https://openapi.flowaccount.com/v1",
        "sandbox": "https://openapi.flowaccount.com/test",
    }
    TOKEN_URLS = {
        "production": "https://openapi.flowaccount.com/v1/token",
        "sandbox": "https://openapi.flowaccount.com/test/token",
    }

    def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
        self.resolve_base_url(environment)
        return (
            CredentialField("client_id", secret=False, label="FlowAccount Client ID"),
            CredentialField("client_secret", secret=True, label="FlowAccount Client Secret"),
        )

    async def prepare_auth(self, *, environment, credentials, client) -> AuthContext:
        response = await client.post(
            self.TOKEN_URLS[environment],
            data={
                "grant_type": "client_credentials",
                "scope": "flowaccount-api",
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
            },
        )
        payload = response.json()
        token = str(payload.get("access_token") or "")
        if response.status_code >= 300 or not token:
            raise ConnectorAuthError("flowaccount_token_failed")
        return AuthContext(headers={"Authorization": f"Bearer {token}"}, query={}, expires_at=None)
```

Move `_peak_timestamp`, `_peak_signature`, `_peak_headers`, `_peak_node`, and `_peak_success` into `drivers/peak.py` as `peak_timestamp`, `peak_signature`, `peak_headers`, `peak_node`, and `peak_success`. Include `Application-Code`, `Client-Token`, `User-Token`, `Time-Stamp`, and `Time-Signature` on PEAK requests. Keep thin imports in `connectors/setup.py` until all old hosted code is migrated; its synchronous compatibility function may use `asyncio.run` only when no event loop is active. Leave the old private journal client unchanged until Task 17 removes it.

Update `DriverRegistry.for_repository` in this task to start with FlowAccount, PEAK, and the five generic factories. Instantiate repository-defined connectors only from `RepositoryConfig.connectors`, and only when every API/auth host required by the selected environment is present in `trusted_hosts`.

- [ ] **Step 4: Run new and regression tests**

Run: `uv run pytest tests/test_flowaccount_driver.py tests/test_peak_driver.py tests/test_connector_setup.py tests/test_flowaccount_journal_client.py -q`

Expected: all tests PASS, including production `/v1/token`.

- [ ] **Step 5: Commit provider drivers**

```bash
git add src/mercury_tools/drivers src/mercury_tools/connectors/setup.py tests/test_flowaccount_driver.py tests/test_peak_driver.py tests/test_connector_setup.py tests/test_flowaccount_journal_client.py
git commit -m "feat: port FlowAccount and PEAK connector drivers"
```

### Task 9: Interactive Credential and Trusted-Host CLI

**Files:**
- Create: `src/mercury_tools/local/credential_cli.py`
- Modify: `src/mercury_tools/cli.py:13-30,56-81,625-755`
- Modify: `pyproject.toml:28-30`
- Test: `tests/test_credential_cli.py`
- Modify: `tests/test_cli_search.py`

**Interfaces:**
- Consumes: `CredentialStore`, `DriverRegistry`, repository state, driver probes.
- Produces these exact commands:
  - `mercury credentials setup <connector> --env <environment> [--repo-root PATH]`
  - `mercury credentials status [--repo-root PATH]`
  - `mercury credentials test <connector> --env <environment> [--repo-root PATH]`
  - `mercury credentials clear <connector> --env <environment> [--repo-root PATH]`
  - `mercury credentials clear --all [--repo-root PATH]`
  - `mercury connector configure <connector> --env <environment> --driver <driver_id> --base-url HTTPS_URL [--key-name NAME] [--token-url HTTPS_URL]`
  - `mercury doctor [--repo-root PATH]`
  - `mercury mcp serve-local`

- [ ] **Step 1: Write failing interactive CLI tests**

```python
def test_setup_prompts_only_required_fields_and_hides_secret(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "visible-client")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "hidden-secret")
    code = main([
        "credentials", "setup", "flowaccount",
        "--env", "production",
        "--repo-root", str(tmp_path),
    ])
    output = capsys.readouterr().out
    assert code == 0
    assert "configured" in output
    assert "hidden-secret" not in output
    assert "visible-client" not in output


def test_status_never_prints_values(tmp_path: Path, capsys) -> None:
    seed_flow_credentials(tmp_path, client_id="visible-client", client_secret="hidden-secret")
    assert main(["credentials", "status", "--repo-root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "client_id" in output
    assert "client_secret" in output
    assert "visible-client" not in output
    assert "hidden-secret" not in output


def test_clear_all_removes_file(tmp_path: Path) -> None:
    seed_flow_credentials(tmp_path)
    assert main(["credentials", "clear", "--all", "--repo-root", str(tmp_path)]) == 0
    assert not (tmp_path / ".mercury/credentials.env").exists()


def test_custom_connector_requires_exact_host_confirmation(tmp_path: Path, monkeypatch) -> None:
    answers = iter(["trust api.example-books.com"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    code = main([
        "connector", "configure", "custom-books",
        "--env", "production",
        "--driver", "api_key_header",
        "--base-url", "https://api.example-books.com/v2",
        "--key-name", "X-API-Key",
        "--repo-root", str(tmp_path),
    ])
    assert code == 0
    config = load_repository_config(ensure_repository_state(tmp_path))
    assert config.trusted_hosts["custom-books"]["production"] == ("api.example-books.com",)
```

- [ ] **Step 2: Run CLI tests**

Run: `uv run pytest tests/test_credential_cli.py -q`

Expected: FAIL because the `credentials` parser does not exist.

- [ ] **Step 3: Implement focused command handlers and thin parser wiring**

```python
def cmd_credentials_setup(args: argparse.Namespace) -> int:
    context = ensure_repository_state(Path(args.repo_root))
    config = load_repository_config(context)
    driver = DriverRegistry.for_repository(config).get(args.connector)
    fields = driver.credential_fields(args.environment)
    values: dict[str, str] = {}
    for field in fields:
        prompt = f"{field.label}: "
        values[field.name] = getpass.getpass(prompt) if field.secret else input(prompt)
    status = CredentialStore(context).save(
        args.connector,
        args.environment,
        values,
        fields,
    )
    print(json.dumps(status.public_dict(), ensure_ascii=False))
    return 0


def add_credential_parsers(sub: argparse._SubParsersAction) -> None:
    credentials = sub.add_parser("credentials")
    commands = credentials.add_subparsers(dest="credentials_command", required=True)
    setup = commands.add_parser("setup")
    setup.add_argument("connector")
    setup.add_argument("--env", dest="environment", required=True)
    setup.add_argument("--repo-root", default=".")
    setup.set_defaults(func=cmd_credentials_setup)
```

Put all credential command handlers in `credential_cli.py` and only import `add_credential_parsers` in `cli.py`. Use `asyncio.run` for `credentials test`. On a successful safe probe, write only `connector_id`, environment, sanitized company display name, `validation_state="connected"`, probe action ID, and validation timestamp to `config.json`; never write tokens or credential fingerprints. `connector configure` prints every exact scheme and host, validates the driver plus non-secret `key_name`, `token_url`, `scope`, and grant type, requires the user to type `trust <space-separated-hosts>`, and then atomically calls `configure_connector`. Add `mercury = "mercury_tools.cli:main"` while retaining `mercury-tools`.

Extend `doctor` to report Python version, uvx availability, selected repo, POSIX permission strength, Cloud URL, local catalog count, configured connector names, and missing fields without values.

- [ ] **Step 4: Run CLI and legacy CLI tests**

Run: `uv run pytest tests/test_credential_cli.py tests/test_cli_search.py -q`

Expected: all tests PASS. Then run `uv run mercury --help` and expect `credentials`, `connector`, `mcp`, `flow`, `search`, and `doctor` in help output.

- [ ] **Step 5: Commit the local setup CLI**

```bash
git add src/mercury_tools/local/credential_cli.py src/mercury_tools/cli.py pyproject.toml tests/test_credential_cli.py tests/test_cli_search.py
git commit -m "feat: add local ERP credential commands"
```

### Task 10: Preview State Store and Local Audit Ledger

**Files:**
- Create: `src/mercury_tools/execution/models.py`
- Create: `src/mercury_tools/execution/store.py`
- Create: `src/mercury_tools/local/audit.py`
- Modify: `src/mercury_tools/local/credential_cli.py`
- Test: `tests/test_request_store.py`
- Test: `tests/test_local_audit.py`
- Modify: `tests/test_credential_cli.py`

**Interfaces:**
- Consumes: repository cache/audit paths, `CatalogAction`, and `RiskDecision`.
- Produces:
  - `RequestState` enum with approved transitions
  - `PreparedRequest` immutable preview
  - `PreparedRequest.from_template(repository, action, environment, request, risk, payload_hash) -> PreparedRequest`
  - `PreparedRequest.to_httpx_request(auth: AuthContext) -> httpx.Request`
  - `LocalRequestStore.get(request_id) -> PreparedRequest`
  - `LocalRequestStore.create_preview(...)`, `confirm(...)`, `invalidate(...)`, `require_ready(...)`, `fail_before_dispatch(...)`, `start_execution(...)`, `complete(...)`
  - `LocalRequestStore.invalidate_pending(connector_id=None, environment=None, reason="credentials_cleared") -> int`
  - `LocalRequestStore.assert_replay_allowed(payload_hash)`
  - `AuditLedger.record(event) -> str` returning an opaque event ID
  - `AuditLedger.get(event_id) -> dict[str, Any] | None`

- [ ] **Step 1: Write failing state, binding, replay, and redaction tests**

```python
def test_tier_two_needs_two_confirmations(request_store, prepared_request) -> None:
    request = request_store.create_preview(prepared_request.model_copy(
        update={"risk_tier": 2, "required_confirmations": 2}
    ))
    first = request_store.confirm(request.request_id, request.payload_hash)
    assert first.state == "awaiting_final_confirmation"
    second = request_store.confirm(request.request_id, request.payload_hash)
    assert second.state == "ready_to_execute"


def test_wrong_hash_invalidates_confirmation(request_store, prepared_request) -> None:
    request = request_store.create_preview(prepared_request)
    with pytest.raises(RequestStateError, match="payload_hash_mismatch"):
        request_store.confirm(request.request_id, "0" * 64)


def test_outcome_unknown_blocks_same_hash(request_store, prepared_request) -> None:
    request = request_store.create_preview(prepared_request)
    request_store.confirm(request.request_id, request.payload_hash)
    request_store.start_execution(request.request_id)
    request_store.complete(request.request_id, "outcome_unknown", {"status_class": "timeout"})
    with pytest.raises(RequestStateError, match="replay_blocked_outcome_unknown"):
        request_store.assert_replay_allowed(request.payload_hash)


def test_credential_clear_invalidates_pending_previews(request_store, prepared_request) -> None:
    request = request_store.create_preview(prepared_request)
    assert request_store.invalidate_pending("flowaccount", "production") == 1
    with pytest.raises(RequestStateError, match="credentials_cleared"):
        request_store.require_ready(request.request_id)


def test_clear_cli_invalidates_repository_request(tmp_path: Path, seeded_pending_request) -> None:
    request_id = seeded_pending_request(tmp_path, connector_id="flowaccount", environment="production")
    assert main([
        "credentials", "clear", "flowaccount",
        "--env", "production",
        "--repo-root", str(tmp_path),
    ]) == 0
    store = LocalRequestStore(ensure_repository_state(tmp_path))
    assert store.get(request_id).failure_reason == "credentials_cleared"


def test_audit_ledger_redacts_credentials_and_personal_fields(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    event_id = ledger.record({
        "connector_id": "flowaccount",
        "authorization": "Bearer token",
        "email": "person@example.com",
        "tax_id": "0105559999999",
    })
    text = (tmp_path / "audit.jsonl").read_text()
    assert "Bearer token" not in text
    assert "person@example.com" not in text
    assert "0105559999999" not in text
    assert ledger.get(event_id)["connector_id"] == "flowaccount"
```

- [ ] **Step 2: Run request and audit tests**

Run: `uv run pytest tests/test_request_store.py tests/test_local_audit.py -q`

Expected: FAIL because request state and audit modules are absent.

- [ ] **Step 3: Implement the SQLite state machine and canonical payload hash**

```python
class RequestState(StrEnum):
    PREVIEWED = "previewed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_FINAL_CONFIRMATION = "awaiting_final_confirmation"
    READY_TO_EXECUTE = "ready_to_execute"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class PreparedRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    repository_id: str
    connector_id: str
    environment: str
    action_id: str
    version_id: str
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    final_path: str
    sanitized_summary: dict[str, Any]
    request_inputs: dict[str, Any]
    payload_hash: str
    risk_tier: RiskTier
    required_confirmations: int
    confirmation_count: int = 0
    state: RequestState
    failure_reason: str | None = None
    response_summary: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
```

Create `requests.sqlite` with WAL mode and a unique partial index that blocks the same `payload_hash` while state is `executing`, `succeeded`, or `outcome_unknown`. Preview expiry is exactly 15 minutes. Every transition uses `BEGIN IMMEDIATE` and validates current state. Update the credential-clear CLI handler to invalidate matching pending requests and reset the non-secret connector validation state in `config.json`; clearing all invalidates every pending request.

`AuditLedger.record` assigns `evt_` plus 24 random hex characters, applies `redact_json` plus tax-ID and email redaction, writes one JSON object per line through `os.open(..., O_APPEND|O_CREAT|O_WRONLY, 0o600)`, and calls `os.fsync`. `get` scans by exact opaque event ID and returns the already-sanitized row. Record local session ID, action/version, payload hash, confirmation events, dispatch state, response summary, and optional artifact path; never store `request_inputs`.

- [ ] **Step 4: Run focused state and safety tests**

Run: `uv run pytest tests/test_request_store.py tests/test_local_audit.py tests/test_redaction.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit state and audit**

```bash
git add src/mercury_tools/execution/models.py src/mercury_tools/execution/store.py src/mercury_tools/local/audit.py src/mercury_tools/local/credential_cli.py tests/test_request_store.py tests/test_local_audit.py tests/test_credential_cli.py
git commit -m "feat: bind ERP write previews and local audit"
```


### Task 11: Network Boundary and Generic ERP Executor

**Files:**
- Modify: `src/mercury_tools/safety/network.py`
- Create: `src/mercury_tools/execution/request_builder.py`
- Create: `src/mercury_tools/execution/executor.py`
- Test: `tests/test_network_policy.py`
- Test: `tests/test_erp_executor.py`

**Interfaces:**
- Consumes: repository config, credential store, merged actions, driver registry, policy, request store, and audit ledger.
- Produces:
  - `NetworkPolicy.validate_base_url(url, allow_private_network=False) -> ValidatedTarget`
  - `build_request(action, base_url, inputs, roots) -> RequestTemplate`
  - `ERPExecutor.run_read(...) -> ConnectorResult`
  - `ERPExecutor.preview_write(...) -> PreparedRequest`
  - `ERPExecutor.confirm_write(request_id, payload_hash) -> PreparedRequest`
  - `ERPExecutor.execute_write(request_id) -> ConnectorResult`
  - `ERPExecutor.get_request_status(request_id) -> dict[str, Any]`
  - `ERPExecutor.resolve_unknown_with_status(request_id) -> ConnectorResult`

- [ ] **Step 1: Write failing SSRF, credential timing, and uncertain-outcome tests**

```python
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://127.0.0.1/api",
        "file:///etc/passwd",
    ],
)
def test_production_network_policy_blocks_metadata_private_and_non_http(url: str) -> None:
    with pytest.raises(NetworkPolicyError):
        NetworkPolicy().validate_base_url(url, allow_private_network=False)


def test_request_builder_rejects_traversal_and_unresolved_path(action_factory, tmp_path: Path) -> None:
    action = action_factory(path_template="/invoices/{id}")
    with pytest.raises(RequestBuildError, match="unresolved_path_parameter"):
        build_request(action, "https://erp.example.com", {"path": {}}, (tmp_path,))
    with pytest.raises(RequestBuildError, match="path_traversal"):
        build_request(action, "https://erp.example.com", {"path": {"id": "../admin"}}, (tmp_path,))


async def test_preview_does_not_load_credentials(executor, credential_store_spy, write_action) -> None:
    preview = await executor.preview_write(
        repository=executor.context,
        action=write_action,
        environment="production",
        inputs={"body": {"amount": 100}},
    )
    assert preview.state == "awaiting_confirmation"
    assert credential_store_spy.load_calls == 0


async def test_timeout_after_send_becomes_outcome_unknown(executor, confirmed_request, transport) -> None:
    transport.raise_on_send = httpx.ReadTimeout("timed out")
    result = await executor.execute_write(confirmed_request.request_id)
    assert result.status == "outcome_unknown"
    with pytest.raises(RequestStateError, match="replay_blocked_outcome_unknown"):
        executor.request_store.assert_replay_allowed(confirmed_request.payload_hash)


async def test_auth_failure_before_mutation_is_definitive(executor, confirmed_request, auth_transport) -> None:
    auth_transport.raise_on_token = httpx.ConnectError("token endpoint unavailable")
    result = await executor.execute_write(confirmed_request.request_id)
    assert result.status == "failed"
    assert result.dispatched is False


async def test_active_action_version_change_invalidates_confirmation(
    executor,
    confirmed_request,
    catalog,
) -> None:
    catalog.activate_new_version(confirmed_request.action_id)
    with pytest.raises(RequestStateError, match="preview_invalidated_action_version"):
        await executor.execute_write(confirmed_request.request_id)
```

- [ ] **Step 2: Run network and executor tests**

Run: `uv run pytest tests/test_network_policy.py tests/test_erp_executor.py -q`

Expected: FAIL because network policy and executor modules are absent.

- [ ] **Step 3: Implement exact-host request construction and local dispatch**

```python
class ERPExecutor:
    async def preview_write(
        self,
        *,
        repository: RepositoryContext,
        action: CatalogAction,
        environment: str,
        inputs: Mapping[str, Any],
    ) -> PreparedRequest:
        if action.method == "GET":
            raise ExecutionPolicyError("read_action_cannot_be_previewed")
        decision = effective_risk(action)
        base_url = self.drivers.get(action.connector_id).resolve_base_url(environment)
        target = self.network.validate_base_url(
            base_url,
            allow_private_network=self.repository_config.allow_private_network(
                action.connector_id, environment
            ),
        )
        request = build_request(action, target.url, inputs, (repository.root,))
        payload_hash = canonical_payload_hash(request.binding_payload())
        self.request_store.assert_replay_allowed(payload_hash)
        return self.request_store.create_preview(
            PreparedRequest.from_template(
                repository=repository,
                action=action,
                environment=environment,
                request=request,
                risk=decision,
                payload_hash=payload_hash,
            )
        )

    async def execute_write(self, request_id: str) -> ConnectorResult:
        prepared = self.request_store.require_ready(request_id)
        active = self.catalog.require(prepared.action_id)
        if active.version_id != prepared.version_id:
            self.request_store.invalidate(request_id, "preview_invalidated_action_version")
            raise RequestStateError("preview_invalidated_action_version")
        action = self.catalog.require_version(prepared.action_id, prepared.version_id)
        driver = self.drivers.get(prepared.connector_id)
        fields = driver.credential_fields(prepared.environment)
        credentials = self.credentials.load(prepared.connector_id, prepared.environment, fields)
        try:
            async with self.client_factory(follow_redirects=False) as client:
                try:
                    auth = await driver.prepare_auth(
                        environment=prepared.environment,
                        credentials=credentials,
                        client=client,
                    )
                except (ConnectorAuthError, httpx.TransportError) as exc:
                    return self._fail_before_dispatch(prepared, exc)
                executing = self.request_store.start_execution(request_id)
                try:
                    response = await client.send(executing.to_httpx_request(auth))
                except httpx.TransportError:
                    return self._complete_unknown(executing)
        finally:
            credentials.clear()
        result = driver.interpret_response(action=action, response=response, dispatched=True)
        if response.status_code >= 500:
            result = result.model_copy(update={"status": "outcome_unknown"})
        return self._complete(executing, result)
```

`run_read` loads credentials only after catalog, host, path, schema, and network checks pass. It sends once with redirects disabled. `build_request`:
- accepts only schema-declared path/query/header/body/file keys;
- prohibits `Authorization`, `Cookie`, host, and auth-key overrides;
- URL-encodes path values and rejects separators/traversal;
- resolves file inputs inside an active MCP root;
- verifies final scheme and host exactly match the driver base URL.

`RequestTemplate.binding_payload()` must contain repository ID, connector, environment, action ID, immutable version ID, method, normalized path, canonical query, model-supplied non-auth headers, canonical body, and SHA-256 plus relative path for each file. It must not contain credential-derived auth headers or tokens. Any change to one bound field creates a different payload hash and therefore requires a new preview.

Immediately before each API or token request, `NetworkPolicy` resolves every A/AAAA result with `socket.getaddrinfo` and rejects the request if any resolved address is loopback, private, link-local, multicast, reserved, unspecified, or metadata-range unless the connector is explicitly configured as local/gateway. Treat every 3xx response as a non-followed failure; never forward auth headers to a redirected host.

Before a write dispatch, run cataloged preflight actions and duplicate checks. Use documented idempotency headers/fields; otherwise bind business references from `action.idempotency`. A duplicate returns `duplicate_blocked`. A write is never retried. Authentication/token failure before mutation dispatch is definitive `failed`, not `outcome_unknown`. An `outcome_unknown` request remains blocked until `resolve_unknown_with_status` runs the catalog's documented GET/status action and records a conclusive provider result; if no status action exists, it returns `manual_reconciliation_required` and keeps the hash blocked.

- [ ] **Step 4: Run executor, driver, state, and safety tests**

Run: `uv run pytest tests/test_network_policy.py tests/test_erp_executor.py tests/test_request_store.py tests/test_generic_drivers.py -q`

Expected: all tests PASS, including no credential load during preview.

- [ ] **Step 5: Commit the generic local executor**

```bash
git add src/mercury_tools/execution src/mercury_tools/safety/network.py tests/test_network_policy.py tests/test_erp_executor.py
git commit -m "feat: execute cataloged ERP actions locally"
```

### Task 12: Build the FlowAccount and PEAK Global Catalog

**Files:**
- Create: `scripts/build_builtin_catalog.py`
- Create: `catalog/global/flowaccount/source.json`
- Create: `catalog/global/flowaccount/actions.json`
- Create: `catalog/global/peak/source.json`
- Create: `catalog/global/peak/actions.json`
- Modify: `src/mercury_tools/rag/chunking.py`
- Test: `tests/test_builtin_action_catalog.py`
- Modify: `tests/test_chunking.py`
- Modify: `wiki/connectors/flowaccount-endpoint-dictionary.md`
- Modify: `wiki/connectors/peak-endpoint-dictionary.md`

**Interfaces:**
- Consumes:
  - `/Users/natthaphon/Desktop/FlowACC API/data/flowaccount_endpoints.json`
  - `/Users/natthaphon/Desktop/Peak/PEAK_API.postman_collection.json`
  - Task 4 importer and Task 6 risk classifier
- Produces 190 FlowAccount actions and 64 PEAK actions as sanitized, reviewable, immutable JSON.

- [ ] **Step 1: Write failing coverage and catalog-quality tests**

```python
def load_actions(connector: str) -> list[dict]:
    return json.loads(Path(f"catalog/global/{connector}/actions.json").read_text())


def test_flowaccount_catalog_preserves_all_documented_variants() -> None:
    actions = load_actions("flowaccount")
    assert len(actions) == 190
    assert Counter(item["method"] for item in actions) == {
        "GET": 36,
        "POST": 119,
        "PUT": 22,
        "DELETE": 13,
    }
    identities = {(item["action_id"], item["version_id"]) for item in actions}
    assert len(identities) == 190


def test_peak_catalog_preserves_all_documented_actions() -> None:
    actions = load_actions("peak")
    assert len(actions) == 64
    assert Counter(item["method"] for item in actions) == {"GET": 20, "POST": 44}


def test_every_action_has_routing_and_safety_metadata() -> None:
    for connector in ("flowaccount", "peak"):
        for action in load_actions(connector):
            assert action["capability"]
            assert action["aliases_en"] or action["aliases_th"]
            assert action["input_schema"].keys() == {"path", "query", "headers", "body", "files"}
            assert action["risk_tier"] in (0, 1, 2)
            assert action["required_confirmations"] in (0, 1, 2)
            assert action["source_hash"]
            assert action["confidence"] in ("exact", "example_derived", "inferred")


def test_catalog_contains_no_source_credentials_or_personal_examples() -> None:
    serialized = "\n".join(
        Path(path).read_text()
        for path in (
            "catalog/global/flowaccount/source.json",
            "catalog/global/flowaccount/actions.json",
            "catalog/global/peak/source.json",
            "catalog/global/peak/actions.json",
        )
    )
    assert not re.search(
        r'"client_secret"\s*:\s*"(?!\[REDACTED\]|)\S+',
        serialized,
        re.IGNORECASE,
    )
    assert "authorization: bearer" not in serialized.casefold()
    assert not re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", serialized)


def test_endpoint_dictionary_chunk_carries_action_id() -> None:
    document = endpoint_dictionary_document(
        "## Create invoice\n\naction_id: act_1234567890abcdef12345678\nmethod: POST\n"
    )
    chunk = chunk_document(document)[0]
    assert chunk.metadata["action_id"] == "act_1234567890abcdef12345678"
```

- [ ] **Step 2: Run catalog coverage tests**

Run: `uv run pytest tests/test_builtin_action_catalog.py -q`

Expected: FAIL because `catalog/global` outputs do not exist.

- [ ] **Step 3: Implement the deterministic catalog builder**

```python
def build_catalog(connector_id: str, source_path: Path, output_dir: Path) -> None:
    with TemporaryDirectory() as temp:
        context = ensure_repository_state(Path(temp))
        result = import_spec(context, connector_id=connector_id, source_path=source_path)
    actions = [
        action.model_copy(update={
            "risk_tier": effective_risk(action).tier,
            "required_confirmations": effective_risk(action).required_confirmations,
        })
        for action in result.actions
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_json_write(output_dir / "source.json", result.source.model_dump(mode="json"))
    atomic_json_write(
        output_dir / "actions.json",
        [item.model_dump(mode="json") for item in sorted(actions, key=lambda row: row.action_id)],
    )
```

For repeated FlowAccount path/method pairs, derive `variant_id` from documented operation name plus canonical request-schema hash so all 190 rows survive. Map provider terms to stable capabilities and Thai/English aliases. Mark payment, approve, void, email, share, invite, and DELETE actions Tier 2. Update each endpoint dictionary with the generated action ID, method, path, capability, risk tier, confidence, and source citation. Extend chunking to parse an exact `action_id: act_<24 hex>` line into chunk metadata for semantic action routing.

- [ ] **Step 4: Generate catalogs and run tests**

Run:

```bash
uv run python scripts/build_builtin_catalog.py \
  --connector flowaccount \
  --source "/Users/natthaphon/Desktop/FlowACC API/data/flowaccount_endpoints.json" \
  --output catalog/global/flowaccount
uv run python scripts/build_builtin_catalog.py \
  --connector peak \
  --source "/Users/natthaphon/Desktop/Peak/PEAK_API.postman_collection.json" \
  --output catalog/global/peak
uv run pytest tests/test_builtin_action_catalog.py tests/test_chunking.py tests/test_accounting_knowledge_wiki.py tests/test_peak_wiki_content.py -q
```

Expected: catalog tests report exactly 190 FlowAccount and 64 PEAK actions; all tests PASS.

- [ ] **Step 5: Commit the reviewed built-in catalogs**

```bash
git add scripts/build_builtin_catalog.py catalog/global src/mercury_tools/rag/chunking.py wiki/connectors tests/test_builtin_action_catalog.py tests/test_chunking.py
git commit -m "feat: catalog FlowAccount and PEAK endpoints"
```

### Task 13: Read-Only Cloud Brain API and Local Client

**Files:**
- Create: `src/mercury_tools/cloud/__init__.py`
- Create: `src/mercury_tools/cloud/api.py`
- Create: `src/mercury_tools/cloud/client.py`
- Modify: `src/mercury_tools/config.py:11-42,99-156`
- Modify: `src/mercury_tools/mcp/server.py:47-66,2718-2846`
- Test: `tests/test_cloud_api.py`
- Test: `tests/test_cloud_client.py`
- Modify: `tests/test_http_app.py`

**Interfaces:**
- Consumes: `SupabaseCatalogStore`, `SupabaseRagStore`, `SKILL_CATALOG_SEED`, and `skill_markdown`.
- Produces these ordinary-user read endpoints:
  - `GET /api/cloud/v1/catalog/actions`
  - `GET /api/cloud/v1/catalog/actions/{action_id}`
  - `GET /api/cloud/v1/connectors`
  - `GET /api/cloud/v1/skills`
  - `GET /api/cloud/v1/skills/{skill_id}`
  - `POST /api/cloud/v1/knowledge/search`
  - `GET /api/cloud/v1/documents/{document_id}`
  - Async `CloudBrainClient` methods with the same read semantics
  - `CatalogFetchResult(actions: tuple[CatalogAction, ...], source: Literal["cloud", "cache"])`
- Adds `MERCURY_CLOUD_BASE_URL`, default `https://mercury-tools-mcp.onrender.com`.

- [ ] **Step 1: Write failing API read-only and client-cache tests**

```python
async def test_cloud_api_exposes_catalog_and_has_no_write_routes(cloud_app, client) -> None:
    response = await client.get("/api/cloud/v1/catalog/actions?connector=flowaccount&method=GET")
    assert response.status_code == 200
    assert response.json()["actions"][0]["connector_id"] == "flowaccount"

    write_attempt = await client.post("/api/cloud/v1/catalog/actions", json={})
    assert write_attempt.status_code == 405


async def test_cloud_search_returns_citations_without_service_credentials(cloud_app, client) -> None:
    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT input tax", "filters": {"jurisdiction": "TH"}, "top_k": 4},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["results"][0]["citation"]
    assert "SUPABASE_SERVICE_ROLE_KEY" not in json.dumps(payload)


async def test_cloud_search_redacts_personal_data_before_rag(cloud_client, rag_spy) -> None:
    await cloud_client.search_knowledge(
        "VAT for person@example.com tax id 0105559999999",
        filters={"jurisdiction": "TH"},
        top_k=4,
    )
    assert "person@example.com" not in rag_spy.last_query
    assert "0105559999999" not in rag_spy.last_query


async def test_client_uses_cached_catalog_when_cloud_is_unavailable(tmp_path: Path, failing_transport) -> None:
    context = ensure_repository_state(tmp_path)
    seed_catalog_cache(context, flow_get_action())
    client = CloudBrainClient(
        base_url="https://cloud.example.com",
        cache=CatalogCache(context),
        transport=failing_transport,
    )
    result = await client.list_actions(connector="flowaccount")
    assert result.source == "cache"
    assert result.actions[0].method == "GET"
```

- [ ] **Step 2: Run Cloud API/client tests**

Run: `uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q`

Expected: FAIL because the Cloud package and routes do not exist.

- [ ] **Step 3: Implement the read-only Starlette routes and typed client**

```python
def cloud_routes(dependencies: CloudDependencies) -> list[Route]:
    return [
        Route("/api/cloud/v1/catalog/actions", dependencies.list_actions, methods=["GET"]),
        Route("/api/cloud/v1/catalog/actions/{action_id}", dependencies.get_action, methods=["GET"]),
        Route("/api/cloud/v1/connectors", dependencies.list_connectors, methods=["GET"]),
        Route("/api/cloud/v1/skills", dependencies.list_skills, methods=["GET"]),
        Route("/api/cloud/v1/skills/{skill_id}", dependencies.get_skill, methods=["GET"]),
        Route("/api/cloud/v1/knowledge/search", dependencies.search_knowledge, methods=["POST"]),
        Route("/api/cloud/v1/documents/{document_id}", dependencies.get_document, methods=["GET"]),
    ]


class CloudBrainClient:
    async def list_actions(
        self,
        *,
        connector: str | None = None,
        method: str | None = None,
    ) -> CatalogFetchResult:
        try:
            response = await self.client.get(
                "/api/cloud/v1/catalog/actions",
                params=without_none({"connector": connector, "method": method}),
                headers=self.cache.conditional_headers(),
            )
            response.raise_for_status()
            actions = tuple(CatalogAction.model_validate(item) for item in response.json()["actions"])
            self.cache.replace_global(actions, response.headers.get("etag", ""))
            return CatalogFetchResult(actions=actions, source="cloud")
        except httpx.HTTPError:
            return CatalogFetchResult(actions=tuple(self.cache.list_global()), source="cache")
```

Mount `cloud_routes` in `create_http_app` without enabling legacy web APIs. Limit query length to 2,000 characters, `top_k` to 1-20, redact emails, Thai tax IDs, bearer tokens, and API-key patterns before search, and return only sanitized catalog/knowledge/skill fields. Do not persist raw search queries in Cloud audit rows or HTTP logs. The client sends no Authorization header, repository path, ERP payload, or credentials. Cache catalog responses by ETag in `catalog.sqlite`.

- [ ] **Step 4: Run Cloud and hosted regression tests**

Run: `uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py tests/test_http_app.py tests/test_mcp_rag_routing.py -q`

Expected: all tests PASS; the hosted MCP compatibility endpoint remains read-only.

- [ ] **Step 5: Commit the Cloud Brain API**

```bash
git add src/mercury_tools/cloud src/mercury_tools/config.py src/mercury_tools/mcp/server.py tests/test_cloud_api.py tests/test_cloud_client.py tests/test_http_app.py
git commit -m "feat: expose read-only Mercury Cloud Brain"
```

### Task 14: One Local MCP Runtime and Stable Tool Surface

**Files:**
- Create: `src/mercury_tools/mcp/local_runtime.py`
- Create: `src/mercury_tools/mcp/local_server.py`
- Modify: `src/mercury_tools/cli.py:154-170,652-660`
- Modify: `src/mercury_tools/flows/models.py:1-42`
- Modify: `src/mercury_tools/flows/runner.py:12,247-473,665-797`
- Modify: `src/mercury_tools/flows/templates.py`
- Test: `tests/test_local_mcp_contract.py`
- Test: `tests/test_local_mcp_roots.py`
- Modify: `tests/test_mcp_contract.py`

**Interfaces:**
- Consumes: Cloud client, repository roots, local/global catalog, drivers, credentials, executor, flows.
- Produces one `FastMCP("Mercury Finance")` with:
  - Existing knowledge tools: `search_knowledge`, `retrieve_context_pack`, `get_document`
  - Existing product tools: `connector_status`, `run_accounting_skill`
  - Existing flow tools: `run_mercury_flow`, `list_workspace_flows`, `save_workspace_flow`, `run_workspace_flow`
  - ERP tools: `search_erp_actions`, `get_erp_action_schema`, `run_erp_read`, `preview_erp_write`, `confirm_erp_write`, `execute_erp_write`, `get_erp_request_status`, `import_erp_spec`, `list_connector_drivers`, `credential_status`
  - `LocalMercuryRuntime.for_repository(context: RepositoryContext) -> LocalMercuryRuntime`
  - `LocalMercuryRuntime.refresh_catalog() -> None`
  - `serve_local() -> None` using `local_mcp.run(transport="stdio")`

- [ ] **Step 1: Write failing one-server contract and root tests**

```python
EXPECTED_ERP_TOOLS = {
    "search_erp_actions",
    "get_erp_action_schema",
    "run_erp_read",
    "preview_erp_write",
    "confirm_erp_write",
    "execute_erp_write",
    "get_erp_request_status",
    "import_erp_spec",
    "list_connector_drivers",
    "credential_status",
}


async def test_local_mcp_exposes_knowledge_flow_and_erp_tools(local_mcp_session) -> None:
    listed = (await local_mcp_session.list_tools()).tools
    tools = {tool.name for tool in listed}
    assert EXPECTED_ERP_TOOLS <= tools
    assert {
        "search_knowledge",
        "retrieve_context_pack",
        "get_document",
        "connector_status",
        "run_accounting_skill",
        "run_mercury_flow",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
    } <= tools
    assert {
        "preview_flowaccount_journal",
        "create_flowaccount_journal_draft",
        "approve_flowaccount_journal",
    }.isdisjoint(tools)
    by_name = {tool.name: tool for tool in listed}
    assert by_name["run_erp_read"].annotations.readOnlyHint is True
    assert by_name["execute_erp_write"].annotations.destructiveHint is True


async def test_local_tool_uses_context_session_roots(tmp_path: Path, context_factory) -> None:
    context = context_factory(roots=[tmp_path.as_uri()])
    roots = await active_root_paths(context)
    assert roots == (tmp_path.resolve(),)


async def test_multiple_roots_return_selection_error(context_factory, tmp_path: Path) -> None:
    context = context_factory(roots=[(tmp_path / "a").as_uri(), (tmp_path / "b").as_uri()])
    result = await credential_status(ctx=context, repo_root=None)
    assert result["status"] == "multiple_mcp_roots"


def test_flow_write_command_can_preview_but_cannot_self_confirm(local_flow_runner) -> None:
    result = local_flow_runner.run_text("""
name: expense-preview
commands:
  - erpWritePreview:
      actionId: act_create_expense
      environment: production
      inputs:
        body:
          reference: DEMO-EXP-001
          amount: 100
""")
    assert result.status == "confirmation_required"
    assert result.steps[-1].output_summary["request_id"].startswith("req_")
    assert local_flow_runner.executor.execute_calls == 0


async def test_workspace_flow_path_cannot_escape_root(context_factory, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside.yaml"
    root.mkdir()
    context = context_factory(roots=[root.as_uri()])
    result = await save_workspace_flow(
        repo_root=str(root),
        path=str(outside),
        content="name: blocked\ncommands: []\n",
        ctx=context,
    )
    assert result["status"] == "path_outside_repository_root"
```

- [ ] **Step 2: Run local MCP tests**

Run: `uv run pytest tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py -q`

Expected: FAIL because `local_server` does not exist.

- [ ] **Step 3: Implement the local runtime factory and MCP tools**

```python
local_mcp = FastMCP("Mercury Finance")


async def active_root_paths(ctx: Context) -> tuple[Path, ...]:
    result = await ctx.session.list_roots()
    return root_paths(tuple(str(root.uri) for root in result.roots))


@local_mcp.tool()
async def run_erp_read(
    repo_root: str,
    action_id: str,
    inputs: dict[str, Any],
    ctx: Context,
    environment: str = "production",
) -> dict[str, Any]:
    roots = await active_root_paths(ctx)
    repository = ensure_repository_state(resolve_repository_root(repo_root, roots))
    runtime = LocalMercuryRuntime.for_repository(repository)
    await runtime.refresh_catalog()
    result = await runtime.executor.run_read(
        repository=repository,
        action=runtime.catalog.require(action_id),
        environment=environment,
        inputs=inputs,
    )
    return redact_json(result.model_dump(mode="json"))


@local_mcp.tool()
async def confirm_erp_write(
    repo_root: str,
    request_id: str,
    payload_hash: str,
    ctx: Context,
) -> dict[str, Any]:
    repository = await repository_from_context(ctx, repo_root)
    request = LocalMercuryRuntime.for_repository(repository).executor.confirm_write(
        request_id, payload_hash
    )
    return request.public_dict()
```

Implement all listed tools as thin adapters. FastMCP injects `Context`; it is not part of the model-visible input schema. `connector_status` combines credential field presence with the non-secret validation metadata in `config.json`, so the model knows the selected ERP, environment, company display name, and whether a new safe probe is required. `search_erp_actions` and `get_erp_action_schema` await `refresh_catalog`, then merge Cloud cache with the selected root's overlay. Search also asks Cloud RAG for endpoint-dictionary chunks, maps returned `action_id` metadata to semantic scores, and passes those scores to `search_actions`; cached deterministic ranking remains available offline. If candidates are ambiguous, return candidate summaries and `status="ambiguous"` without an action choice. Knowledge and Skill tools call Cloud Brain REST. `run_accounting_skill` returns the canonical Skill, cited context pack, and ordered generic tool plan; it does not call an LLM.

Replace the flow runner's `public_capability_gate` with injected local runtime commands `erpRead` and `erpWritePreview`. `erpRead` may call only Tier 0 actions. `erpWritePreview` creates a bound request and returns `confirmation_required`; a flow cannot call `confirm_erp_write` itself. Reject mutation commands inside `retry` so no flow can automatically replay a dispatched write. Existing knowledge, report, loop, assertion, and nested-flow commands keep their current behavior.

Apply `resolve_repository_root` and symlink-resolved containment checks to every `save_workspace_flow`, `list_workspace_flows`, and `run_workspace_flow` path so flow files cannot escape active MCP roots.

Expose resources `mercury://wiki/index`, `mercury://wiki/doc/{document_id}`, `mercury://skills/{skill_id}`, `mercury://connectors`, and `mercury://audit/{event_id}`. Expose the five existing accounting prompts. Never return raw local audit files through a broad path; `mercury://audit/{event_id}` returns one sanitized event.

Set MCP tool annotations explicitly: knowledge/search/schema/status/read tools use `readOnlyHint=True`; import, preview, and confirm use `readOnlyHint=False` and `destructiveHint=False`; `execute_erp_write` uses `readOnlyHint=False` and `destructiveHint=True`.

- [ ] **Step 4: Run local MCP and flow/RAG regressions**

Run: `uv run pytest tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py tests/test_mcp_contract.py tests/test_flows.py tests/test_mcp_rag_routing.py -q`

Expected: all tests PASS. Run `uv run mercury mcp serve-local` in a PTY, send MCP initialization, and expect server name `Mercury Finance` with one tool list.

- [ ] **Step 5: Commit the unified local MCP**

```bash
git add src/mercury_tools/mcp/local_runtime.py src/mercury_tools/mcp/local_server.py src/mercury_tools/cli.py src/mercury_tools/flows/models.py src/mercury_tools/flows/runner.py src/mercury_tools/flows/templates.py tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py tests/test_mcp_contract.py tests/test_flows.py
git commit -m "feat: add unified local Mercury Finance MCP"
```


### Task 15: Migrate Skills to the Generic Local Gateway

**Files:**
- Create: `plugins/mercury-finance/skills/flowaccount-journal-posting-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/connector-setup-guide-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/flowaccount-connector-setup-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/peak-connector-setup-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/company-health-check-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/vat-summary-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/invoice-review-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/management-report-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/mercury-flow-runner/SKILL.md`
- Modify: `src/mercury_tools/db/product.py:50-134`
- Delete: `plugins/mercury-finance-private/`
- Modify: `tests/test_plugin_package.py`
- Delete: `tests/test_private_mcp.py`

**Interfaces:**
- Consumes: generic local MCP tools from Task 14.
- Produces gated Skills that never ask the model to transmit credentials and never call the three private journal tools.

- [ ] **Step 1: Replace plugin tests with the one-plugin, local-credential contract**

```python
PLUGIN_ROOT = ROOT / "plugins/mercury-finance"


def test_marketplace_contains_exactly_one_mercury_plugin() -> None:
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    assert [item["name"] for item in marketplace["plugins"]] == ["mercury-finance"]
    assert not (ROOT / "plugins/mercury-finance-private").exists()


def test_setup_skills_stop_for_local_cli_when_credentials_are_missing() -> None:
    for skill_name in (
        "connector-credential-setup-th",
        "flowaccount-connector-setup-th",
        "peak-connector-setup-th",
    ):
        text = (PLUGIN_ROOT / f"skills/{skill_name}/SKILL.md").read_text()
        assert "credential_status" in text
        assert "mercury credentials setup" in text
        assert "Do not proceed" in text
        assert "submit_connector_credentials" not in text
        assert "client_secret" not in text


def test_journal_skill_uses_generic_preview_confirm_execute_sequence() -> None:
    text = (PLUGIN_ROOT / "skills/flowaccount-journal-posting-th/SKILL.md").read_text()
    ordered = [
        "search_erp_actions",
        "get_erp_action_schema",
        "preview_erp_write",
        "wait for explicit confirmation",
        "confirm_erp_write",
        "execute_erp_write",
    ]
    positions = [text.index(item) for item in ordered]
    assert positions == sorted(positions)
    assert "outcome_unknown" in text
    assert "create_flowaccount_journal_draft" not in text
```

- [ ] **Step 2: Run plugin Skill tests**

Run: `uv run pytest tests/test_plugin_package.py -q`

Expected: FAIL because the current Skills reference public workspaces/server credentials and the private plugin still exists.

- [ ] **Step 3: Rewrite setup and accounting Skills**

The setup Skills must use this exact gate:

```markdown
1. Call `credential_status` for the active repository, connector, and environment.
2. If any required field is missing, stop. Tell the user to run:
   `mercury credentials setup <connector> --env <environment> --repo-root "<repo>"`
   Do not ask for or accept the values in chat.
3. After the user confirms setup is complete, call `credential_status` again.
4. If configured, ask the user to run `mercury credentials test <connector> --env <environment> --repo-root "<repo>"`.
5. Continue only after the test reports `connected`.
```

The journal Skill validates debit equals credit with `journals/models.py`, searches the catalog, requests the exact action schema, previews, stops for each required confirmation, confirms by request ID and payload hash, and executes once. Approval is a separate Tier 2 action with a new preview and two confirmations. If the result is `outcome_unknown`, stop and use `get_erp_request_status`; never replay.

Company health, VAT, invoice review, management reporting, and flow Skills retrieve cited Cloud context and run read actions without verbose evidence unless the user requests audit detail.

- [ ] **Step 4: Remove the private plugin and run Skill/package tests**

Delete `plugins/mercury-finance-private` and remove its marketplace entry. Update `SKILL_CATALOG_SEED` to retain `flowaccount-journal-posting-th` with tags `["flowaccount","journal","write","thai"]` and no `private` tag.

Run: `uv run pytest tests/test_plugin_package.py tests/test_runtime_skills.py tests/test_journal_models.py -q`

Expected: all tests PASS; the repository has one plugin directory and the journal balance model remains reusable.

- [ ] **Step 5: Commit the Skill migration**

```bash
git add plugins .agents/plugins/marketplace.json src/mercury_tools/db/product.py tests/test_plugin_package.py tests/test_runtime_skills.py
git rm tests/test_private_mcp.py
git commit -m "feat: route Mercury Skills through local ERP tools"
```

### Task 16: Package One Pinned Local stdio Plugin

**Files:**
- Modify: `plugins/mercury-finance/.mcp.json`
- Modify: `plugins/mercury-finance/.codex-plugin/plugin.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/mercury_tools/rag/embeddings.py`
- Create: `scripts/validate_release_plugin.py`
- Test: `tests/test_plugin_clean_install.py`
- Modify: `tests/test_plugin_package.py`
- Modify: `docs/JUDGE_QUICKSTART.md`

**Interfaces:**
- Consumes: `mercury` CLI and `mcp serve-local`.
- Produces plugin version `0.2.0+codex.20260711` and a runtime pinned to Git tag `v0.2.0`.

- [ ] **Step 1: Write failing package and immutable-launcher tests**

```python
def test_plugin_registers_one_pinned_local_stdio_server() -> None:
    data = json.loads(Path("plugins/mercury-finance/.mcp.json").read_text())
    assert list(data["mcpServers"]) == ["mercury-finance"]
    server = data["mcpServers"]["mercury-finance"]
    assert server["command"] == "uvx"
    assert server["args"] == [
        "--from",
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.0",
        "mercury",
        "mcp",
        "serve-local",
    ]
    assert "url" not in server
    assert "bearer_token_env_var" not in server


def test_plugin_declares_read_and_write_without_embedded_secrets() -> None:
    manifest = json.loads(Path("plugins/mercury-finance/.codex-plugin/plugin.json").read_text())
    serialized = json.dumps(manifest)
    assert manifest["version"] == "0.2.0+codex.20260711"
    assert manifest["interface"]["capabilities"] == ["Interactive", "Read", "Write"]
    assert "MERCURY_PRIVATE_MCP_TOKEN" not in serialized
    assert "client_secret" not in serialized


def test_release_runtime_dependencies_are_exactly_pinned() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text())
    for dependency in data["project"]["dependencies"]:
        assert "==" in dependency
    assert data["project"]["optional-dependencies"]["openai"] == ["openai==2.44.0"]
```

- [ ] **Step 2: Run package tests**

Run: `uv run pytest tests/test_plugin_package.py tests/test_plugin_clean_install.py -q`

Expected: FAIL because the plugin still registers the hosted HTTP MCP.

- [ ] **Step 3: Pin the local launcher and update product metadata**

```json
{
  "mcpServers": {
    "mercury-finance": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.0",
        "mercury",
        "mcp",
        "serve-local"
      ],
      "cwd": ".",
      "tool_timeout_sec": 900
    }
  }
}
```

Set the plugin capabilities to `Interactive`, `Read`, and `Write`. Rewrite the long description and default prompts around repository-local FlowAccount/PEAK setup, endpoint search, read actions, and approval-gated writes. Keep marketplace policy `AVAILABLE` and `ON_INSTALL`. `validate_release_plugin.py` rejects HTTP MCP URLs, moving refs (`main`, `master`, branch names), multiple Mercury servers, private token names, and any credential values.

Pin the v0.2.0 core dependencies exactly: `cryptography==49.0.0` (removed in Task 17), `httpx==0.28.1`, `mcp==1.26.0`, `pydantic==2.13.4`, `python-dotenv==1.2.2`, `pyyaml==6.0.3`, `starlette==1.3.1`, and `uvicorn==0.50.0`. Move `openai==2.44.0` to optional dependency group `openai`, move the import inside `OpenAIEmbeddingProvider.__init__`, and raise `Install mercury-tools[openai] to use OpenAI embeddings.` when absent. Regenerate and commit `uv.lock`; the default Cloud and local provider remains deterministic `hash` and requires no OpenAI API key.

- [ ] **Step 4: Validate the package and local executable**

Run:

```bash
uv build
uvx --from . mercury --help
uv run python /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance
uv run python scripts/validate_release_plugin.py
uv run pytest tests/test_plugin_package.py tests/test_plugin_clean_install.py -q
```

Expected:
- wheel and source distribution build successfully;
- `mercury --help` exits 0;
- both plugin validators exit 0;
- package tests PASS.

- [ ] **Step 5: Commit the pinned plugin package**

```bash
git add plugins/mercury-finance .agents/plugins/marketplace.json pyproject.toml uv.lock src/mercury_tools/rag/embeddings.py scripts/validate_release_plugin.py tests/test_plugin_clean_install.py tests/test_plugin_package.py docs/JUDGE_QUICKSTART.md
git commit -m "feat: package one pinned Mercury Finance MCP"
```

### Task 17: Remove Cloud ERP Secrets and the Private Write Runtime

**Files:**
- Create: `supabase/migrations/20260711130000_remove_cloud_erp_secrets.sql`
- Create: `scripts/purge_cloud_erp_secrets.py`
- Modify: `src/mercury_tools/config.py:18,35-42,59-64,117-155`
- Modify: `src/mercury_tools/mcp/server.py:96-124,2718-2846`
- Modify: `src/mercury_tools/db/product.py`
- Delete: `src/mercury_tools/mcp/private_server.py`
- Delete: `src/mercury_tools/db/journal_writes.py`
- Delete: `src/mercury_tools/journals/service.py`
- Delete: `src/mercury_tools/connectors/flowaccount_journal.py`
- Delete: `docs/PRIVATE_JOURNAL_MCP.md`
- Delete: `tests/test_journal_write_store.py`
- Delete: `tests/test_journal_service.py`
- Delete: `tests/test_flowaccount_journal_client.py`
- Modify: `render.yaml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_cloud_secret_removal.py`
- Modify: `tests/test_http_app.py`
- Modify: `tests/test_remote_config.py`

**Interfaces:**
- Consumes: validated local runtime from Tasks 14-16.
- Produces a Cloud service that stores no connector credentials or encrypted ERP request payloads and has no `/private-mcp` route.

- [ ] **Step 1: Write failing server-vault and route-removal tests**

```python
def test_cleanup_migration_removes_vault_data_and_write_table() -> None:
    sql = Path("supabase/migrations/20260711130000_remove_cloud_erp_secrets.sql").read_text()
    assert "drop table if exists public.connector_write_requests" in sql
    assert "metadata - 'server_vault'" in sql
    assert "metadata - 'credential_fingerprints'" in sql
    assert "metadata - 'credential_fields'" in sql
    assert "tags = tags - 'private'" in sql


def test_render_blueprint_has_no_private_or_vault_env() -> None:
    text = Path("render.yaml").read_text()
    assert "MERCURY_PRIVATE_MCP" not in text
    assert "MERCURY_CREDENTIAL_VAULT_SECRET" not in text


async def test_hosted_app_has_no_private_mcp_route(client) -> None:
    response = await client.post("/private-mcp")
    assert response.status_code == 404


def test_server_code_has_no_credential_ingestion_surface() -> None:
    text = Path("src/mercury_tools/mcp/server.py").read_text()
    assert "submit_connector_credentials" not in text
    assert "PrivateBearerAuthMiddleware" not in text
    assert "private_mcp" not in text
```

- [ ] **Step 2: Run cleanup tests**

Run: `uv run pytest tests/test_cloud_secret_removal.py tests/test_http_app.py tests/test_remote_config.py -q`

Expected: FAIL because private routes, vault settings, and cloud secret storage remain.

- [ ] **Step 3: Add a dry-run-first purge and destructive migration**

```sql
update public.mercury_connector_profiles
set
  status = 'requires_credentials',
  metadata = metadata
    - 'server_vault'
    - 'credential_fingerprints'
    - 'credential_fields'
    - 'credentials_configured'
    - 'credentials_configured_at'
    - 'credential_storage',
  updated_at = now()
where metadata ?| array[
  'server_vault',
  'credential_fingerprints',
  'credential_fields',
  'credentials_configured',
  'credentials_configured_at',
  'credential_storage'
];

drop table if exists public.connector_write_requests;

update public.mercury_skill_catalog
set
  tags = tags - 'private',
  summary = 'FlowAccount journal workflow through local catalog actions and confirmation gates',
  updated_at = now()
where skill_id = 'flowaccount-journal-posting-th';
```

`purge_cloud_erp_secrets.py` defaults to `--dry-run` and prints counts only. Applying requires both `--apply` and `--confirm DELETE_SERVER_ERP_SECRETS`. It removes the same JSON keys and write rows through service-role requests, emits no metadata bodies, and is idempotent. It also performs a value-free high-confidence secret scan over `mercury_skill_uploads.markdown`, `knowledge_documents.body`, `knowledge_chunks.chunk_text`, `mercury_product_events.summary/metadata`, and `mcp_audit_events.output_summary/metadata`. Apply mode replaces only matched bearer/API-key/secret values with `[REDACTED]`; field names and documentation placeholders remain.

Delete remote credential set/get/encrypt/decrypt methods and the public MCP credential submission tool. Keep connector names, environments, company display names, and non-secret capability metadata. Remove `cryptography` after `rg -n "cryptography|Fernet" src` returns no matches.

- [ ] **Step 4: Run cleanup regressions and commit the ingress shutdown**

Run:

```bash
rg -n "cryptography|Fernet|MERCURY_PRIVATE_MCP|server_vault" src render.yaml pyproject.toml
uv lock
uv run pytest tests/test_cloud_secret_removal.py tests/test_http_app.py tests/test_remote_config.py -q
uv run ruff check .
```

Expected: `rg` returns no matches and exits 1; all tests and Ruff PASS. Commit:

```bash
git add supabase/migrations/20260711130000_remove_cloud_erp_secrets.sql scripts/purge_cloud_erp_secrets.py src/mercury_tools/config.py src/mercury_tools/mcp/server.py src/mercury_tools/db/product.py render.yaml pyproject.toml uv.lock tests/test_cloud_secret_removal.py tests/test_http_app.py tests/test_remote_config.py
git rm src/mercury_tools/mcp/private_server.py src/mercury_tools/db/journal_writes.py src/mercury_tools/journals/service.py src/mercury_tools/connectors/flowaccount_journal.py docs/PRIVATE_JOURNAL_MCP.md tests/test_journal_write_store.py tests/test_journal_service.py tests/test_flowaccount_journal_client.py
git commit -m "refactor: remove cloud ERP credential runtime"
git push origin mercury-public-mcp-contest
```

- [ ] **Step 5: Deploy the ingress shutdown before deleting stored secrets**

Deploy the Task 17 commit to the existing Render service through the Render service API/tool. Verify the new code is live before touching Supabase data:

```bash
curl -fsS https://mercury-tools-mcp.onrender.com/healthz
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST https://mercury-tools-mcp.onrender.com/private-mcp
```

Expected: health is `ok` with no private-MCP fields; the second command prints `404`. If it does not print `404`, stop and do not purge.

- [ ] **Step 6: Validate local credentials, purge Cloud secrets, and remove Render secrets**

Create a disposable repository and run interactive local setup. The operator enters the production FlowAccount values directly into the hidden terminal prompts:

```bash
mkdir -p /tmp/mercury-v020-smoke
uv run mercury credentials setup flowaccount \
  --env production \
  --repo-root /tmp/mercury-v020-smoke
uv run mercury credentials status --repo-root /tmp/mercury-v020-smoke
uv run mercury credentials test flowaccount \
  --env production \
  --repo-root /tmp/mercury-v020-smoke
uv run python scripts/purge_cloud_erp_secrets.py --dry-run
```

Expected: the connector probe reports `connected` and dry-run prints counts without values. Then run:

```bash
uv run python scripts/purge_cloud_erp_secrets.py \
  --apply \
  --confirm DELETE_SERVER_ERP_SECRETS
uv run python scripts/purge_cloud_erp_secrets.py --dry-run
uv run mercury credentials clear --all --repo-root /tmp/mercury-v020-smoke
```

Apply `supabase/migrations/20260711130000_remove_cloud_erp_secrets.sql` with the connected Supabase MCP tool `supabase_apply_migration`, using `project_id="vbnlkqvauqwnjbxngkas"` and `name="remove_cloud_erp_secrets_v020"`. Expected: the final dry-run reports zero vault records, zero encrypted write requests, and zero high-confidence secret-value matches. Remove `MERCURY_PRIVATE_MCP_TOKEN` and `MERCURY_CREDENTIAL_VAULT_SECRET` from the Render service environment through the Render service API/tool, redeploy once, and verify `/healthz` remains `ok`.

### Task 18: End-to-End Acceptance, Release Tag, and Judge Handoff

**Files:**
- Create: `tests/integration/test_local_erp_mcp.py`
- Create: `scripts/smoke_local_plugin.py`
- Modify: `README.md`
- Modify: `docs/JUDGE_QUICKSTART.md`
- Create: `docs/LOCAL_CREDENTIALS.md`
- Create: `docs/ACTION_CATALOG.md`
- Create: `docs/RELEASE_V0.2.0.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: all prior tasks.
- Produces a reproducible `v0.2.0` release with one installed MCP, safe live GET probes, approval-gated mocked writes, and current operating documentation.

- [ ] **Step 1: Add the acceptance test that exercises the complete local path**

```python
@pytest.mark.integration
async def test_local_mcp_acceptance(tmp_path: Path, fake_cloud, fake_erp, local_mcp_client) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    setup_fake_credentials(repo)

    tools = {tool.name for tool in (await local_mcp_client.list_tools()).tools}
    assert "run_erp_read" in tools
    assert "preview_erp_write" in tools
    assert "execute_erp_write" in tools

    imported = await call_tool(
        local_mcp_client,
        "import_erp_spec",
        {"repo_root": str(repo), "source_path": str(repo / "erp-openapi.json")},
    )
    assert imported["status"] == "imported"

    read = await call_tool(
        local_mcp_client,
        "run_erp_read",
        {"repo_root": str(repo), "action_id": "act_company_info", "inputs": {}},
    )
    assert read["status"] == "succeeded"

    preview = await call_tool(
        local_mcp_client,
        "preview_erp_write",
        {
            "repo_root": str(repo),
            "action_id": "act_create_invoice",
            "inputs": {"body": {"reference": "DEMO-001", "amount": 100}},
        },
    )
    assert preview["state"] == "awaiting_confirmation"

    confirmed = await call_tool(
        local_mcp_client,
        "confirm_erp_write",
        {
            "repo_root": str(repo),
            "request_id": preview["request_id"],
            "payload_hash": preview["payload_hash"],
        },
    )
    assert confirmed["state"] == "ready_to_execute"

    executed = await call_tool(
        local_mcp_client,
        "execute_erp_write",
        {"repo_root": str(repo), "request_id": preview["request_id"]},
    )
    assert executed["status"] == "succeeded"
    assert "secret" not in (repo / ".mercury/audit/audit_ledger.jsonl").read_text()
```

The integration uses only fake Cloud and ERP transports. Add optional live tests gated by `MERCURY_LIVE_FLOWACCOUNT=1` and `MERCURY_LIVE_PEAK=1`; live tests run credential validation and safe GET probes only.

- [ ] **Step 2: Run the complete local and Cloud quality gate**

Run:

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -m "not integration" -q
uv run pytest tests/integration/test_local_erp_mcp.py -q
uv run mercury doctor --repo-root .
uv run python scripts/validate_release_plugin.py
uv run python scripts/smoke_local_plugin.py
```

Expected:
- Ruff exits 0;
- all non-integration tests PASS;
- the fake end-to-end integration PASS;
- doctor identifies one repository and no permission leak;
- plugin validation and stdio initialization PASS.

- [ ] **Step 3: Publish global catalog and verify the Cloud Brain**

Use the Supabase MCP `supabase_list_migrations` tool for project `vbnlkqvauqwnjbxngkas` and verify both `erp_action_catalog_v020` and `remove_cloud_erp_secrets_v020` are present.

Run:

```bash
uv run python scripts/publish_catalog.py --path catalog/global
curl -fsS "https://mercury-tools-mcp.onrender.com/api/cloud/v1/catalog/actions?connector=flowaccount&method=GET"
curl -fsS -X POST \
  -H "Content-Type: application/json" \
  -d '{"query":"ภาษีซื้อ VAT","filters":{"jurisdiction":"TH"},"top_k":4}' \
  https://mercury-tools-mcp.onrender.com/api/cloud/v1/knowledge/search
```

Expected: the catalog response contains FlowAccount GET actions; knowledge search returns cited Thai accounting/tax chunks; neither response contains credentials or repository paths.

- [ ] **Step 4: Commit documentation and release readiness**

Document:
- plugin install from GitHub marketplace;
- first-run `uvx` prerequisite;
- repository-local credential setup/status/test/clear;
- FlowAccount and PEAK safe probes;
- catalog import and trusted-host confirmation;
- risk tiers and confirmation sequence;
- `outcome_unknown` recovery;
- audit location and dotenv trust boundary;
- no web UI, no local LLM, and no Cloud credential storage;
- removal semantics: `credentials clear --all` removes the file but does not claim forensic secure erase.

Run:

```bash
git add tests/integration/test_local_erp_mcp.py scripts/smoke_local_plugin.py README.md docs .github/workflows/ci.yml
git commit -m "docs: complete Mercury v0.2.0 release"
git push origin mercury-public-mcp-contest
```

Expected: push succeeds and PR CI is green.

- [ ] **Step 5: Merge, tag the immutable runtime, and clean-install the plugin**

After PR review is green:

```bash
gh pr merge 2 --squash --delete-branch
git checkout main
git pull --ff-only origin main
git tag -a v0.2.0 -m "Mercury Finance unified local ERP MCP v0.2.0"
git push origin v0.2.0
```

Create an isolated Codex home and install from the immutable tag:

```bash
export MERCURY_TEST_CODEX_HOME="$(mktemp -d)"
CODEX_HOME="$MERCURY_TEST_CODEX_HOME" codex plugin marketplace add \
  natthaphonchop2-creator/mercury-tools \
  --ref v0.2.0 \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
CODEX_HOME="$MERCURY_TEST_CODEX_HOME" codex plugin add mercury-finance@mercury-tools
CODEX_HOME="$MERCURY_TEST_CODEX_HOME" codex mcp list
```

Expected:
- install succeeds without a Mercury Owner Token;
- `codex mcp list` contains exactly one Mercury entry named `mercury-finance`;
- `mercury-finance-private` is absent;
- starting a task exposes knowledge, flow, GET, POST, PUT, PATCH, and DELETE catalog tools through one local stdio server.


- [ ] **Step 6: Replace the existing developer-machine installation**

After the isolated install passes, migrate the real Codex home:

```bash
codex plugin remove mercury-finance-private@mercury-tools
codex plugin remove mercury-finance@mercury-tools
codex plugin marketplace remove mercury-tools
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref v0.2.0 \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
codex plugin add mercury-finance@mercury-tools
codex mcp list
```

Expected: one `mercury-finance` MCP and no private plugin. Remove the retired local token without reading it:

```bash
security delete-generic-password -s mercury-finance-private 2>/dev/null || true
launchctl unsetenv MERCURY_PRIVATE_MCP_TOKEN
unset MERCURY_PRIVATE_MCP_TOKEN
```

Search shell startup files for the variable name only, delete any export line with `apply_patch`, restart Codex, and confirm `codex mcp list` still shows one Mercury server.


## Final Acceptance Matrix

| Approved requirement | Verification |
| --- | --- |
| One plugin and one MCP | `test_marketplace_contains_exactly_one_mercury_plugin` and isolated `codex mcp list` |
| Local ERP credentials | credential store/CLI tests and no-value output assertions |
| Local ERP network origin | fake transport bound to `ERPExecutor` in local process; Render has no executor route |
| GET/POST/PUT/PATCH/DELETE | catalog model constraint plus built-in catalog and executor parameterized tests |
| Dynamic local imports | end-to-end `import_erp_spec` followed by immediate action execution |
| GitHub global imports | publisher workflow and Cloud catalog query |
| FlowAccount and PEAK | 190/64 catalog counts plus mocked auth and optional safe live probes |
| Tiered confirmation | policy and request-state parameterized tests |
| Preview binding | payload-hash mismatch and action-version binding tests |
| Duplicate/idempotency | executor preflight and SQLite replay-block tests |
| `outcome_unknown` | timeout/5xx tests and replay block |
| SSRF/file/redirect controls | network policy and request builder tests |
| Local audit/privacy | JSONL redaction tests and Cloud payload-free assertions |
| Cloud Skills/RAG/catalog | Cloud API/client and citation tests |
| No private MCP/server vault | cleanup migration, route 404, Render env, and source scan |
| No web UI/local LLM | plugin manifest, README, and hosted route contract |
| Pinned reproducible runtime | `v0.2.0` launcher assertion and clean install |
