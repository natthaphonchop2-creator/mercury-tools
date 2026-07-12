"""Supabase-backed product state for Mercury Connect."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from mercury_tools.config import Settings, require_supabase
from mercury_tools.connectors.catalog import connector_by_id, list_connector_summaries
from mercury_tools.connectors.setup import required_missing_fields, resolve_setup_state
from mercury_tools.flows.parser import parse_flow_text
from mercury_tools.product import ConnectRequest, normalize_host_app
from mercury_tools.safety.redaction import redact_json
from mercury_tools.workspaces.public import (
    new_public_workspace_id,
    public_workspace_connect_request,
    public_workspace_token_payload,
)

SKILL_CATALOG_SEED: list[dict[str, Any]] = [
    {
        "skill_id": "company-health-check-th",
        "title": "Company Health Check TH",
        "category": "audit",
        "summary": "ตรวจสุขภาพบริษัทจากข้อมูลบัญชีและหลักฐานที่มี พร้อมจุดที่ควรให้บัญชีตรวจทาน",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": ["flowaccount"],
        "tags": ["audit", "thai", "management"],
    },
    {
        "skill_id": "vat-summary-th",
        "title": "VAT Summary TH",
        "category": "tax",
        "summary": "ช่วยสรุป VAT และบริบทภาษีซื้อ/ภาษีขายพร้อม citation จาก Mercury Wiki",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": ["flowaccount"],
        "tags": ["vat", "thai", "tax"],
    },
    {
        "skill_id": "invoice-review-th",
        "title": "Invoice Review TH",
        "category": "audit",
        "summary": "ตรวจใบแจ้งหนี้/ใบกำกับภาษีและจัดเตรียมงานตาม endpoint capability ที่เชื่อมอยู่",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": ["flowaccount"],
        "tags": ["invoice", "audit", "thai"],
    },
    {
        "skill_id": "management-report-th",
        "title": "Management Report TH",
        "category": "reporting",
        "summary": "เตรียม context pack สำหรับรายงานผู้บริหาร: รายได้, VAT, cash flow, margin",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": ["flowaccount"],
        "tags": ["report", "thai", "finance"],
    },
    {
        "skill_id": "connector-setup-guide-th",
        "title": "Connector Setup Guide TH",
        "category": "setup",
        "summary": "แนะนำขั้นตอนเชื่อมโปรแกรมบัญชี โดยแยกข้อมูลที่ต้องถามผู้ใช้กับค่าที่ตั้งล่วงหน้าได้",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": [],
        "tags": ["setup", "connector", "thai"],
    },
    {
        "skill_id": "connector-credential-setup-th",
        "title": "Connector Credential Setup TH",
        "category": "setup",
        "summary": "นำผู้ใช้เชื่อม ERP ทีละขั้นและหยุดรอจนแต่ละขั้นตรวจสอบสำเร็จ",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": [],
        "tags": ["setup", "credentials", "connector", "thai"],
    },
    {
        "skill_id": "flowaccount-connector-setup-th",
        "title": "FlowAccount Connector Setup TH",
        "category": "setup",
        "summary": "เชื่อมและตรวจสอบ FlowAccount แบบ guided setup โดยไม่เปิดเผย credential",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": ["flowaccount"],
        "tags": ["setup", "connector", "flowaccount", "thai"],
    },
    {
        "skill_id": "peak-connector-setup-th",
        "title": "PEAK Connector Setup TH",
        "category": "setup",
        "summary": (
            "แนะนำการเชื่อม PEAK Open API, credential ที่ต้องใช้, "
            "เอกสารอ้างอิง, และ setup validation ก่อนใช้งาน GET/POST endpoint"
        ),
        "status": "available",
        "version": "0.1.0",
        "required_connectors": ["peak"],
        "tags": ["setup", "connector", "peak", "thai"],
    },
    {
        "skill_id": "mercury-flow-runner",
        "title": "Mercury Flow Runner",
        "category": "automation",
        "summary": "วางแผน บันทึก และรัน workflow บัญชีแบบ read-only พร้อม capability gate",
        "status": "available",
        "version": "0.1.0",
        "required_connectors": [],
        "tags": ["flow", "workflow", "automation", "read-only"],
    },
    {
        "skill_id": "flowaccount-journal-posting-th",
        "title": "FlowAccount Journal Posting TH",
        "category": "accounting",
        "summary": (
            "เตรียม ตรวจสมดุล สร้างร่าง และอนุมัติรายการสมุดรายวัน "
            "FlowAccount โดยแยกการยืนยันแต่ละขั้น"
        ),
        "status": "available",
        "version": "0.1.0",
        "required_connectors": ["flowaccount"],
        "tags": ["flowaccount", "journal", "write", "thai"],
    },
]

PRODUCT_STATE_TOOL = "mercury_product_state"
PRODUCT_FALLBACK_LIMIT = 500
PUBLIC_CONNECTOR_METADATA_KEYS = frozenset(
    {
        "setup_state",
        "required_secret_fields",
        "preset",
        "capabilities",
        "enabled_capabilities",
        "validation",
        "source",
    }
)


def slugify(value: str, *, fallback: str = "workspace") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def workspace_key(company: str) -> str:
    normalized = " ".join(company.strip().lower().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{slugify(normalized, fallback='company')}-{digest}"


def utc_from_epoch(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def now_utc() -> str:
    return datetime.now(tz=UTC).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def flow_id_from_title(title: str, flow_yaml: str) -> str:
    digest = hashlib.sha256(f"{title}\n{flow_yaml}".encode()).hexdigest()[:8]
    return f"workspace-{slugify(title, fallback='flow')}-{digest}"


def flow_summary_from_yaml(
    flow_yaml: str,
    *,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    flow = parse_flow_text(flow_yaml)
    flow_title = (title or flow.name).strip()
    if not flow_title:
        raise ValueError("Flow title is required.")
    return {
        "flow_id": flow_id_from_title(flow_title, flow_yaml),
        "title": flow_title,
        "name": flow.name,
        "description": flow.description,
        "tags": flow.tags,
        "command_count": len(flow.commands),
        "on_flow_start_count": len(flow.on_flow_start),
        "on_flow_complete_count": len(flow.on_flow_complete),
        "sha256": hashlib.sha256(flow_yaml.encode()).hexdigest(),
        "status": "draft",
        "yaml": flow_yaml,
        "metadata": metadata or {},
        "updated_at": now_utc(),
    }


def flow_run_summary(
    *,
    flow_id: str | None,
    title: str | None,
    result_payload: dict[str, Any],
    dry_run: bool,
    env_keys: list[str] | None = None,
) -> dict[str, Any]:
    flow = result_payload.get("flow") or {}
    flow_title = str(title or flow.get("name") or flow_id or "Mercury Flow").strip()
    created_at = now_utc()
    clean_env_keys = sorted({str(key).strip() for key in (env_keys or []) if str(key).strip()})
    run_basis = json.dumps(
        {
            "flow_id": flow_id,
            "title": flow_title,
            "status": result_payload.get("status"),
            "dry_run": dry_run,
            "env_keys": clean_env_keys,
            "created_at": created_at,
        },
        sort_keys=True,
    )
    artifacts = []
    for artifact in (result_payload.get("artifacts") or [])[:8]:
        if not isinstance(artifact, dict):
            continue
        artifacts.append(
            {
                "title": artifact.get("title") or artifact.get("message") or "artifact",
                "status": artifact.get("status") or result_payload.get("status"),
            }
        )
    return redact_json(
        {
            "run_id": "flow_run_" + hashlib.sha256(run_basis.encode("utf-8")).hexdigest()[:16],
            "flow_id": flow_id,
            "title": flow_title,
            "status": result_payload.get("status"),
            "dry_run": dry_run,
            "step_count": len(result_payload.get("steps") or []),
            "artifact_count": len(result_payload.get("artifacts") or []),
            "artifacts": artifacts,
            "env_keys": clean_env_keys,
            "created_at": created_at,
        }
    )


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def email_domain(email: str) -> str:
    parts = email.strip().lower().split("@", 1)
    return parts[1] if len(parts) == 2 else ""


def _public_connector_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if str(key) in PUBLIC_CONNECTOR_METADATA_KEYS
    }


def public_connector_profile(profile: dict[str, Any]) -> dict[str, Any]:
    public = dict(profile)
    public["metadata"] = _public_connector_metadata(public.get("metadata"))
    if str(public.get("status") or "").strip().lower() == "connected_read_only":
        public["status"] = "connected"
    return redact_json(public)


def public_connector_profiles(profiles: Any) -> list[dict[str, Any]]:
    if not isinstance(profiles, list):
        return []
    return [
        public_connector_profile(profile)
        for profile in profiles
        if isinstance(profile, dict)
    ]


def public_product_event(row: dict[str, Any]) -> dict[str, Any]:
    return redact_json(dict(row))


def connector_profile_status_from_metadata(metadata: dict[str, Any] | None) -> str:
    return "requires_credentials"


def is_product_schema_error(exc: RuntimeError) -> bool:
    text = str(exc).lower()
    return "mercury_" in text and (
        "404" in text
        or "pgrst" in text
        or "schema cache" in text
        or "does not exist" in text
        or "could not find" in text
    )


def skill_catalog_rows() -> list[dict[str, Any]]:
    return [
        {
            **skill,
            "enabled": False,
            "configured_at": None,
            "metadata": skill.get("metadata") or {},
            "updated_at": None,
        }
        for skill in SKILL_CATALOG_SEED
    ]


class SupabaseProductStore:
    """Persist Mercury Connect product state via Supabase PostgREST."""

    def __init__(self, settings: Settings):
        require_supabase(settings)
        self.settings = settings
        self.base_url = f"{settings.supabase_url}/rest/v1"
        self.headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        extra_headers = kwargs.pop("headers", {})
        headers = {**self.headers, **extra_headers}
        response = httpx.request(method, url, headers=headers, timeout=60, **kwargs)
        if response.status_code >= 300:
            raise RuntimeError(
                f"Supabase product request failed: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )
        if not response.text:
            return None
        return response.json()

    def _upsert_one(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        on_conflict: str,
    ) -> dict[str, Any]:
        rows = self._request(
            "POST",
            table,
            params={"on_conflict": on_conflict},
            headers={
                **self.headers,
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            json=[payload],
        )
        return rows[0]

    def _fallback_workspace_for_token(self, token_payload: dict[str, Any]) -> dict[str, Any]:
        key = workspace_key(
            str(token_payload.get("company") or token_payload.get("sub") or "workspace")
        )
        workspace = {
            "id": stable_id("workspace", key),
            "workspace_key": key,
            "name": str(token_payload.get("company") or "Mercury Workspace"),
            "plan": "invite-preview",
            "status": "active",
            "metadata": {"storage": "audit_fallback"},
            "created_at": utc_from_epoch(int(token_payload.get("iat") or 0)),
            "updated_at": now_utc(),
        }
        member = {
            "id": stable_id("member", key, token_payload.get("sub")),
            "email": str(token_payload.get("sub") or ""),
            "role": "owner",
            "host_app": normalize_host_app(str(token_payload.get("host_app") or "generic")),
            "status": "active",
            "created_at": utc_from_epoch(int(token_payload.get("iat") or 0)),
            "last_seen_at": now_utc(),
        }
        token = {
            "id": stable_id("client", token_payload.get("jti")),
            "status": "active",
            "workspace_id": workspace["id"],
            "member_id": member["id"],
            "host_app": member["host_app"],
            "expires_at": utc_from_epoch(int(token_payload.get("exp") or 0)),
        }
        return {"token": token, "workspace": workspace, "member": member}

    def _fallback_upsert_connection(
        self,
        request: ConnectRequest,
        token_payload: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._fallback_workspace_for_token(token_payload)
        key = context["workspace"]["workspace_key"]
        self._fallback_record_state_event(
            workspace_key=key,
            client_jti=str(token_payload.get("jti") or ""),
            event_type="connect.token_issued",
            input_payload={"company": request.company, "host_app": request.host_app},
            summary={
                "workspace": {
                    "id": context["workspace"]["id"],
                    "workspace_key": key,
                    "name": request.company.strip(),
                    "plan": context["workspace"]["plan"],
                    "status": "active",
                },
                "member": {
                    "id": context["member"]["id"],
                    "role": context["member"]["role"],
                    "host_app": context["member"]["host_app"],
                    "status": "active",
                },
                "client": {
                    "id": context["token"]["id"],
                    "host_app": context["token"]["host_app"],
                    "expires_at": context["token"]["expires_at"],
                },
                "event_summary": {"host_app": context["member"]["host_app"]},
            },
        )
        return context

    def _fallback_state_events(self, workspace_key_value: str) -> list[dict[str, Any]]:
        rows = self._request(
            "GET",
            "mcp_audit_events",
            params={
                "tool_name": f"eq.{PRODUCT_STATE_TOOL}",
                "select": "id,created_at,tool_name,output_summary,status,metadata",
                "order": "created_at.asc",
                "limit": str(PRODUCT_FALLBACK_LIMIT),
            },
        )
        return [
            row
            for row in rows or []
            if (row.get("metadata") or {}).get("workspace_key") == workspace_key_value
        ]

    def _fallback_dashboard(self, token_payload: dict[str, Any]) -> dict[str, Any]:
        context = self._fallback_workspace_for_token(token_payload)
        key = context["workspace"]["workspace_key"]
        skills = {skill["skill_id"]: skill for skill in skill_catalog_rows()}
        connector_profiles: dict[str, dict[str, Any]] = {}
        members: dict[str, dict[str, Any]] = {
            context["member"]["id"]: context["member"],
        }
        flows: dict[str, dict[str, Any]] = {}
        flow_runs: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []

        for row in self._fallback_state_events(key):
            summary = row.get("output_summary") or {}
            event_type = str(summary.get("event_type") or "")
            if event_type == "connect.token_issued":
                saved_workspace = summary.get("workspace") or {}
                if saved_workspace.get("name"):
                    context["workspace"] = {
                        **context["workspace"],
                        **{
                            field: saved_workspace[field]
                            for field in ("id", "workspace_key", "name", "plan", "status")
                            if field in saved_workspace
                        },
                    }
            elif event_type == "connector.profile_configured":
                profile = summary.get("profile") or {}
                profile_key = f"{profile.get('connector_id')}:{profile.get('environment')}"
                connector_profiles[profile_key] = public_connector_profile(profile)
            elif event_type in {"skill.enabled", "skill.disabled"}:
                skill_id = str(summary.get("skill_id") or "")
                if skill_id in skills:
                    skills[skill_id]["enabled"] = bool(summary.get("enabled"))
                    skills[skill_id]["configured_at"] = row.get("created_at")
            elif event_type == "skill.uploaded":
                skill = summary.get("skill") or {}
                if skill.get("skill_id"):
                    skills[str(skill["skill_id"])] = {
                        **skill,
                        "enabled": True,
                        "configured_at": row.get("created_at"),
                    }
            elif event_type == "team.member_invited":
                member = summary.get("member") or {}
                if member.get("id"):
                    members[str(member["id"])] = member
            elif event_type == "flow.saved":
                flow = summary.get("flow") or {}
                if flow.get("flow_id"):
                    flows[str(flow["flow_id"])] = flow
            elif event_type == "flow.run_completed":
                run = summary.get("flow_run") or {}
                if run.get("run_id"):
                    flow_runs.append(run)

            events.append(
                {
                    "id": row.get("id"),
                    "created_at": row.get("created_at"),
                    "event_type": event_type,
                    "summary": summary.get("event_summary") or {},
                    "status": row.get("status"),
                    "metadata": {"storage": "audit_fallback"},
                }
            )

        return {
            "status": "ok",
            "storage": "audit_fallback",
            **context,
            "connectors": list_connector_summaries(),
            "connector_profiles": list(connector_profiles.values()),
            "members": list(members.values()),
            "skills": sorted(skills.values(), key=lambda item: (item["category"], item["title"])),
            "flows": sorted(
                flows.values(),
                key=lambda item: str(item.get("updated_at") or ""),
                reverse=True,
            ),
            "flow_runs": sorted(
                flow_runs,
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )[:12],
            "events": [public_product_event(event) for event in reversed(events[-12:])],
        }

    def _fallback_flows_for_workspace_key(self, workspace_key_value: str) -> list[dict[str, Any]]:
        flows: dict[str, dict[str, Any]] = {}
        for row in self._fallback_state_events(workspace_key_value):
            summary = row.get("output_summary") or {}
            if str(summary.get("event_type") or "") != "flow.saved":
                continue
            flow = summary.get("flow") or {}
            if flow.get("flow_id"):
                flows[str(flow["flow_id"])] = flow
        return sorted(
            flows.values(),
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )

    def get_flow(self, *, token_payload: dict[str, Any], flow_id: str) -> dict[str, Any] | None:
        context = self._fallback_workspace_for_token(token_payload)
        for flow in self._fallback_flows_for_workspace_key(context["workspace"]["workspace_key"]):
            if flow.get("flow_id") == flow_id:
                return flow
        return None

    def save_flow(
        self,
        *,
        token_payload: dict[str, Any],
        title: str | None,
        flow_yaml: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._fallback_workspace_for_token(token_payload)
        flow = flow_summary_from_yaml(flow_yaml, title=title, metadata=metadata)
        self._fallback_record_state_event(
            workspace_key=context["workspace"]["workspace_key"],
            client_jti=str(token_payload.get("jti") or ""),
            event_type="flow.saved",
            input_payload={
                "flow_id": flow["flow_id"],
                "title": flow["title"],
                "sha256": flow["sha256"],
            },
            summary={
                "flow": flow,
                "event_summary": {
                    "flow_id": flow["flow_id"],
                    "title": flow["title"],
                    "status": flow["status"],
                },
            },
        )
        return flow

    def record_flow_run(
        self,
        *,
        token_payload: dict[str, Any],
        flow_id: str | None,
        title: str | None,
        result_payload: dict[str, Any],
        dry_run: bool,
        env_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        context = self.workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        run = flow_run_summary(
            flow_id=flow_id,
            title=title,
            result_payload=result_payload,
            dry_run=dry_run,
            env_keys=env_keys,
        )
        self.record_event(
            workspace_id=context["workspace"]["id"],
            member_id=(context.get("member") or {}).get("id"),
            event_type="flow.run_completed",
            input_payload={
                "flow_id": flow_id,
                "title": title,
                "dry_run": dry_run,
                "status": result_payload.get("status"),
                "env_keys": run["env_keys"],
            },
            summary={
                "flow_run": run,
                "event_summary": {
                    "run_id": run["run_id"],
                    "flow_id": run.get("flow_id"),
                    "title": run["title"],
                    "status": run["status"],
                    "dry_run": dry_run,
                },
            },
            status=str(result_payload.get("status") or "ok"),
            metadata={
                "workspace_key": context["workspace"]["workspace_key"],
                "client_jti": str(token_payload.get("jti") or ""),
            },
        )
        return run

    def _fallback_set_skill_enabled(
        self,
        *,
        token_payload: dict[str, Any],
        skill_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        context = self._fallback_workspace_for_token(token_payload)
        skill = next((item for item in skill_catalog_rows() if item["skill_id"] == skill_id), None)
        if not skill and not skill_id.startswith("workspace-"):
            raise ValueError(f"Unknown skill: {skill_id}")
        row = {
            "id": stable_id("workspace_skill", context["workspace"]["workspace_key"], skill_id),
            "workspace_id": context["workspace"]["id"],
            "skill_id": skill_id,
            "enabled": enabled,
            "configured_by_member_id": context["member"]["id"],
            "configured_at": now_utc(),
            "storage": "audit_fallback",
        }
        self._fallback_record_state_event(
            workspace_key=context["workspace"]["workspace_key"],
            client_jti=str(token_payload.get("jti") or ""),
            event_type="skill.enabled" if enabled else "skill.disabled",
            input_payload={"skill_id": skill_id},
            summary={
                "skill_id": skill_id,
                "enabled": enabled,
                "event_summary": {"skill_id": skill_id, "enabled": enabled},
            },
        )
        return row

    def _fallback_invite_member(
        self,
        *,
        token_payload: dict[str, Any],
        email: str,
        role: str = "member",
    ) -> dict[str, Any]:
        context = self._fallback_workspace_for_token(token_payload)
        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise ValueError("Valid member email is required.")
        member_role = role if role in {"owner", "admin", "member", "viewer"} else "member"
        member = {
            "id": stable_id(
                "member",
                context["workspace"]["workspace_key"],
                email_hash(normalized_email),
            ),
            "workspace_id": context["workspace"]["id"],
            "email_hash": email_hash(normalized_email),
            "email_domain": email_domain(normalized_email),
            "role": member_role,
            "host_app": "pending",
            "status": "invited",
            "created_at": now_utc(),
            "last_seen_at": None,
            "storage": "audit_fallback",
        }
        self._fallback_record_state_event(
            workspace_key=context["workspace"]["workspace_key"],
            client_jti=str(token_payload.get("jti") or ""),
            event_type="team.member_invited",
            input_payload={"member_email": normalized_email, "role": member_role},
            summary={
                "member": member,
                "event_summary": {
                    "member_email_hash": member["email_hash"],
                    "email_domain": member["email_domain"],
                    "role": member_role,
                    "status": "invited",
                },
            },
        )
        return member

    def _fallback_set_connector_profile(
        self,
        *,
        token_payload: dict[str, Any],
        connector_id: str,
        environment: str,
        company_name: str | None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._fallback_workspace_for_token(token_payload)
        connector = connector_by_id(connector_id)
        if not connector:
            raise ValueError(f"Unknown connector: {connector_id}")
        canonical_connector_id = connector.connector_id
        if environment not in connector.environments:
            raise ValueError(
                f"Unsupported environment for {canonical_connector_id}: {environment}"
            )
        profile_id = stable_id(
            "connector",
            context["workspace"]["workspace_key"],
            canonical_connector_id,
            environment,
        )
        existing_profile: dict[str, Any] | None = None
        for row in self._fallback_state_events(context["workspace"]["workspace_key"]):
            summary = row.get("output_summary") or {}
            if summary.get("event_type") != "connector.profile_configured":
                continue
            profile = summary.get("profile") or {}
            if (
                profile.get("connector_id") == canonical_connector_id
                and profile.get("environment") == environment
            ):
                existing_profile = profile
        existing_metadata = _public_connector_metadata(
            (existing_profile or {}).get("metadata")
        )
        merged_metadata = {
            **existing_metadata,
            "required_secret_fields": connector.required_secret_fields,
            "preset": connector.preset_for_environment(environment),
            **_public_connector_metadata(metadata),
        }
        profile = {
            "id": profile_id,
            "workspace_id": context["workspace"]["id"],
            "connector_id": canonical_connector_id,
            "environment": environment,
            "display_name": display_name or connector.name,
            "company_name": (
                company_name
                if company_name is not None
                else (existing_profile or {}).get("company_name")
            ),
            "status": connector_profile_status_from_metadata(merged_metadata),
            "metadata": merged_metadata,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        public_profile = public_connector_profile(profile)
        self._fallback_record_state_event(
            workspace_key=context["workspace"]["workspace_key"],
            client_jti=str(token_payload.get("jti") or ""),
            event_type="connector.profile_configured",
            input_payload={
                "connector_id": canonical_connector_id,
                "environment": environment,
            },
            summary={
                "profile": public_profile,
                "event_summary": {
                    "connector_id": canonical_connector_id,
                    "environment": environment,
                    "status": profile["status"],
                },
            },
        )
        return public_connector_profile(profile)

    def start_connector_setup(
        self,
        *,
        token_payload: dict[str, Any],
        connector_id: str,
        environment: str,
        company_name: str | None = None,
    ) -> dict[str, Any]:
        manifest = connector_by_id(connector_id)
        if not manifest:
            raise ValueError(f"Unknown connector: {connector_id}")
        if environment not in manifest.environments:
            raise ValueError(f"Unsupported environment for {connector_id}: {environment}")
        setup_state = resolve_setup_state(
            has_program=True,
            has_environment=bool(environment),
            missing_fields=required_missing_fields(manifest, {}),
        )
        return self.set_connector_profile(
            token_payload=token_payload,
            connector_id=manifest.connector_id,
            environment=environment,
            company_name=company_name,
            metadata={
                "setup_state": setup_state,
                "required_secret_fields": manifest.required_secret_fields,
                "preset": manifest.preset_for_environment(environment),
                "capabilities": manifest.capabilities,
            },
        )

    def _fallback_record_uploaded_skill(
        self,
        *,
        token_payload: dict[str, Any],
        skill_id: str,
        title: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._fallback_workspace_for_token(token_payload)
        skill = {
            "skill_id": skill_id,
            "title": title,
            "category": str(metadata.get("category") or "custom"),
            "summary": str(metadata.get("summary") or "Uploaded workspace skill."),
            "status": "uploaded",
            "version": "0.1.0",
            "required_connectors": metadata.get("required_connectors") or [],
            "tags": metadata.get("tags") or ["uploaded"],
            "metadata": {
                "source": "workspace_upload",
                "workspace_id": context["workspace"]["id"],
                "document_uri": metadata.get("document_uri"),
                "storage": "audit_fallback",
            },
            "updated_at": now_utc(),
        }
        upload = {
            "id": stable_id("upload", context["workspace"]["workspace_key"], skill_id),
            "workspace_id": context["workspace"]["id"],
            "member_id": context["member"]["id"],
            "skill_id": skill_id,
            "title": title,
            "status": "draft",
            "metadata": metadata,
            "created_at": now_utc(),
            "storage": "audit_fallback",
        }
        self._fallback_record_state_event(
            workspace_key=context["workspace"]["workspace_key"],
            client_jti=str(token_payload.get("jti") or ""),
            event_type="skill.uploaded",
            input_payload={"skill_id": skill_id, "title": title},
            summary={
                "skill": skill,
                "upload": upload,
                "event_summary": {"skill_id": skill_id, "status": "draft"},
            },
        )
        self._fallback_set_skill_enabled(
            token_payload=token_payload,
            skill_id=skill_id,
            enabled=True,
        )
        return {"catalog": skill, "upload": upload}

    def _fallback_record_state_event(
        self,
        *,
        workspace_key: str,
        client_jti: str,
        event_type: str,
        input_payload: dict[str, Any],
        summary: dict[str, Any],
        status: str = "ok",
    ) -> dict[str, Any]:
        sanitized_input = redact_json(input_payload)
        sanitized_summary = redact_json(
            {
                "event_type": event_type,
                "workspace_key": workspace_key,
                **summary,
            }
        )
        rows = self._request(
            "POST",
            "mcp_audit_events",
            headers={**self.headers, "Prefer": "return=representation"},
            json=[
                {
                    "tool_name": PRODUCT_STATE_TOOL,
                    "input_hash": hashlib.sha256(
                        json.dumps(sanitized_input, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "output_summary": sanitized_summary,
                    "status": status,
                    "metadata": {
                        "product_layer": True,
                        "storage": "audit_fallback",
                        "workspace_key": workspace_key,
                        "client_jti": client_jti,
                    },
                }
            ],
        )
        return rows[0]

    def seed_skill_catalog(self) -> int:
        try:
            rows = self._request(
                "POST",
                "mercury_skill_catalog",
                params={"on_conflict": "skill_id"},
                headers={
                    **self.headers,
                    "Prefer": "resolution=merge-duplicates,return=representation",
                },
                json=SKILL_CATALOG_SEED,
            )
            return len(rows or [])
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return len(SKILL_CATALOG_SEED)
            raise

    def create_public_workspace(self, company_name: str | None = None) -> dict[str, Any]:
        workspace_id = new_public_workspace_id()
        request = public_workspace_connect_request(workspace_id, company_name)
        token_payload = public_workspace_token_payload(workspace_id)
        persisted = self.upsert_connection(request, token_payload)
        self.seed_skill_catalog()
        workspace = {
            **persisted["workspace"],
            "name": request.company,
        }
        return {
            "status": "ok",
            "public_mode": True,
            "workspace_id": workspace_id,
            "workspace": workspace,
        }

    def public_dashboard(self, workspace_id: str) -> dict[str, Any]:
        payload = self.dashboard(public_workspace_token_payload(workspace_id))
        if payload.get("status") == "unregistered":
            return {
                "status": "not_found",
                "public_mode": True,
                "workspace_id": workspace_id,
            }
        return {
            **payload,
            "public_mode": True,
            "workspace_id": workspace_id,
        }

    def upsert_connection(
        self,
        request: ConnectRequest,
        token_payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._upsert_connection_product_tables(request, token_payload)
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return self._fallback_upsert_connection(request, token_payload)
            raise

    def _upsert_connection_product_tables(
        self,
        request: ConnectRequest,
        token_payload: dict[str, Any],
    ) -> dict[str, Any]:
        workspace = self._upsert_one(
            "mercury_workspaces",
            {
                "workspace_key": workspace_key(request.company),
                "name": request.company.strip(),
                "plan": "invite-preview",
                "status": "active",
                "metadata": {"source": "mercury-connect"},
            },
            on_conflict="workspace_key",
        )
        member = self._upsert_one(
            "mercury_workspace_members",
            {
                "workspace_id": workspace["id"],
                "email": request.email.strip().lower(),
                "role": "owner",
                "host_app": normalize_host_app(request.host_app),
                "status": "active",
            },
            on_conflict="workspace_id,email",
        )
        token = self._upsert_one(
            "mercury_client_tokens",
            {
                "workspace_id": workspace["id"],
                "member_id": member["id"],
                "token_jti": token_payload["jti"],
                "subject_email": token_payload["sub"],
                "host_app": token_payload.get("host_app"),
                "scopes": token_payload.get("scope") or [],
                "issued_at": utc_from_epoch(int(token_payload["iat"])),
                "expires_at": utc_from_epoch(int(token_payload["exp"])),
                "status": "active",
            },
            on_conflict="token_jti",
        )
        self.record_event(
            workspace_id=workspace["id"],
            member_id=member["id"],
            event_type="connect.token_issued",
            input_payload={"email": request.email, "company": request.company},
            summary={"host_app": normalize_host_app(request.host_app), "token_id": token["id"]},
        )
        return {"workspace": workspace, "member": member, "token": token}

    def workspace_for_token(self, token_payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self._workspace_for_token_product_tables(token_payload)
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return self._fallback_workspace_for_token(token_payload)
            raise

    def _workspace_for_token_product_tables(
        self,
        token_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "mercury_client_tokens",
            params={
                "token_jti": f"eq.{token_payload.get('jti')}",
                "select": "id,status,workspace_id,member_id,host_app,expires_at",
                "limit": "1",
            },
        )
        if not rows:
            return None
        token = rows[0]
        workspace = self._request(
            "GET",
            "mercury_workspaces",
            params={
                "id": f"eq.{token['workspace_id']}",
                "select": "id,workspace_key,name,plan,status,metadata,created_at,updated_at",
                "limit": "1",
            },
        )[0]
        member = self._request(
            "GET",
            "mercury_workspace_members",
            params={
                "id": f"eq.{token['member_id']}",
                "select": "id,email,role,host_app,status,created_at,last_seen_at",
                "limit": "1",
            },
        )[0]
        return {"token": token, "workspace": workspace, "member": member}

    def dashboard(self, token_payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._dashboard_product_tables(token_payload)
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return self._fallback_dashboard(token_payload)
            raise

    def _dashboard_product_tables(self, token_payload: dict[str, Any]) -> dict[str, Any]:
        context = self.workspace_for_token(token_payload)
        if not context:
            return {
                "status": "unregistered",
                "workspace": {
                    "name": token_payload.get("company"),
                    "host_app": token_payload.get("host_app"),
                },
                "member": {"email": token_payload.get("sub")},
                "skills": [],
                "flows": [],
                "connectors": list_connector_summaries(),
                "connector_profiles": [],
                "events": [],
            }

        workspace_id = context["workspace"]["id"]
        member_id = context["member"]["id"]
        flows = self._fallback_flows_for_workspace_key(context["workspace"]["workspace_key"])
        self._request(
            "PATCH",
            "mercury_workspace_members",
            params={"id": f"eq.{member_id}"},
            json={"last_seen_at": datetime.now(tz=UTC).isoformat()},
        )
        catalog = self._request(
            "GET",
            "mercury_skill_catalog",
            params={
                "select": (
                    "skill_id,title,category,summary,status,version,"
                    "required_connectors,tags,metadata,updated_at"
                ),
                "order": "category.asc,title.asc",
            },
        )
        enabled_rows = self._request(
            "GET",
            "mercury_workspace_skills",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": "skill_id,enabled,configured_at",
            },
        )
        enabled = {row["skill_id"]: row for row in enabled_rows or []}
        connector_profiles = self._request(
            "GET",
            "mercury_connector_profiles",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": (
                    "id,connector_id,environment,display_name,status,company_name,"
                    "metadata,created_at,updated_at"
                ),
                "order": "updated_at.desc",
            },
        )
        members = self._request(
            "GET",
            "mercury_workspace_members",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": "id,email,role,host_app,status,created_at,last_seen_at",
                "order": "created_at.asc",
            },
        )
        events = self._request(
            "GET",
            "mercury_product_events",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": "id,created_at,event_type,summary,status,metadata",
                "order": "created_at.desc",
                "limit": "12",
            },
        )
        flow_run_events = self._request(
            "GET",
            "mercury_product_events",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "event_type": "eq.flow.run_completed",
                "select": "id,created_at,event_type,summary,status,metadata",
                "order": "created_at.desc",
                "limit": "12",
            },
        )
        return {
            "status": "ok",
            **context,
            "connectors": list_connector_summaries(),
            "connector_profiles": public_connector_profiles(connector_profiles or []),
            "members": members or [],
            "skills": [
                {
                    **skill,
                    "enabled": bool(enabled.get(skill["skill_id"], {}).get("enabled")),
                    "configured_at": enabled.get(skill["skill_id"], {}).get("configured_at"),
                }
                for skill in catalog or []
            ],
            "flows": flows,
            "flow_runs": [
                {
                    **((row.get("summary") or {}).get("flow_run") or {}),
                    "event_id": row.get("id"),
                    "event_status": row.get("status"),
                }
                for row in flow_run_events or []
                if ((row.get("summary") or {}).get("flow_run") or {}).get("run_id")
            ],
            "events": [
                public_product_event(event)
                for event in events or []
                if isinstance(event, dict)
            ],
        }

    def set_skill_enabled(
        self,
        *,
        token_payload: dict[str, Any],
        skill_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        try:
            return self._set_skill_enabled_product_tables(
                token_payload=token_payload,
                skill_id=skill_id,
                enabled=enabled,
            )
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return self._fallback_set_skill_enabled(
                    token_payload=token_payload,
                    skill_id=skill_id,
                    enabled=enabled,
                )
            raise

    def _set_skill_enabled_product_tables(
        self,
        *,
        token_payload: dict[str, Any],
        skill_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        context = self.workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        row = self._upsert_one(
            "mercury_workspace_skills",
            {
                "workspace_id": context["workspace"]["id"],
                "skill_id": skill_id,
                "enabled": enabled,
                "configured_by_member_id": context["member"]["id"],
                "configured_at": datetime.now(tz=UTC).isoformat(),
            },
            on_conflict="workspace_id,skill_id",
        )
        self.record_event(
            workspace_id=context["workspace"]["id"],
            member_id=context["member"]["id"],
            event_type="skill.enabled" if enabled else "skill.disabled",
            input_payload={"skill_id": skill_id},
            summary={"skill_id": skill_id, "enabled": enabled},
        )
        return row

    def invite_member(
        self,
        *,
        token_payload: dict[str, Any],
        email: str,
        role: str = "member",
    ) -> dict[str, Any]:
        try:
            return self._invite_member_product_tables(
                token_payload=token_payload,
                email=email,
                role=role,
            )
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return self._fallback_invite_member(
                    token_payload=token_payload,
                    email=email,
                    role=role,
                )
            raise

    def _invite_member_product_tables(
        self,
        *,
        token_payload: dict[str, Any],
        email: str,
        role: str = "member",
    ) -> dict[str, Any]:
        context = self.workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise ValueError("Valid member email is required.")
        member_role = role if role in {"owner", "admin", "member", "viewer"} else "member"
        row = self._upsert_one(
            "mercury_workspace_members",
            {
                "workspace_id": context["workspace"]["id"],
                "email": normalized_email,
                "role": member_role,
                "host_app": "pending",
                "status": "invited",
            },
            on_conflict="workspace_id,email",
        )
        self.record_event(
            workspace_id=context["workspace"]["id"],
            member_id=context["member"]["id"],
            event_type="team.member_invited",
            input_payload={"member_email": normalized_email, "role": member_role},
            summary={
                "member_id": row["id"],
                "member_email_hash": email_hash(normalized_email),
                "email_domain": email_domain(normalized_email),
                "role": member_role,
                "status": "invited",
            },
        )
        return row

    def set_connector_profile(
        self,
        *,
        token_payload: dict[str, Any],
        connector_id: str,
        environment: str,
        company_name: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        connector = connector_by_id(connector_id)
        if not connector:
            raise ValueError(f"Unknown connector: {connector_id}")
        canonical_connector_id = connector.connector_id
        try:
            return self._set_connector_profile_product_tables(
                token_payload=token_payload,
                connector_id=canonical_connector_id,
                environment=environment,
                company_name=company_name,
                display_name=display_name,
                metadata=metadata,
            )
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return self._fallback_set_connector_profile(
                    token_payload=token_payload,
                    connector_id=canonical_connector_id,
                    environment=environment,
                    company_name=company_name,
                    display_name=display_name,
                    metadata=metadata,
                )
            raise

    def _set_connector_profile_product_tables(
        self,
        *,
        token_payload: dict[str, Any],
        connector_id: str,
        environment: str,
        company_name: str | None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self.workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        connector = connector_by_id(connector_id)
        if not connector:
            raise ValueError(f"Unknown connector: {connector_id}")
        canonical_connector_id = connector.connector_id
        if environment not in connector.environments:
            raise ValueError(
                f"Unsupported environment for {canonical_connector_id}: {environment}"
            )
        existing_rows = self._request(
            "GET",
            "mercury_connector_profiles",
            params={
                "workspace_id": f"eq.{context['workspace']['id']}",
                "connector_id": f"eq.{canonical_connector_id}",
                "environment": f"eq.{environment}",
                "select": "id,metadata,display_name,company_name",
                "limit": "1",
            },
        )
        existing_profile = (existing_rows or [None])[0]
        existing_metadata = _public_connector_metadata(
            (existing_profile or {}).get("metadata")
        )
        merged_metadata = {
            **existing_metadata,
            "required_secret_fields": connector.required_secret_fields,
            "preset": connector.preset_for_environment(environment),
            **_public_connector_metadata(metadata),
        }
        payload = {
            "workspace_id": context["workspace"]["id"],
            "connector_id": canonical_connector_id,
            "environment": environment,
            "display_name": (
                display_name
                if display_name is not None
                else (existing_profile or {}).get("display_name") or connector.name
            ),
            "status": connector_profile_status_from_metadata(merged_metadata),
            "metadata": merged_metadata,
        }
        if company_name is not None:
            payload["company_name"] = company_name
        row = self._upsert_one(
            "mercury_connector_profiles",
            payload,
            on_conflict="workspace_id,connector_id,environment",
        )
        self.record_event(
            workspace_id=context["workspace"]["id"],
            member_id=context["member"]["id"],
            event_type="connector.profile_configured",
            input_payload={
                "connector_id": canonical_connector_id,
                "environment": environment,
            },
            summary={
                "connector_id": canonical_connector_id,
                "environment": environment,
                "status": row["status"],
            },
        )
        return public_connector_profile(dict(row))

    def record_uploaded_skill(
        self,
        *,
        token_payload: dict[str, Any],
        skill_id: str,
        title: str,
        markdown: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._record_uploaded_skill_product_tables(
                token_payload=token_payload,
                skill_id=skill_id,
                title=title,
                markdown=markdown,
                metadata=metadata,
            )
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                return self._fallback_record_uploaded_skill(
                    token_payload=token_payload,
                    skill_id=skill_id,
                    title=title,
                    metadata=metadata,
                )
            raise

    def _record_uploaded_skill_product_tables(
        self,
        *,
        token_payload: dict[str, Any],
        skill_id: str,
        title: str,
        markdown: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        context = self.workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        catalog = self._upsert_one(
            "mercury_skill_catalog",
            {
                "skill_id": skill_id,
                "title": title,
                "category": str(metadata.get("category") or "custom"),
                "summary": str(metadata.get("summary") or "Uploaded workspace skill."),
                "status": "uploaded",
                "version": "0.1.0",
                "required_connectors": metadata.get("required_connectors") or [],
                "tags": metadata.get("tags") or ["uploaded"],
                "metadata": {
                    "source": "workspace_upload",
                    "workspace_id": context["workspace"]["id"],
                    "document_uri": metadata.get("document_uri"),
                },
            },
            on_conflict="skill_id",
        )
        rows = self._request(
            "POST",
            "mercury_skill_uploads",
            headers={**self.headers, "Prefer": "return=representation"},
            json=[
                {
                    "workspace_id": context["workspace"]["id"],
                    "member_id": context["member"]["id"],
                    "skill_id": skill_id,
                    "title": title,
                    "markdown": markdown,
                    "status": "draft",
                    "metadata": metadata,
                }
            ],
        )
        self.set_skill_enabled(
            token_payload=token_payload,
            skill_id=skill_id,
            enabled=True,
        )
        self.record_event(
            workspace_id=context["workspace"]["id"],
            member_id=context["member"]["id"],
            event_type="skill.uploaded",
            input_payload={"skill_id": skill_id, "title": title},
            summary={"skill_id": skill_id, "status": "draft"},
        )
        return {"catalog": catalog, "upload": rows[0]}

    def record_event(
        self,
        *,
        workspace_id: str,
        member_id: str | None,
        event_type: str,
        input_payload: dict[str, Any],
        summary: dict[str, Any],
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._record_event_product_tables(
                workspace_id=workspace_id,
                member_id=member_id,
                event_type=event_type,
                input_payload=input_payload,
                summary=summary,
                status=status,
                metadata=metadata,
            )
        except RuntimeError as exc:
            if is_product_schema_error(exc):
                workspace_key_value = str((metadata or {}).get("workspace_key") or workspace_id)
                return self._fallback_record_state_event(
                    workspace_key=workspace_key_value,
                    client_jti=str((metadata or {}).get("client_jti") or ""),
                    event_type=event_type,
                    input_payload=input_payload,
                    summary=summary,
                    status=status,
                )
            raise

    def _record_event_product_tables(
        self,
        *,
        workspace_id: str,
        member_id: str | None,
        event_type: str,
        input_payload: dict[str, Any],
        summary: dict[str, Any],
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sanitized_input = redact_json(input_payload)
        sanitized_summary = redact_json(summary)
        rows = self._request(
            "POST",
            "mercury_product_events",
            headers={**self.headers, "Prefer": "return=representation"},
            json=[
                {
                    "workspace_id": workspace_id,
                    "member_id": member_id,
                    "event_type": event_type,
                    "input_hash": hashlib.sha256(
                        json.dumps(sanitized_input, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "summary": sanitized_summary,
                    "status": status,
                    "metadata": metadata or {},
                }
            ],
        )
        return rows[0]
