"""Supabase-backed product state for Mercury Connect."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from mercury_tools.config import Settings, require_supabase
from mercury_tools.product import ConnectRequest, normalize_host_app
from mercury_tools.safety.redaction import redact_json

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
        "summary": "ตรวจใบแจ้งหนี้/ใบกำกับภาษีแบบอ่านอย่างเดียวและทำรายการประเด็นให้ฝ่ายบัญชี",
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
]

CONNECTOR_CATALOG: list[dict[str, Any]] = [
    {
        "connector_id": "flowaccount",
        "name": "FlowAccount",
        "status": "available",
        "environments": ["production", "sandbox"],
        "required_secret_fields": ["client_id", "client_secret"],
        "preset": {
            "grant_type": "client_credentials",
            "scope": "flowaccount-api",
            "api_base_url": "https://openapi.flowaccount.com/v1",
            "token_url": "https://openapi.flowaccount.com/token",
        },
    },
    {
        "connector_id": "peak",
        "name": "PEAK Accounting",
        "status": "setup_target",
        "environments": ["production", "sandbox"],
        "required_secret_fields": ["client_id", "client_secret"],
        "preset": {},
    },
    {
        "connector_id": "express",
        "name": "Express Account",
        "status": "setup_target",
        "environments": ["local", "gateway"],
        "required_secret_fields": ["gateway_url", "api_key"],
        "preset": {},
    },
]


def slugify(value: str, *, fallback: str = "workspace") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def workspace_key(company: str) -> str:
    normalized = " ".join(company.strip().lower().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{slugify(normalized, fallback='company')}-{digest}"


def utc_from_epoch(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


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

    def seed_skill_catalog(self) -> int:
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

    def upsert_connection(
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
                "connectors": CONNECTOR_CATALOG,
                "connector_profiles": [],
                "events": [],
            }

        workspace_id = context["workspace"]["id"]
        member_id = context["member"]["id"]
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
        return {
            "status": "ok",
            **context,
            "connectors": CONNECTOR_CATALOG,
            "connector_profiles": connector_profiles or [],
            "skills": [
                {
                    **skill,
                    "enabled": bool(enabled.get(skill["skill_id"], {}).get("enabled")),
                    "configured_at": enabled.get(skill["skill_id"], {}).get("configured_at"),
                }
                for skill in catalog or []
            ],
            "events": events or [],
        }

    def set_skill_enabled(
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

    def set_connector_profile(
        self,
        *,
        token_payload: dict[str, Any],
        connector_id: str,
        environment: str,
        company_name: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self.workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        connector = next(
            (item for item in CONNECTOR_CATALOG if item["connector_id"] == connector_id),
            None,
        )
        if not connector:
            raise ValueError(f"Unknown connector: {connector_id}")
        if environment not in connector["environments"]:
            raise ValueError(f"Unsupported environment for {connector_id}: {environment}")
        merged_metadata = {
            "required_secret_fields": connector["required_secret_fields"],
            "preset": connector["preset"],
            "credential_storage": "host_or_user_vault",
            **(metadata or {}),
        }
        row = self._upsert_one(
            "mercury_connector_profiles",
            {
                "workspace_id": context["workspace"]["id"],
                "connector_id": connector_id,
                "environment": environment,
                "display_name": display_name or connector["name"],
                "company_name": company_name,
                "status": "requires_credentials",
                "metadata": merged_metadata,
            },
            on_conflict="workspace_id,connector_id,environment",
        )
        self.record_event(
            workspace_id=context["workspace"]["id"],
            member_id=context["member"]["id"],
            event_type="connector.profile_configured",
            input_payload={"connector_id": connector_id, "environment": environment},
            summary={
                "connector_id": connector_id,
                "environment": environment,
                "status": row["status"],
            },
        )
        return row

    def record_uploaded_skill(
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
