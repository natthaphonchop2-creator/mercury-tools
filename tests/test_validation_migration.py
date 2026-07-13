import json
import re
from pathlib import Path

from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

MIGRATION = Path("supabase/migrations/20260713100000_erp_action_validation_knowledge.sql")
SEMANTIC_CONTRACTS = (
    Path("catalog/global/flowaccount/semantic-contracts.json"),
    Path("catalog/global/peak/semantic-contracts.json"),
)

FORBIDDEN_VALUE_TOKEN_LABELS = (
    "auth",
    "authentication",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "file",
    "header",
    "headers",
    "local",
    "oauth",
    "password",
    "passwords",
    "path",
    "payload",
    "raw",
    "request",
    "response",
    "secret",
    "secrets",
    "source",
    "token",
    "tokens",
)
FORBIDDEN_VALUE_GROUP_LABELS = (
    "access key",
    "access keys",
    "access token",
    "access tokens",
    "api key",
    "api keys",
    "api secret",
    "api secrets",
    "auth header",
    "auth headers",
    "auth token",
    "auth tokens",
    "authentication header",
    "authentication token",
    "authorization header",
    "authorization token",
    "client id",
    "client secret",
    "client secrets",
    "cookie header",
    "cookie token",
    "file name",
    "file path",
    "id token",
    "local file",
    "local path",
    "oauth token",
    "provider response",
    "raw payload",
    "raw response",
    "refresh token",
    "request body",
    "request payload",
    "response body",
    "response payload",
    "session cookie",
    "session token",
    "source file",
    "source path",
)
FORBIDDEN_VALUE_LABELS = (
    *FORBIDDEN_VALUE_TOKEN_LABELS,
    *FORBIDDEN_VALUE_GROUP_LABELS,
)
PROVIDER_ID_PREFIXES = (
    "provider",
    "source",
    "customer",
    "contact",
    "document",
    "record",
    "invoice",
    "payment",
)
PROVIDER_REFERENCE_OWNERS = ("provider", "source")
PROVIDER_REFERENCE_OBJECTS = ("record", "document")
PROVIDER_ID_KEY_PATTERN = re.compile(
    rf"^({'|'.join(PROVIDER_ID_PREFIXES)})(_[a-z]+)*_id$"
)

