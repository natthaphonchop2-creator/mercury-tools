"""Frozen, contract-only validation for the built-in PEAK catalog."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from mercury_tools.catalog.identity import canonical_json
from mercury_tools.catalog.models import (
    CatalogAction,
    CatalogSource,
    revalidate_catalog_action,
    revalidate_catalog_source,
)
from mercury_tools.catalog.schema_contract import validate_required_schema_contract
from mercury_tools.drivers.peak import PeakDriver
from mercury_tools.execution.request_builder import build_request
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationReport,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.semantics import (
    SemanticMap,
    load_actions,
    load_semantic_contracts,
    require_semantic_contract,
)
from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

PEAK_ACTION_COUNT = 64
PEAK_CANONICAL_IDENTITIES = (
    (
        "act_0114957a3922dc0f46a99907",
        "av_e90f0f57aa76ffc8d44b6eadd1fd6df5df9a3b2216e5b4873c56f607c84e185b",
    ),
    (
        "act_0b47bbf8bcc51ceada745adc",
        "av_64c34c16981733e9610a7b75a4ee436bc9353dc5855847fb6c0bca560598e4fb",
    ),
    (
        "act_0dcc727c86ca16631816ed12",
        "av_efd1473944186910b40174f8ed5c3e14760842cd976d257c7a546b80cc68bdc4",
    ),
    (
        "act_260661133c4f8b1fbbeeadac",
        "av_45e3354092406838198cebc27088d4a17bab3f5b67fdb8e3916a1bc0573a65ec",
    ),
    (
        "act_308b782f3c276b9a0dd818d3",
        "av_ad8a337c9a7ec0772f3086aba4417a0a5f6fd35d7dbaef6bc2082f27e83acb4b",
    ),
    (
        "act_3863cfee28a4ce0ab4f65ab9",
        "av_9f3558d396f85dbf67b2b6f33149b83d2de9fd87338ac77296da6677de4d6fea",
    ),
    (
        "act_3c4cc6c07a8ffa418232c909",
        "av_925aec344cbec2e51c697fc8ed9e8c3694dafe084aa1490367a0b0ea3781aa9a",
    ),
    (
        "act_3f63028cae55eb90783e7be5",
        "av_69d2b44a34525755f6412007a7b77fd734a87b062a20a3075b8e401c9b726cac",
    ),
    (
        "act_402bb61694a153488b29ab33",
        "av_426f74c9a69838ccf860b9a4e7b012427bb054b9e53290467e855b17d5e8c7c2",
    ),
    (
        "act_41394a4923b15a4c53cf644d",
        "av_9d45a828d94cc245966e5b84f06237f4593441cba965d8638ce463ccb7d4a4cf",
    ),
    (
        "act_46c9288523202918dd477367",
        "av_232d5f124f25a1919bcfc532cd11b13a4297bf2f269aa7bd939e3a9cb9158ff5",
    ),
    (
        "act_46db1343db1c07b58d37cf3e",
        "av_49b3f621d6d203832725a9e41048a8fe1f238c4e0104380a44b9b3140235b4e3",
    ),
    (
        "act_4ec1db144d0b79dbdc133236",
        "av_43d6d67e148ce3f1a22093b403f8e7623f40a1cfb0b49b66d533725d47b81487",
    ),
    (
        "act_5ab58230233fb4cff85d36ff",
        "av_b0a1656158d5d61217e14425079bce567b4f83461666e5ebe0d2c139e15b9a2d",
    ),
    (
        "act_5ccd2472984a6e8007989784",
        "av_fcaf0996c0741992aa8834cb312eff44d07f978339950e6a236aafbfae53397d",
    ),
    (
        "act_5d022ddce93542bed3ba3c55",
        "av_5cf9adec93819b0a4e6a70ee73354837efd6a81ca9a57df602aafe321526f4f7",
    ),
    (
        "act_6aa9e1aeb5c70e874a3a2b19",
        "av_c88df2a2ff7072c5841588542601cbe7a922f4092573f8c359651ce0d31685c7",
    ),
    (
        "act_6e49e877eb897677e2eb5b15",
        "av_2cc96c450746945131c19eb3524333ad1673675ed382356fc406f7a3de57a7cd",
    ),
    (
        "act_6eca4e9dd9b68da50839acc2",
        "av_a5f787fa68a7f806aee01305379cc1c749c86b101037acd1c134128ed32c8e70",
    ),
    (
        "act_7284bd508c7d69b2062caf86",
        "av_3cf9bfc8de12027f273a440aad0d41f1bc7c643703c4030f5f0e4118386cf6ff",
    ),
    (
        "act_7771eabbe70dd3c4cb2db76f",
        "av_5ec713eea2450448adf233566e1f379804f8c833a9ce5fff3f8dcacdd31f01bd",
    ),
    (
        "act_86726c451fa0efd2550e9991",
        "av_1fefc350b2828a4f708dc1110f2063c3d3900081ab81c7931298659281d403b7",
    ),
    (
        "act_88d77abde2055b5bc7d1dd13",
        "av_4d240b35b1b0da28d29a553a08052d731cc10d39611e5216732b2f687e9671e5",
    ),
    (
        "act_8db2ad5402e03ff26f75d826",
        "av_49f42dce3250346e7ece1d58a428603efdbdec0fe21c20e3624d1238767fa4a6",
    ),
    (
        "act_8fcd208ff82ccc6429492e75",
        "av_7fb0e6e4b7032730f882bc7057b290345988d706ced797b4e80c99d69ee9de4f",
    ),
    (
        "act_926b428b4b14729473e0e0c0",
        "av_e9b8c7ceadd7c41a4629cdde534e2c2860de757134d8da65d3ae37ea9e51aaae",
    ),
    (
        "act_92c9d694f30bc103781a62ee",
        "av_785284030716a1fd303018d44e0dd30a529b03e16c2050739da51887310133ac",
    ),
    (
        "act_93a09cc8c33bbcb6f9ac3679",
        "av_18410c8b6ef21f3e22a1566fd9d5bdd5fd81e43b295e4c57f03cfb1a5b341faa",
    ),
    (
        "act_93fa566f5267d83e04faba9e",
        "av_b68db796eca6cc05263da138c22b97543bb5b86beda3fd865216c61ce3fa7365",
    ),
    (
        "act_97010c84618ec71bd7944a7b",
        "av_54ef41085a72594e463e78e2adb232e82f8ddff144ec4bad5a9e3d6f27e11b6e",
    ),
    (
        "act_97c2fd64b9ffb9e25179c49d",
        "av_1e6a7690dd1dbc4355511da3072d7f1007061ad0584422edb84273b927a81364",
    ),
    (
        "act_987d7da49b5c2152305f4fcd",
        "av_dc27cf11dd36fce7de32fbce2e8e98e319b8b8c2a30cdf3d11daf32dfd2dd932",
    ),
    (
        "act_9e8c8d5a1fe33e4a9c28cef1",
        "av_ff825691bca5afc1ff26d32c7d3187eb800b765ca141a1551f4df1da93b3f4b1",
    ),
    (
        "act_a739859a3ff8941fa0a6c25c",
        "av_a6f49e25549cd6a23dbcd92fb4fa015e72da454f620831dcf2c721783c0e7f2a",
    ),
    (
        "act_a7f3097c2a8e59319c0dd716",
        "av_fc52ce410834f3b8ef8765f50d728e950daaae3532db5634c4b69eeb36d3723a",
    ),
    (
        "act_a854ec32e2b7ac849f11deeb",
        "av_2981d281c7e87500e3f69ba6983ff29f46d465edeecfb81132c819613174904b",
    ),
    (
        "act_a86f5ab29488d2109529cfdb",
        "av_40ca39dda84fff81fe0807cef514918d2cc3cb72120f379b75d97667ec8fca0c",
    ),
    (
        "act_ab53b5325bebc62984f4516b",
        "av_de5a2a991b1a14274f1dc023bf6b409f315901717e2de449e7d16f64a70a78a9",
    ),
    (
        "act_ae4c4adac955fc15bf85865f",
        "av_f4298735030d1cf9418c18932ebd9ca392eed0512f8e9dc7d831d981c19e74ab",
    ),
    (
        "act_b76683cad7af033640036e7c",
        "av_0cd6a8b4a0fd0ea9586cc3e04da267f13d9b4dfd075744ae6da514f6a997ffc3",
    ),
    (
        "act_baa0f8fc773de48ffe631499",
        "av_addb9843ea052124c5c3b76e471992be7e76745443e006a204e85ccfce023766",
    ),
    (
        "act_bd70b00e0df3d157d22fb1a1",
        "av_63fe4bcf9dc737a03677aa013bc729b5d0b891e7f5bc2459f37335dba0f5066a",
    ),
    (
        "act_c022b664b9954b24789e0d5d",
        "av_5fcbb88fa7dda38d868b23d6a03a1a2d90e1610d1307b986120e1382a62b7910",
    ),
    (
        "act_c0fb09d59ce450c3819a9521",
        "av_24d7e1e075d9a9bc73df29abe509aeae6192ffbe6bbcdcb347236f66021ec188",
    ),
    (
        "act_c2feb6d508c7ad482c11a468",
        "av_dc13337ef05ecd11cf9f96d268e39d2c9052f6cf7754941cd8199cfa6676cfbd",
    ),
    (
        "act_c4af9d26172dd61c9a9e4432",
        "av_30d136bf4ce01df411bec6e1eefb1231c1b192f0f23aedf069b9c7c1da93f246",
    ),
    (
        "act_c5b0baafb0d76261e443457c",
        "av_309d6faf7293a0efa9c1674c78348ef1a2c67ba9ada0d433e9e0b400a07e63dd",
    ),
    (
        "act_cbe47f843982380a2f6e3bf5",
        "av_6d89b22e8f3bb88b85ee5703a1052b00d3ffc43b8418be6e5f50861ace662548",
    ),
    (
        "act_ccc3343c893ddeb9a4d4a207",
        "av_3123b9451db9b6d22a5462ccd54f86cdd3bdf3c180d0c1e226a7c3bdfb95e53a",
    ),
    (
        "act_cfea2f8629ca1587e64c87cd",
        "av_2dd329eeb5c11c23035ac83cd454616179a8ed1105fcb52e0cc49603b455fc23",
    ),
    (
        "act_d1034a254d9cc65822cc83c9",
        "av_5ff413f0c4e9f98fd08880dddedde7476614f25bdbddf0863277b05131f8c30a",
    ),
    (
        "act_d14adea3e772e3f406ba790e",
        "av_f2bd081dc2067351eeb62cf1b2dd060ffafeb33267a03015fb59d1301a9d8453",
    ),
    (
        "act_d1aa008c401d40255ca0738e",
        "av_8da87567c98b9516f3b1544d6a9ed4ce3221a8351b9405831bb4bee48e44cf6e",
    ),
    (
        "act_d1d6ef93ff84247b17baa719",
        "av_3b4842aada8bf8dbea88e14ee706fd3f72263bfb8cd73dbfe25bbb69afcf87f6",
    ),
    (
        "act_d34838c6775859ea15830d5d",
        "av_219c776f6a04c9f2171cdee9374d2f86f4f06f88e076eb50e40266285cd74099",
    ),
    (
        "act_dbb5a5862c26dec57ef1a220",
        "av_749ee62799630ab9a93acb2f8f3c032d9d7b9e1519099527835dff14c3b6f613",
    ),
    (
        "act_e370123e4fd83afb6490cdf9",
        "av_195369104f5c56d7e688d27ef193d20e8dd301d579084bf06698e44e6564ab09",
    ),
    (
        "act_e4744c138983b7fd8fd9b402",
        "av_3a19d251a67a38310d1588f78af5b892050441d94a9af3064eb06297547cbb0b",
    ),
    (
        "act_e5ebcd779f494d89d8e53d2e",
        "av_fe4a0379ed6537a2399bf5764958ae516153a6666258f193d881f9c009b79616",
    ),
    (
        "act_e705bc1495702ad0914e3494",
        "av_628c8f5f4831fb719fd601a9732dfab1c17fa089b452327089951da636295b0b",
    ),
    (
        "act_eaadd03ba498084a17d266fa",
        "av_188115eac24b83b988635daa3609c2ac36c04a40b608bfcb0a40e89d535bd461",
    ),
    (
        "act_ed12fe4f9e2c080243869ffa",
        "av_549b0147da22d0d31f53bf5a7b0f64d77131a59dfb05337bbaaa2013e511e333",
    ),
    (
        "act_ef839e82267a176b9fc738b8",
        "av_fe59716b6fa42ae39c362e37a229ae04a2207badd0e36523100a539d57420145",
    ),
    (
        "act_f087bdd426544f61eb91387a",
        "av_d0475609954bed84beb0fa68aecb891b02d4a2cd4aa2205db64f415b8145d57b",
    ),
)
_PEAK_CANONICAL_IDENTITY_SET = frozenset(PEAK_CANONICAL_IDENTITIES)
_ENDPOINT_FIELDS = frozenset(
    {
        "confidence",
        "content_type",
        "description",
        "input_schema",
        "method",
        "operation_id",
        "path_template",
    }
)
_INPUT_SECTIONS = frozenset({"path", "query", "headers", "body", "files"})
_PATH_VARIABLE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
_RUN_ID = "run_00000000000000000000000000"
_EVALUATED_AT = datetime(1970, 1, 1, tzinfo=UTC)

if (
    len(PEAK_CANONICAL_IDENTITIES) != PEAK_ACTION_COUNT
    or len(_PEAK_CANONICAL_IDENTITY_SET) != PEAK_ACTION_COUNT
):
    raise RuntimeError("peak_builtin_identity_source_invalid")


@dataclass(frozen=True)
class _DocumentedEndpoint:
    method: str
    path_template: str
    operation_id: str
    content_type: str
    description: str
    confidence: str
    input_schema: Mapping[str, Any]
    fingerprint: str


class PeakQualificationReport(QualificationReport):
    """Contract-only PEAK report with an explicit zero-attempt boundary."""

    http_attempts: int = Field(default=0, ge=0, le=0)
    mutation_attempts: int = Field(default=0, ge=0, le=0)

    def public_dict(self) -> dict[str, Any]:
        counts = Counter(record.validation_status.value for record in self.records)
        return {
            "connector_id": self.connector_id,
            "environment": self.environment,
            "run_id": self.run_id,
            "run_state": self.run_state.value,
            "total": self.total,
            "http_attempts": self.http_attempts,
            "mutation_attempts": self.mutation_attempts,
            "counts": {key: counts[key] for key in sorted(counts)},
            "records": [record.model_dump(mode="json") for record in self.records],
        }


def validate_peak_documented_contracts(
    *,
    source: CatalogSource,
    actions: Sequence[CatalogAction],
    semantics: SemanticMap,
    file_fixture: Path,
) -> PeakQualificationReport:
    """Validate all frozen PEAK contracts without credentials or provider I/O."""

    checked_source = _require_peak_source(source)
    checked_actions = require_canonical_peak_actions(actions)
    _require_exact_semantic_coverage(semantics)
    fixture = _require_fixture_path(file_fixture)
    document = checked_source.sanitization["document"]
    if not isinstance(document, Mapping):
        raise ValueError("peak_documented_source_invalid")
    documented = endpoint_index(document.get("endpoints"))
    production_base_url = PeakDriver().resolve_base_url("production")

    records: list[ValidationKnowledge] = []
    matched_endpoints: set[str] = set()
    for action in sorted(checked_actions, key=lambda item: (item.action_id, item.version_id)):
        endpoint = _require_documented_endpoint(action, documented)
        matched_endpoints.add(endpoint.fingerprint)
        semantic_contract = require_semantic_contract(action, semantics)
        build_request(
            action,
            production_base_url,
            contract_fixture_inputs(action, fixture),
            (fixture.parent,),
            environment="production",
        )
        records.append(_blocked_contract_record(action, semantic_contract))

    documented_count = sum(len(entries) for entries in documented.values())
    if (
        documented_count != PEAK_ACTION_COUNT
        or len(matched_endpoints) != PEAK_ACTION_COUNT
        or documented_count != len(matched_endpoints)
    ):
        raise ValueError("peak_documented_endpoint_coverage_invalid")
    return _build_report(records)


def load_peak_contract_report(repository_root: Path) -> PeakQualificationReport:
    """Load checked-in PEAK sidecars and validate them with a local fixture."""

    try:
        root = Path(repository_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError("peak_catalog_root_invalid") from None

    catalog_root = root / "catalog" / "global" / "peak"
    source = _load_source(catalog_root / "source.json")
    actions = tuple(load_actions(catalog_root / "actions.json"))
    semantics = load_semantic_contracts(catalog_root / "semantic-contracts.json", actions)
    with tempfile.TemporaryDirectory(prefix="mercury-peak-contract-") as directory:
        fixture = Path(directory) / "fixture.pdf"
        fixture.write_bytes(b"%PDF-1.4\n% contract-only fixture\n")
        return validate_peak_documented_contracts(
            source=source,
            actions=actions,
            semantics=semantics,
            file_fixture=fixture,
        )


def require_canonical_peak_actions(actions: Sequence[CatalogAction]) -> tuple[CatalogAction, ...]:
    """Require the exact 64 immutable PEAK action identities."""

    try:
        checked = tuple(revalidate_catalog_action(action) for action in actions)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("peak_catalog_coverage_invalid") from None
    identities = tuple((action.action_id, action.version_id) for action in checked)
    if (
        len(checked) != PEAK_ACTION_COUNT
        or len(set(identities)) != PEAK_ACTION_COUNT
        or frozenset(identities) != _PEAK_CANONICAL_IDENTITY_SET
        or any(action.connector_id != "peak" for action in checked)
    ):
        raise ValueError("peak_catalog_coverage_invalid")
    return checked


def endpoint_index(value: object) -> dict[tuple[str, str], tuple[_DocumentedEndpoint, ...]]:
    """Index sanitized document rows while rejecting untrusted endpoint shapes."""

    if not isinstance(value, Sequence) or isinstance(value, (bytes, str)):
        raise ValueError("peak_documented_endpoint_index_invalid")

    routes: dict[tuple[str, str], list[_DocumentedEndpoint]] = {}
    fingerprints: set[str] = set()
    for raw_endpoint in value:
        endpoint = _validated_documented_endpoint(raw_endpoint)
        if endpoint.fingerprint in fingerprints:
            raise ValueError("peak_documented_endpoint_duplicate")
        fingerprints.add(endpoint.fingerprint)
        routes.setdefault((endpoint.method, endpoint.path_template), []).append(endpoint)
    return {
        route: tuple(sorted(entries, key=lambda item: item.fingerprint))
        for route, entries in routes.items()
    }


def contract_fixture_inputs(action: CatalogAction, file_fixture: Path) -> dict[str, Any]:
    """Build deterministic, credential-free inputs from the declared schema only."""

    schema = _validate_input_schema(action.input_schema)
    inputs: dict[str, Any] = {}
    path_schema = schema["path"]
    path_names = frozenset(_PATH_VARIABLE.findall(action.path_template))
    if path_names:
        inputs["path"] = {
            name: _fixture_schema_value(path_schema.get(name, {})) for name in sorted(path_names)
        }
    for section in ("query", "headers"):
        required = {
            name: _fixture_schema_value(declaration)
            for name, declaration in schema[section].items()
            if declaration.get("required") is True
        }
        if required:
            inputs[section] = required
    required_files = {
        name: str(file_fixture)
        for name, declaration in schema["files"].items()
        if declaration.get("required") is True
    }
    if required_files:
        inputs["files"] = required_files
    if schema["body"].get("x-mercury-required") is True:
        inputs["body"] = _fixture_schema_value(schema["body"])
    return inputs


def _require_peak_source(source: CatalogSource) -> CatalogSource:
    if not isinstance(source, CatalogSource):
        raise ValueError("peak_documented_source_invalid")
    try:
        checked = revalidate_catalog_source(source)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("peak_documented_source_invalid") from None
    if checked.connector_id != "peak":
        raise ValueError("peak_documented_source_invalid")
    return checked


def _require_exact_semantic_coverage(semantics: SemanticMap) -> None:
    if not isinstance(semantics, Mapping) or set(semantics) != _PEAK_CANONICAL_IDENTITY_SET:
        raise ValueError("peak_semantic_contract_coverage_invalid")
    if any(not isinstance(contract, SemanticContract) for contract in semantics.values()):
        raise ValueError("peak_semantic_contract_coverage_invalid")


def _require_fixture_path(file_fixture: Path) -> Path:
    if not isinstance(file_fixture, Path) or not file_fixture.name:
        raise ValueError("peak_fixture_path_invalid")
    try:
        if not file_fixture.parent.is_dir():
            raise ValueError("peak_fixture_path_invalid")
    except OSError:
        raise ValueError("peak_fixture_path_invalid") from None
    return file_fixture


def _validated_documented_endpoint(value: object) -> _DocumentedEndpoint:
    if not isinstance(value, Mapping) or set(value) != _ENDPOINT_FIELDS:
        raise ValueError("peak_documented_endpoint_invalid")
    method = value["method"]
    path_template = value["path_template"]
    operation_id = value["operation_id"]
    content_type = value["content_type"]
    description = value["description"]
    confidence = value["confidence"]
    input_schema = _validate_input_schema(value["input_schema"])
    if not all(
        isinstance(item, str) and item
        for item in (
            method,
            path_template,
            operation_id,
            content_type,
            description,
            confidence,
        )
    ):
        raise ValueError("peak_documented_endpoint_invalid")
    fingerprint = _endpoint_fingerprint(
        method=method,
        path_template=path_template,
        operation_id=operation_id,
        content_type=content_type,
        description=description,
        confidence=confidence,
        input_schema=input_schema,
    )
    return _DocumentedEndpoint(
        method=method,
        path_template=path_template,
        operation_id=operation_id,
        content_type=content_type,
        description=description,
        confidence=confidence,
        input_schema=input_schema,
        fingerprint=fingerprint,
    )


def _require_documented_endpoint(
    action: CatalogAction,
    documented: Mapping[tuple[str, str], Sequence[_DocumentedEndpoint]],
) -> _DocumentedEndpoint:
    route = (action.method.value, action.path_template)
    candidates = documented.get(route)
    if not candidates:
        raise ValueError("peak_documented_endpoint_missing")
    expected_fingerprint = _endpoint_fingerprint(
        method=action.method.value,
        path_template=action.path_template,
        operation_id=action.operation_id,
        content_type=action.content_type,
        description=action.description,
        confidence=action.confidence.value,
        input_schema=_validate_input_schema(action.input_schema),
    )
    matched = [item for item in candidates if item.fingerprint == expected_fingerprint]
    if len(matched) != 1:
        raise ValueError("peak_documented_endpoint_contract_mismatch")
    return matched[0]


def _validate_input_schema(value: object) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != _INPUT_SECTIONS:
        raise ValueError("peak_input_schema_invalid")
    validated: dict[str, Mapping[str, Any]] = {}
    for section in ("path", "query", "headers", "files"):
        declarations = value[section]
        if not isinstance(declarations, Mapping):
            raise ValueError("peak_input_schema_invalid")
        for name, declaration in declarations.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(declaration, Mapping)
                or not isinstance(declaration.get("required", False), bool)
            ):
                raise ValueError("peak_input_schema_invalid")
        validated[section] = declarations
    body = value["body"]
    validate_required_schema_contract(body)
    if not isinstance(body, Mapping):
        raise ValueError("peak_input_schema_invalid")
    validated["body"] = body
    return validated


def _endpoint_fingerprint(
    *,
    method: str,
    path_template: str,
    operation_id: str,
    content_type: str,
    description: str,
    confidence: str,
    input_schema: Mapping[str, Any],
) -> str:
    return canonical_json(
        {
            "method": method,
            "path_template": path_template,
            "operation_id": operation_id,
            "content_type": content_type,
            "description": description,
            "confidence": confidence,
            "input_schema": input_schema,
        }
    )


def _fixture_schema_value(schema: Mapping[str, Any]) -> Any:
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (bytes, str)) and enum:
        return enum[0]
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        if not isinstance(properties, Mapping) or not isinstance(required, Sequence):
            return {}
        return {
            name: _fixture_schema_value(properties[name])
            for name in required
            if isinstance(name, str) and isinstance(properties.get(name), Mapping)
        }
    if schema_type == "array":
        return []
    if schema_type == "boolean":
        return True
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "null":
        return None
    return "contract-fixture"


def _blocked_contract_record(
    action: CatalogAction,
    semantic_contract: SemanticContract,
) -> ValidationKnowledge:
    contract = SemanticContract.model_validate(
        {name: getattr(semantic_contract, name) for name in SemanticContract.model_fields}
    )
    payload = {
        "action_id": action.action_id,
        "version_id": action.version_id,
        "connector_id": "peak",
        "environment": "production",
        "validation_status": ValidationStatus.BLOCKED_MISSING_CREDENTIALS.value,
        "evidence_level": EvidenceLevel.CONTRACT_VALIDATED.value,
        "execution_eligibility": ExecutionEligibility.BLOCKED.value,
        "status_class": "not_attempted",
        "semantic_contract": contract.model_dump(mode="json"),
    }
    evidence_sha256 = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ValidationKnowledge(
        opaque_evidence_id="ev_" + evidence_sha256[:26].upper(),
        run_id=_RUN_ID,
        action_id=action.action_id,
        version_id=action.version_id,
        connector_id="peak",
        environment="production",
        validation_status=ValidationStatus.BLOCKED_MISSING_CREDENTIALS,
        evidence_level=EvidenceLevel.CONTRACT_VALIDATED,
        execution_eligibility=ExecutionEligibility.BLOCKED,
        summary_th=SUMMARY_TH[ValidationStatus.BLOCKED_MISSING_CREDENTIALS],
        summary_en=SUMMARY_EN[ValidationStatus.BLOCKED_MISSING_CREDENTIALS],
        limitations=("live_validation_not_attempted",),
        recommended_next_step="configure_connector",
        response_shape={},
        status_class="not_attempted",
        latency_ms=None,
        semantic_contract=contract,
        evidence_sha256=evidence_sha256,
        reviewed_by="local_reviewer",
        runner_version="0.2.2",
        run_state=QualificationRunState.COMPLETED,
        evaluated_at=_EVALUATED_AT,
        expires_at=None,
    )


def _build_report(records: Sequence[ValidationKnowledge]) -> PeakQualificationReport:
    checked = tuple(ValidationKnowledge.model_validate(record) for record in records)
    identities = tuple((record.action_id, record.version_id) for record in checked)
    if (
        len(checked) != PEAK_ACTION_COUNT
        or len(set(identities)) != PEAK_ACTION_COUNT
        or frozenset(identities) != _PEAK_CANONICAL_IDENTITY_SET
    ):
        raise ValueError("peak_record_coverage_invalid")
    for record in checked:
        if (
            record.connector_id != "peak"
            or record.environment != "production"
            or record.run_id != _RUN_ID
            or record.run_state is not QualificationRunState.COMPLETED
            or record.validation_status is not ValidationStatus.BLOCKED_MISSING_CREDENTIALS
            or record.evidence_level is not EvidenceLevel.CONTRACT_VALIDATED
            or record.execution_eligibility is not ExecutionEligibility.BLOCKED
            or record.status_class != "not_attempted"
            or record.latency_ms is not None
        ):
            raise ValueError("peak_record_contract_invalid")
    return PeakQualificationReport(
        connector_id="peak",
        environment="production",
        run_id=_RUN_ID,
        run_state=QualificationRunState.COMPLETED,
        http_attempts=0,
        mutation_attempts=0,
        records=tuple(sorted(checked, key=lambda item: (item.action_id, item.version_id))),
    )


def _load_source(path: Path) -> CatalogSource:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CatalogSource.model_validate(payload)
    except (OSError, TypeError, ValueError):
        raise ValueError("peak_documented_source_invalid") from None


__all__ = [
    "PEAK_ACTION_COUNT",
    "PEAK_CANONICAL_IDENTITIES",
    "PeakQualificationReport",
    "contract_fixture_inputs",
    "endpoint_index",
    "load_peak_contract_report",
    "require_canonical_peak_actions",
    "validate_peak_documented_contracts",
]