SAFE_VALUE_STATE_PATTERN = (
    "absent|available|configured|disabled|included|known|missing|needed|omitted|"
    "present|provided|redacted|required|stored|supported|unavailable|unknown"
)
SAFE_REFERENCE_TYPE_PATTERN = (
    "array|boolean|field|id|identifier|integer|key|null|number|object|schema|string|"
    "truncated|unknown"
)
SAFE_REFERENCE_DESCRIPTOR_PATTERN = (
    rf"([a-z]+[[:space:]]+){{0,3}}({SAFE_REFERENCE_TYPE_PATTERN})"
)
SAFE_LIVE_VALIDATION_EXPLANATION_PATTERN = (
    r"are[[:space:]]+not[[:space:]]+available[[:space:]]+"
    r"for[[:space:]]+live[[:space:]]+validation"
)
SAFE_STATE_EXPLANATION_PATTERN = (
    rf"(({SAFE_VALUE_STATE_PATTERN})|"
    rf"(are|is|remain|remains|was|were)[[:space:]]+"
    rf"(not[[:space:]]+)?({SAFE_VALUE_STATE_PATTERN})|"
    rf"(cannot|must|should)[[:space:]]+be[[:space:]]+"
    rf"({SAFE_VALUE_STATE_PATTERN}))"
)
SAFE_LABEL_CONTINUATION_PATTERN = (
    "body|credential|credentials|document|file|header|headers|id|key|keys|name|"
    "path|payload|record|response|secret|secrets|token|tokens|value"
)
SAFE_CONTROLLED_REQUEST_TAIL_PATTERN = (
    r"completed[[:space:]]+with[[:space:]]+"
    r"(the[[:space:]]+reviewed[[:space:]]+expected[[:space:]]+outcome|"
    r"a[[:space:]]+classified[[:space:]]+failure)|"
    r"outcome[[:space:]]+could[[:space:]]+not[[:space:]]+be[[:space:]]+proven"
    r"[[:space:]]+and[[:space:]]+was[[:space:]]+not[[:space:]]+retried"
)
SAFE_SENSITIVE_EXPLANATION_PATTERN = (
    rf"^(({SAFE_STATE_EXPLANATION_PATTERN})|"
    rf"({SAFE_LIVE_VALIDATION_EXPLANATION_PATTERN})|"
    rf"(({SAFE_LABEL_CONTINUATION_PATTERN})[ _-]+"
    rf"({SAFE_STATE_EXPLANATION_PATTERN}))|"
    rf"((record|document)[ _-]+({SAFE_REFERENCE_DESCRIPTOR_PATTERN}))|"
    rf"({SAFE_CONTROLLED_REQUEST_TAIL_PATTERN}))[.]?$"
)
SAFE_REFERENCE_EXPLANATION_PATTERN = (
    rf"^(({SAFE_VALUE_STATE_PATTERN})|({SAFE_REFERENCE_DESCRIPTOR_PATTERN})|"
    rf"(are|is|remain|remains|was|were)[[:space:]]+"
    rf"(not[[:space:]]+)?"
    rf"({SAFE_VALUE_STATE_PATTERN}|{SAFE_REFERENCE_DESCRIPTOR_PATTERN})|"
    rf"(cannot|must|should)[[:space:]]+be[[:space:]]+"
    rf"({SAFE_VALUE_STATE_PATTERN}|{SAFE_REFERENCE_DESCRIPTOR_PATTERN}))[.]?$"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def _compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _function_body(sql: str, name: str) -> str:
    return sql.split(f"create or replace function public.{name}", 1)[1].split(
        "$function$;", 1
    )[0]


def _python_regex(pattern: str) -> str:
    return pattern.replace("[^[:space:]]", r"\S").replace("[[:space:]]", r"\s")


def _label_pattern(label: str) -> str:
    return re.escape(label).replace(r"\ ", r"[ _-]+")


def _labelled_forbidden_values() -> tuple[str, ...]:
    values: list[str] = []
    for label in FORBIDDEN_VALUE_LABELS:
        values.extend(
            (
                f"{label}: !value",
                f"{label.replace(' ', '_')} = '#1234'",
                f"{label} synthetic_value",
            )
        )
    for prefix in PROVIDER_ID_PREFIXES:
        values.extend(
            (
                f"{prefix}_id: #1234",
                f"{prefix} id = '!value'",
                f"{prefix} id #1234",
                f"{prefix}_external_record_id: !value",
                f"{prefix} external record id = '#1234'",
            )
        )
    for owner in PROVIDER_REFERENCE_OWNERS:
        for reference in PROVIDER_REFERENCE_OBJECTS:
            values.extend(
                (
                    f"{owner} {reference}: #1234",
                    f"{owner}_{reference}_id = '#1234'",
                    f"{owner} {reference} #1234",
                )
            )
    return tuple(dict.fromkeys(values))


def _has_labelled_actual_value(value: str) -> bool:
    lowered = value.lower()
    for label in FORBIDDEN_VALUE_LABELS:
        pattern = (
            rf"(^|[^a-z0-9])(?:{_label_pattern(label)})"
            rf"(?:\s*[:=]\s*|\s+)(\S.*)"
        )
        for match in re.finditer(pattern, lowered):
            if not re.fullmatch(
                _python_regex(SAFE_SENSITIVE_EXPLANATION_PATTERN), match.group(2)
            ):
                return True

    provider_id_pattern = (
        rf"(^|[^a-z0-9])(?:{'|'.join(PROVIDER_ID_PREFIXES)})"
        rf"(?:[ _-]+[a-z]+)*[ _-]+id"
        rf"(?:\s*[:=]\s*|\s+)(\S.*)"
    )
    for match in re.finditer(provider_id_pattern, lowered):
        if not re.fullmatch(
            _python_regex(SAFE_REFERENCE_EXPLANATION_PATTERN), match.group(2)
        ):
            return True

    reference_pattern = (
        rf"(^|[^a-z0-9])(?:{'|'.join(PROVIDER_REFERENCE_OWNERS)})[ _-]+"
        rf"(?:{'|'.join(PROVIDER_REFERENCE_OBJECTS)})(?:[ _-]+id)?"
        rf"(?:\s*[:=]\s*|\s+)(\S.*)"
    )
    for match in re.finditer(reference_pattern, lowered):
        if not re.fullmatch(
            _python_regex(SAFE_REFERENCE_EXPLANATION_PATTERN), match.group(2)
        ):
            return True
    return False


def _has_unsafe_provider_id_assignment(value: object) -> bool:
    if isinstance(value, list):
        return any(_has_unsafe_provider_id_assignment(item) for item in value)
    if not isinstance(value, dict):
        return False

    for key, item in value.items():
        separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
        separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
        normalized_key = re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")
        if PROVIDER_ID_KEY_PATTERN.fullmatch(normalized_key) and (
            not isinstance(item, str)
            or re.fullmatch(
                _python_regex(SAFE_REFERENCE_EXPLANATION_PATTERN), item.lower()
            )
            is None
        ):
            return True
        if _has_unsafe_provider_id_assignment(item):
            return True
    return False


def _string_values(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _string_values(item)


def test_validation_migration_defines_strict_version_bound_knowledge() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "create table public.erp_action_validation_knowledge" in sql
    assert "erp_action_versions_connector_identity_unique" in sql
    assert "unique (connector_id, action_id, version_id)" in sql
    assert "unique (connector_id, action_id, version_id, environment, run_id)" in sql
    assert (
        "foreign key (connector_id, action_id, version_id) references "
        "public.erp_action_versions(connector_id, action_id, version_id) on delete restrict"
    ) in compact
    assert "approved_public boolean not null default false" in compact
    assert "opaque_evidence_id ~ '^ev_[a-z0-9_]{8,128}$'" in compact
    assert "run_id ~ '^run_[a-z0-9_]{8,128}$'" in compact
    assert "evidence_sha256 ~ '^[0-9a-f]{64}$'" in compact
    assert "latency_ms is null or latency_ms >= 0" in compact
    assert "expires_at is null or expires_at > evaluated_at" in compact

    for values in (
        "'sandbox', 'test', 'uat', 'production'",
        (
            "'live_success', 'live_failed', 'contract_validated', "
            "'blocked_missing_credentials', 'blocked_missing_prerequisite', "
            "'blocked_external_effect', 'unsupported_by_sandbox', 'outcome_unknown'"
        ),
        "'documented', 'contract_validated', 'sandbox_observed', 'accountant_reviewed'",
        (
            "'discovery_only', 'sandbox_read', 'sandbox_write_with_approval', "
            "'production_pending_validation', 'blocked'"
        ),
        "'completed', 'quarantined', 'failed'",
    ):
        assert values in compact


def test_validation_migration_has_exact_lookup_and_coverage_indexes() -> None:
    compact = _compact(_sql())

    assert (
        "create index if not exists erp_validation_exact_lookup_idx on "
        "public.erp_action_validation_knowledge "
        "(connector_id, action_id, version_id, environment, evaluated_at desc)"
    ) in compact
    assert (
        "create index if not exists erp_validation_coverage_idx on "
        "public.erp_action_validation_knowledge "
        "(connector_id, environment, validation_status, approved_public)"
    ) in compact


def test_validation_and_observations_are_append_only_with_constant_errors() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "set search_path = pg_catalog, pg_temp" in compact
    assert "message = 'erp_validation_evidence_is_append_only'" in compact
    assert "errcode = '55000'" in compact
    assert (
        "create trigger erp_action_validation_knowledge_is_append_only before update or delete "
        "on public.erp_action_validation_knowledge"
    ) in compact
    assert (
        "create trigger erp_action_observations_are_append_only before update or delete "
        "on public.erp_action_observations"
    ) in compact
    assert (
        "drop trigger if exists erp_action_validation_knowledge_is_append_only on "
        "public.erp_action_validation_knowledge"
    ) in compact
    assert (
        "drop trigger if exists erp_action_observations_are_append_only on "
        "public.erp_action_observations"
    ) in compact
    assert "revoke truncate on table public.erp_action_validation_knowledge" in compact
    assert "revoke truncate on table public.erp_action_observations" in compact


def test_validation_migration_is_service_role_only_without_permissive_policies() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "alter table public.erp_action_validation_knowledge enable row level security" in compact
    assert (
        "revoke all on table public.erp_action_validation_knowledge "
        "from public, anon, authenticated"
    ) in compact
    assert "grant all on table public.erp_action_validation_knowledge to service_role" in compact
    assert "create policy" not in sql
    for function in (
        "public.mercury_validation_test_guard_matches(text)",
        "public.jsonb_has_forbidden_validation_key(jsonb)",
        "public.validation_text_has_forbidden_value(text)",
        "public.jsonb_has_forbidden_validation_value(jsonb)",
        "public.jsonb_is_safe_validation_string_array(jsonb)",
        "public.jsonb_is_safe_validation_response_shape(jsonb)",
        "public.jsonb_is_safe_validation_semantic_contract(jsonb)",
        "public.reject_validation_evidence_mutation()",
    ):
        assert f"revoke all on function {function} from public, anon, authenticated" in compact
        assert f"grant execute on function {function} to service_role" in compact


def test_validation_migration_has_service_role_only_server_isolation_guard() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert (
        "create or replace function public.mercury_validation_test_guard_matches( "
        "expected_marker text )"
    ) in compact
    body = _function_body(sql, "mercury_validation_test_guard_matches")
    body_compact = _compact(body)
    assert "returns boolean language sql stable parallel safe" in body_compact
    assert "set search_path = pg_catalog, pg_temp" in body_compact
    assert "current_setting('app.mercury_validation_test_guard', true)" in body_compact
    assert "expected_marker ~ '^[a-za-z0-9][a-za-z0-9_-]{7,63}$'" in body_compact
    assert "coalesce(" in body_compact
    assert "raise" not in body_compact
    assert "format(" not in body_compact


def test_validation_migration_recursively_rejects_normalized_sensitive_key_fragments() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "jsonb_path_query" in sql
    assert "$.**.keyvalue()" in sql
    assert "regexp_replace" in sql
    for fragment in (
        "authorization",
        "token",
        "secret",
        "password",
        "api_key",
        "client_id",
        "client_secret",
        "path",
        "uri",
        "raw",
        "payload",
        "response",
    ):
        assert f"'{fragment}'" in sql

    assert "constraint erp_validation_public_json_safe check" in compact
    for column in ("prerequisites", "limitations", "response_shape", "semantic_contract"):
        assert f"not public.jsonb_has_forbidden_validation_key({column})" in compact


def test_validation_migration_preserves_safe_typed_schema_field_names() -> None:
    sql = _sql()
    function_body = _function_body(sql, "jsonb_has_forbidden_validation_key")

    for safe_field_name in (
        "email",
        "document_id",
        "record_id",
        "counterparty_tax_id",
    ):
        assert f"'{safe_field_name}'" not in function_body


def test_validation_migration_rejects_unsafe_json_and_text_values() -> None:
    sql = _sql()
    compact = _compact(sql)

    for function_name in (
        "validation_text_has_forbidden_value(value text)",
        "jsonb_has_forbidden_validation_value(value jsonb)",
        "jsonb_is_safe_validation_string_array(value jsonb)",
        "jsonb_is_safe_validation_response_shape(value jsonb)",
        "jsonb_is_safe_validation_semantic_contract(value jsonb)",
    ):
        assert f"create or replace function public.{function_name}" in compact

    text_body = _function_body(sql, "validation_text_has_forbidden_value")
    for marker in (
        "strpos(value, '@')",
        "strpos(lower(value), '://')",
        "'bearer '",
        "'github_pat_'",
        "'../'",
        "'[[:cntrl:]]'",
    ):
        assert marker in text_body

    json_body = _function_body(sql, "jsonb_has_forbidden_validation_value")
    assert "jsonb_path_query" in json_body
    assert "jsonb_typeof(nodes.item) in ('number', 'boolean', 'null')" in _compact(json_body)
    assert "$.**.keyvalue()" in json_body
    assert "as provider_id_entry(item)" in _compact(json_body)
    assert "as normalized_provider_id(normalized_key)" in _compact(json_body)
    assert "(_[a-z]+)*_id$" in json_body
    assert "provider_id_entry.item->>'value' !~" in _compact(json_body)
    assert "nodes.item #>> '{}' ~ '[[:digit:]]'" not in _compact(json_body)
    assert "([[:digit:]][^[:alnum:]]*){9}" in text_body
    assert "([a-z]+[0-9]+|[0-9]+[a-z]+)" in text_body

    assert "constraint erp_validation_public_value_safe check" in compact
    for column in (
        "summary_th",
        "summary_en",
        "recommended_next_step",
        "status_class",
        "reviewed_by",
        "runner_version",
    ):
        assert f"not public.validation_text_has_forbidden_value({column})" in compact
    for column in ("prerequisites", "limitations", "response_shape", "semantic_contract"):
        assert f"not public.jsonb_has_forbidden_validation_value({column})" in compact
    assert "public.jsonb_is_safe_validation_string_array(prerequisites)" in compact
    assert "public.jsonb_is_safe_validation_string_array(limitations)" in compact
    assert "public.jsonb_is_safe_validation_response_shape(response_shape)" in compact
    assert "public.jsonb_is_safe_validation_semantic_contract(semantic_contract)" in compact


def test_validation_migration_uses_complete_table_driven_label_contract() -> None:
    text_body = _function_body(_sql(), "validation_text_has_forbidden_value")
    compact = _compact(text_body)

    assert "as forbidden_label(label)" in compact
    assert "replace(forbidden_label.label, ' ', '[ _-]+')" in compact
    assert "as provider_id_prefix(label)" in compact
    assert "as reference_owner(label)" in compact
    assert "as reference_object(label)" in compact
    forbidden_label_table = text_body.split("]) as forbidden_label(label)", 1)[0].rsplit(
        "from unnest(array[", 1
    )[1]
    for label in FORBIDDEN_VALUE_LABELS:
        assert f"'{label}'" in forbidden_label_table

    provider_id_table = text_body.split("]) as provider_id_prefix(label)", 1)[0].rsplit(
        "from unnest(array[", 1
    )[1]
    for prefix in PROVIDER_ID_PREFIXES:
        assert f"'{prefix}'" in provider_id_table
    assert "unnest(array['provider', 'source']) as reference_owner(label)" in compact
    assert "unnest(array['record', 'document']) as reference_object(label)" in compact

    assert SAFE_SENSITIVE_EXPLANATION_PATTERN in text_body
    assert SAFE_REFERENCE_EXPLANATION_PATTERN in text_body
    assert "cross join lateral regexp_matches(" in compact
    assert "labelled_forbidden.parts[4] !~" in compact
    assert "labelled_provider_id.parts[5] !~" in compact
    assert "labelled_reference.parts[5] !~" in compact
    assert text_body.count("([^[:space:]].*)") == 3

    unconditional_fragments = text_body.split("from unnest(array[", 1)[1].split(
        "]) as forbidden", 1
    )[0]
    for label_only_fragment in (
        "access token",
        "api key",
        "auth token",
        "client credential",
        "client secret",
        "credential value",
        "refresh token",
        "secret value",
        "source record",
        "raw payload",
        "raw response",
        "request body",
        "request payload",
        "response body",
        "response payload",
        "provider response",
    ):
        assert f"'{label_only_fragment}'" not in unconditional_fragments


def test_labelled_value_patterns_preserve_all_254_semantic_contracts() -> None:
    contracts: list[dict[str, object]] = []
    for path in SEMANTIC_CONTRACTS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        contracts.extend(payload["contracts"])

    assert len(contracts) == 254
    for contract in contracts:
        assert not _has_unsafe_provider_id_assignment(contract)
        for value in _string_values(contract):
            assert not _has_labelled_actual_value(value)


def test_controlled_qualification_summaries_remain_text_safe() -> None:
    summaries = (*SUMMARY_EN.values(), *SUMMARY_TH.values())

    assert len(SUMMARY_EN) == 8
    assert len(SUMMARY_TH) == 8
    assert len(summaries) == 16
    assert (
        "Provider credentials are not available for live validation." in summaries
    )
    for summary in summaries:
        assert not _has_labelled_actual_value(summary)


def test_labelled_value_contract_covers_complete_generated_assignment_matrix() -> None:
    safe_metadata_values = (
        "provider credentials are not available",
        "Provider credentials are not available for live validation.",
        "client secret is unavailable",
        "password is redacted",
        "api key should be omitted",
        "authorization header is unavailable",
        "raw payload should be redacted",
        "request payload is omitted",
        "source path is redacted",
        "provider record identifier",
        "source document string",
        "email:string",
        "document_id:string",
        "record_id:string",
        "provider_id:provider identifier",
        "provider_external_record_id:provider record identifier",
        "counterparty_tax_id:counterparty tax identifier",
    )
    for safe_metadata in safe_metadata_values:
        assert not _has_labelled_actual_value(safe_metadata)

    unsafe_values = _labelled_forbidden_values()
    assert len(unsafe_values) >= 120
    for reviewer_example in (
        "authorization_header: !value",
        "auth header = '#1234'",
        "client_id: !value",
        "api_key = '#1234'",
        'access token: "!value"',
        "cookie: #1234",
        "session_token = '#1234'",
        "raw_payload: !value",
        "request: #1234",
        "response = '!value'",
        "document_id: #1234",
        "record id = '!value'",
        "provider_id: #1234",
        "credentials are not available for live validation. !value",
        "provider record identifier #1234",
    ):
        assert reviewer_example in unsafe_values or _has_labelled_actual_value(
            reviewer_example
        )

    for unsafe_value in unsafe_values:
        assert _has_labelled_actual_value(unsafe_value)

    for prefix in PROVIDER_ID_PREFIXES:
        assert _has_unsafe_provider_id_assignment({f"{prefix}_id": "#1234"})
        assert not _has_unsafe_provider_id_assignment({f"{prefix}_id": "string"})

    assert not _has_unsafe_provider_id_assignment(
        {
            "email": "string",
            "document_id": "string",
            "record_id": "string",
            "counterparty_tax_id": "counterparty tax identifier",
            "action_id": "act_" + "0" * 24,
            "version_id": "av_" + "0" * 64,
        }
    )


def test_validation_value_helpers_allow_only_typed_shapes_and_semantic_metadata() -> None:
    sql = _sql()
    response_body = _function_body(sql, "jsonb_is_safe_validation_response_shape")
    semantic_body = _function_body(sql, "jsonb_is_safe_validation_semantic_contract")

    assert "'boolean', 'integer', 'null', 'number', 'string', 'truncated', 'unknown'" in _compact(
        response_body
    )
    for semantic_field in (
        "business_object",
        "operation",
        "accounting_uses",
        "output_semantics",
        "join_keys",
        "next_action_ids",
        "required_external_capabilities",
        "optional_external_capabilities",
        "fallbacks",
    ):
        assert f"'{semantic_field}'" in semantic_body
    assert "jsonb_typeof(value->'output_semantics') <> 'object'" in _compact(semantic_body)
    assert "^[a-z]+( [a-z]+)+$" in semantic_body
