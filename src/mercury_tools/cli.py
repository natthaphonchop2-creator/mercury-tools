"""Command-line interface for Mercury Tools."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from mercury_tools.config import load_settings
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.flows.parser import FlowValidationError, validate_flow_path
from mercury_tools.flows.reports import write_html_report, write_junit_report
from mercury_tools.flows.runner import create_default_runner
from mercury_tools.flows.templates import FLOW_CHEAT_SHEET, TEMPLATES
from mercury_tools.flows.workspace import (
    create_workspace_scaffold,
    discover_workspace_flows,
    run_workspace_flows,
    workspace_manifest,
)
from mercury_tools.local.credential_cli import add_credential_parsers
from mercury_tools.qualification.flowaccount import (
    SandboxRunApproval,
    build_flowaccount_preflight_failure_report,
    create_flowaccount_qualification_runner,
)
from mercury_tools.qualification.models import QualificationRunState
from mercury_tools.qualification.peak import load_peak_contract_report
from mercury_tools.rag.embeddings import create_embedding_provider
from mercury_tools.rag.ingest import ingest_wiki
from mercury_tools.rag.models import SearchFilters, public_search_result_payload
from mercury_tools.rag.routing import apply_knowledge_routing
from mercury_tools.rag.service import RagService
from mercury_tools.remote import DEFAULT_RENDER_URL, DEFAULT_TOKEN_FILE, read_token, verify_remote


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _env_overrides(args: argparse.Namespace) -> dict[str, str]:
    pairs = getattr(args, "env", None) or []
    env: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise FlowValidationError(f"Environment override must be KEY=value: {pair}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise FlowValidationError(f"Environment override key is empty: {pair}")
        env[key] = value
    return env


def _embedder(args: argparse.Namespace):
    settings = load_settings()
    return create_embedding_provider(settings, provider=getattr(args, "embedding_provider", None))


def cmd_ingest_wiki(args: argparse.Namespace) -> int:
    settings = load_settings()
    stats = ingest_wiki(
        Path(args.path),
        store=SupabaseRagStore(settings),
        embedder=_embedder(args),
    )
    _print_json({"status": "ok", "ingest": stats.as_dict()})
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    settings = load_settings()
    service = RagService(store=SupabaseRagStore(settings), embedder=_embedder(args))
    raw_filters = {
        key: value
        for key, value in {
            "jurisdiction": args.jurisdiction,
            "connector": args.connector,
            "doc_type": args.doc_type,
            "review_status": args.review_status,
            "effective_date": args.effective_date,
            "action_id": args.action_id,
            "version_id": args.version_id,
            "environment": args.environment,
            "capability": args.capability,
            "accounting_use": args.accounting_use,
        }.items()
        if value is not None
    }
    applied_filters, inferred_connector, inferred_domain = apply_knowledge_routing(
        args.query,
        raw_filters,
    )
    filters = SearchFilters(
        jurisdiction=applied_filters.get("jurisdiction"),
        connector=applied_filters.get("connector"),
        doc_type=applied_filters.get("doc_type"),
        review_status=applied_filters.get("review_status"),
        effective_date=applied_filters.get("effective_date"),
        action_id=applied_filters.get("action_id"),
        version_id=applied_filters.get("version_id"),
        environment=applied_filters.get("environment"),
        capability=applied_filters.get("capability"),
        accounting_use=applied_filters.get("accounting_use"),
    )
    results = service.search(args.query, filters=filters, top_k=args.top_k, mode=args.mode)
    payload = [public_search_result_payload(result) for result in results]
    if args.json:
        _print_json(
            {
                "query": args.query,
                "applied_filters": applied_filters,
                "inferred_connector": inferred_connector,
                "inferred_domain": inferred_domain,
                "results": payload,
            }
        )
    else:
        for item in payload:
            print(f"- {item['source_title']} ({item['score']:.3f})")
            print(f"  {item['text'][:240].replace(chr(10), ' ')}")
            print(f"  citation: {item['citation']}")
    return 0


def cmd_catalog_qualify(args: argparse.Namespace) -> int:
    if (args.connector, args.environment, args.all) != (
        "flowaccount",
        "sandbox",
        True,
    ):
        _print_json(
            {
                "status": "error",
                "error": "qualification_scope_not_supported",
            }
        )
        return 2

    try:
        runner = create_flowaccount_qualification_runner(
            Path(args.repo_root),
            dry_run=bool(args.dry_run),
        )
    except Exception:
        report = build_flowaccount_preflight_failure_report()
    else:
        report = asyncio.run(
            runner.qualify_all(
                approval=SandboxRunApproval(
                    reads=True,
                    writes=bool(args.sandbox_writes),
                    dry_run=bool(args.dry_run),
                ),
                dry_run=bool(args.dry_run),
            )
        )
    _print_json(report.public_dict())
    return 0 if report.run_state is QualificationRunState.COMPLETED else 1


def cmd_catalog_validate(args: argparse.Namespace) -> int:
    if (args.connector, args.all) != ("peak", True):
        _print_json(
            {
                "status": "error",
                "error": "qualification_scope_not_supported",
            }
        )
        return 2

    try:
        report = load_peak_contract_report(Path(args.repo_root))
    except Exception:
        _print_json(
            {
                "status": "error",
                "error": "catalog_validation_failed",
            }
        )
        return 1
    _print_json(report.public_dict())
    return 0


def cmd_release_scan_secrets(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from mercury_tools.release.models import SecretScanPolicy, SecretScanRequest
    from mercury_tools.release.scanner import (
        ReleaseGateError,
        build_blocked_report,
        load_known_secret_digests,
        load_public_surface_manifest,
        load_secret_scan_allowlist,
        scan_public_release,
    )

    try:
        manifest = load_public_surface_manifest(Path(args.manifest))
        allowlist = load_secret_scan_allowlist(Path(args.allowlist))
        fingerprints = load_known_secret_digests(
            paths=tuple(Path(path) for path in args.known_fingerprint_file),
            interactive=bool(args.known_fingerprint_stdin),
            repo_root=Path(args.repo_root),
        )
        request = SecretScanRequest(
            repo=args.repo,
            artifacts=Path(args.artifacts),
            all_history=bool(args.all_history),
            hosted=bool(args.hosted),
            manifest=manifest,
            allowlist=allowlist,
            policy=SecretScanPolicy(
                scanner_versions=manifest.scanner_versions,
                known_secret_digests=fingerprints,
            ),
        )
    except ReleaseGateError as exc:
        report = build_blocked_report(str(exc))
    except ValidationError:
        report = build_blocked_report("scan_request_malformed")
    else:
        try:
            report = scan_public_release(request)
        except Exception:
            report = build_blocked_report("release_scan_failed")

    payload = report.public_dict()
    if args.output:
        output = Path(args.output)
        temporary = output.with_name(f".{output.name}.tmp")
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(output)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            report = build_blocked_report("report_write_failed")
            payload = report.public_dict()
    _print_json(payload)
    return 0 if report.passed else 1


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    from mercury_tools.mcp.server import serve

    settings = load_settings()
    require_auth = settings.http_require_auth
    if args.require_auth:
        require_auth = True
    if args.allow_unauthenticated:
        require_auth = False
    serve(
        transport=args.transport or settings.mcp_transport,
        host=args.host or settings.mcp_host,
        port=args.port or settings.mcp_port,
        require_auth=require_auth,
    )
    return 0


def cmd_mcp_serve_local(_args: argparse.Namespace) -> int:
    """Defer the Task 14 local runtime import until this command is executed."""

    try:
        from mercury_tools.mcp.local_server import serve_local
    except ModuleNotFoundError as exc:
        if exc.name != "mercury_tools.mcp.local_server":
            raise
        _print_json({"status": "error", "error": "local_runtime_unavailable"})
        return 1
    serve_local()
    return 0


def cmd_remote_verify(args: argparse.Namespace) -> int:
    token = read_token(token=args.token, token_file=args.token_file)
    result = verify_remote(
        base_url=args.url,
        mcp_path=args.mcp_path,
        token=token,
        timeout=args.timeout,
    )
    payload = result.as_dict()
    if args.json:
        _print_json(payload)
    else:
        print(f"Mercury Tools remote: {payload['base_url']}")
        print(f"healthz: HTTP {payload['health_status_code']} -> {payload['health'].get('status')}")
        print(f"supabase: {payload['health'].get('supabase')}")
        print(f"embedding: {payload['health'].get('embedding_provider')}")
        print(f"embedding configured: {payload['health'].get('embedding_configured')}")
        print(f"openai: {payload['health'].get('openai')}")
        print(f"mcp: {payload['mcp_url']}")
        print(f"auth configured: {payload['health'].get('http_auth_configured')}")
        print(f"auth check: {payload['authenticated_mcp_reachable']}")
        if payload["missing"]:
            print("missing:")
            for item in payload["missing"]:
                print(f"- {item}")
        if payload["errors"]:
            print("errors:")
            for item in payload["errors"]:
                print(f"- {item}")
        print(f"ready: {payload['ready']}")
    return 0 if result.ready else 1


def cmd_flow_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        payload = validate_flow_path(path)
    except FlowValidationError as exc:
        payload = {"status": "error", "message": str(exc), "path": str(path)}
        if args.json:
            _print_json(payload)
        else:
            print(f"Flow invalid: {exc}")
        return 1
    if args.json:
        _print_json(payload)
    else:
        flow = payload["flow"]
        print(f"Flow valid: {flow['name']}")
        print(f"commands: {flow['command_count']}")
        if flow["tags"]:
            print("tags: " + ", ".join(flow["tags"]))
    return 0


def cmd_flow_run(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        result = create_default_runner(dry_run=args.dry_run).run_path(
            path,
            env=_env_overrides(args),
        )
    except (FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc), "path": str(path)}
        if args.json:
            _print_json(payload)
        else:
            print(f"Flow failed: {exc}")
        return 1
    payload = result.as_dict()
    if args.json:
        _print_json(payload)
    else:
        print(f"Flow {payload['status']}: {payload['flow']['name']}")
        for step in payload["steps"]:
            saved = f" -> {step['saved_as']}" if step.get("saved_as") else ""
            print(f"- {step['index']}. {step['command']} [{step['status']}]{saved}")
        if payload["artifacts"]:
            print("artifacts:")
            for artifact in payload["artifacts"]:
                print(f"- {artifact.get('title', 'artifact')}")
    return 0


def _print_suite_payload(payload: dict[str, Any]) -> None:
    print(
        f"Flow suite {payload['status']}: "
        f"{payload['workspace']['selected_count']} selected / "
        f"{payload['workspace']['flow_count']} discovered"
    )
    for result in payload["results"]:
        print(f"- {result['flow']['name']}: {result['status']} ({len(result['steps'])} steps)")
    if payload.get("report_path"):
        print(f"report: {payload['report_path']}")


def cmd_flow_list(args: argparse.Namespace) -> int:
    try:
        workspace = discover_workspace_flows(
            Path(args.path),
            include_tags=args.tag,
            exclude_tags=args.exclude_tag,
        )
    except FlowValidationError as exc:
        payload = {"status": "error", "message": str(exc), "path": args.path}
        if args.json:
            _print_json(payload)
        else:
            print(f"Workspace invalid: {exc}")
        return 1
    payload = {"status": "ok", "workspace": workspace.as_dict()}
    if args.json:
        _print_json(payload)
    else:
        config = workspace.config
        source = config.config_path.name if config.config_path else "default discovery"
        print(f"Flow workspace: {config.root}")
        print(f"config: {source}")
        print(f"flows: {len(workspace.records)} discovered, {len(workspace.selected)} selected")
        for record in workspace.records:
            marker = "*" if record.selected else "-"
            tags = ", ".join(record.tags) if record.tags else "no tags"
            label = record.name or record.error or record.path.name
            relative_path = record.path.relative_to(config.root)
            print(f"{marker} {relative_path} [{record.status}] {label} ({tags})")
    return 0


def cmd_flow_manifest(args: argparse.Namespace) -> int:
    try:
        workspace = discover_workspace_flows(
            Path(args.path),
            include_tags=args.tag,
            exclude_tags=args.exclude_tag,
        )
    except FlowValidationError as exc:
        payload = {"status": "error", "message": str(exc), "path": args.path}
        if args.json:
            _print_json(payload)
        else:
            print(f"Workspace manifest failed: {exc}")
        return 1
    payload = workspace_manifest(workspace)
    if args.json:
        _print_json(payload)
    else:
        discovery = payload["discovery"]
        print(f"Mercury flow manifest: {payload['workspace']['root']}")
        print(f"runtime: {payload['runtime_boundary']['primary_runtime']}")
        print(
            f"flows: {discovery['selected_count']} selected / {discovery['flow_count']} discovered"
        )
        if discovery["tags"]:
            print("tags: " + ", ".join(discovery["tags"]))
        print("ordered:")
        for item in payload["execution"]["ordered_flow_paths"]:
            print(f"- {item}")
        print("next:")
        for item in payload["agent_handoff"]["cli_examples"]:
            print(f"- {item}")
    return 0


def cmd_flow_run_suite(args: argparse.Namespace) -> int:
    try:
        suite = run_workspace_flows(
            Path(args.path),
            dry_run=args.dry_run,
            include_tags=args.tag,
            exclude_tags=args.exclude_tag,
            env=_env_overrides(args),
        )
    except (FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc), "path": args.path}
        if args.json:
            _print_json(payload)
        else:
            print(f"Flow suite failed: {exc}")
        return 1
    payload = suite.as_dict()
    junit_path: Path | None = None
    html_path: Path | None = None
    if args.format == "junit":
        junit_path = Path(args.output or "report.xml").expanduser().resolve()
        write_junit_report(suite, junit_path)
        payload["junit_report_path"] = str(junit_path)
    elif args.format == "html":
        html_path = Path(args.output or "report.html").expanduser().resolve()
        write_html_report(suite, html_path)
        payload["html_report_path"] = str(html_path)
    if args.json:
        _print_json(payload)
    else:
        _print_suite_payload(payload)
        if junit_path:
            print(f"junit: {junit_path}")
        if html_path:
            print(f"html: {html_path}")
    if payload["status"] == "failed" and not args.allow_failures:
        return 1
    return 0


def _watch_snapshot(
    path: Path,
    *,
    include_tags: list[str],
    exclude_tags: list[str],
) -> tuple[tuple[str, int, int], ...]:
    workspace = discover_workspace_flows(
        path,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    paths = {record.path for record in workspace.records}
    if workspace.config.config_path:
        paths.add(workspace.config.config_path)
    if path.exists() and path.is_file():
        paths.add(path.expanduser().resolve())
    snapshot: list[tuple[str, int, int]] = []
    for item in sorted(paths):
        try:
            stat = item.stat()
        except FileNotFoundError:
            snapshot.append((str(item), -1, -1))
        else:
            snapshot.append((str(item), stat.st_mtime_ns, stat.st_size))
    return tuple(snapshot)


def cmd_flow_watch(args: argparse.Namespace) -> int:
    path = Path(args.path)
    runs = 0
    last_snapshot: tuple[tuple[str, int, int], ...] | None = None
    try:
        env = _env_overrides(args)
    except FlowValidationError as exc:
        if args.json:
            _print_json({"status": "error", "message": str(exc), "path": str(path)})
        else:
            print(f"Flow watch failed: {exc}")
        return 1
    print(f"Watching Mercury flows: {path}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            try:
                snapshot = _watch_snapshot(
                    path,
                    include_tags=args.tag,
                    exclude_tags=args.exclude_tag,
                )
                should_run = last_snapshot is None or snapshot != last_snapshot
                last_snapshot = snapshot
                if should_run:
                    runs += 1
                    print(f"\nRun {runs}:")
                    suite = run_workspace_flows(
                        path,
                        dry_run=args.dry_run,
                        include_tags=args.tag,
                        exclude_tags=args.exclude_tag,
                        env=env,
                    )
                    payload = suite.as_dict()
                    if args.json:
                        _print_json(payload)
                    else:
                        _print_suite_payload(payload)
            except (FlowValidationError, RuntimeError, ValueError) as exc:
                runs += 1
                error = {"status": "error", "message": str(exc), "path": str(path)}
                if args.json:
                    _print_json(error)
                else:
                    print(f"\nRun {runs}: Flow watch failed: {exc}")

            if args.max_runs is not None and runs >= args.max_runs:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped Mercury flow watch.")
        return 0


def _flow_import_payload(
    path: Path,
    *,
    include_tags: list[str],
    exclude_tags: list[str],
) -> dict[str, Any]:
    workspace = discover_workspace_flows(
        path,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    invalid = [record for record in workspace.selected if record.status == "invalid"]
    if invalid:
        first = invalid[0]
        raise FlowValidationError(f"Invalid workspace flow {first.path}: {first.error}")
    flows = []
    for record in workspace.selected:
        flows.append(
            {
                "title": record.name or record.path.stem,
                "flow_yaml": record.path.read_text(encoding="utf-8"),
                "metadata": {
                    "source": "cli-flow-push",
                    "relative_path": record.as_dict(root=workspace.config.root)["relative_path"],
                    "tags": record.tags,
                },
            }
        )
    return {
        "workspace": workspace.as_dict(),
        "flows": flows,
    }


def cmd_flow_push(args: argparse.Namespace) -> int:
    try:
        payload = _flow_import_payload(
            Path(args.path),
            include_tags=args.tag,
            exclude_tags=args.exclude_tag,
        )
        if not payload["flows"]:
            raise FlowValidationError("No selected flows to push.")
    except FlowValidationError as exc:
        error = {"status": "error", "message": str(exc), "path": args.path}
        if args.json:
            _print_json(error)
        else:
            print(f"Flow push failed: {exc}")
        return 1

    if args.dry_run:
        response_payload = {
            "status": "planned",
            "url": args.url.rstrip("/"),
            "flow_count": len(payload["flows"]),
            "workspace": payload["workspace"],
        }
        if args.json:
            _print_json(response_payload)
        else:
            print(
                f"Flow push planned: {response_payload['flow_count']} flows -> "
                f"{response_payload['url']}"
            )
        return 0

    client_token = read_token(token=args.client_token, token_file=args.client_token_file)
    if not client_token.startswith("mc_"):
        print("Flow push requires a Mercury client token (mc_...).")
        return 1

    url = f"{args.url.rstrip('/')}/api/flows/import"
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {client_token}"},
            json=payload,
            timeout=args.timeout,
        )
        response_payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        response_payload = {"status": "error", "message": str(exc), "url": url}
        if args.json:
            _print_json(response_payload)
        else:
            print(f"Flow push failed: {exc}")
        return 1

    if response.status_code >= 400:
        if args.json:
            _print_json(response_payload)
        else:
            print(f"Flow push failed: HTTP {response.status_code}")
            print(
                response_payload.get("message")
                or response_payload.get("error")
                or "Request failed."
            )
        return 1

    if args.json:
        _print_json(response_payload)
    else:
        print(
            f"Flow push ok: {response_payload.get('imported_count', 0)} flows -> "
            f"{args.url.rstrip('/')}"
        )
        for flow in response_payload.get("flows", []):
            print(f"- {flow.get('flow_id')}: {flow.get('title') or flow.get('name')}")
    return 0


def cmd_flow_init(args: argparse.Namespace) -> int:
    template = TEMPLATES[args.template]
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"Refusing to overwrite existing file: {path}")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    if args.json:
        _print_json({"status": "ok", "path": str(path), "template": args.template})
    else:
        print(f"Created Mercury flow: {path}")
    return 0


def cmd_flow_init_workspace(args: argparse.Namespace) -> int:
    try:
        payload = create_workspace_scaffold(
            Path(args.path),
            connector=args.connector,
            jurisdiction=args.jurisdiction,
            month=args.month,
            force=args.force,
        )
    except FlowValidationError as exc:
        error = {"status": "error", "message": str(exc), "path": args.path}
        if args.json:
            _print_json(error)
        else:
            print(f"Workspace init failed: {exc}")
        return 1
    if args.json:
        _print_json({"status": "ok", "workspace": payload})
    else:
        print(f"Created Mercury flow workspace: {payload['root']}")
        for item in payload["created"]:
            print(f"- created {item}")
        for item in payload["overwritten"]:
            print(f"- overwritten {item}")
        print("Next:")
        print(f"  mercury-tools flow list {payload['root']}")
        print(f"  mercury-tools flow run-suite {payload['root']} --dry-run")
    return 0


def cmd_flow_cheat_sheet(args: argparse.Namespace) -> int:
    if args.json:
        _print_json({"status": "ok", "cheat_sheet": FLOW_CHEAT_SHEET})
    else:
        print(FLOW_CHEAT_SHEET)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mercury-tools")
    sub = parser.add_subparsers(dest="command", required=True)

    add_credential_parsers(sub)

    catalog = sub.add_parser("catalog")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    qualify = catalog_sub.add_parser("qualify")
    qualify.add_argument("--connector", required=True)
    qualify.add_argument("--env", dest="environment", required=True)
    qualify.add_argument("--all", action="store_true")
    qualify.add_argument("--sandbox-writes", action="store_true")
    qualify.add_argument("--dry-run", action="store_true")
    qualify.add_argument("--repo-root", default=".")
    qualify.set_defaults(func=cmd_catalog_qualify)

    validate = catalog_sub.add_parser("validate")
    validate.add_argument("--connector", required=True)
    validate.add_argument("--all", action="store_true")
    validate.add_argument("--repo-root", default=".")
    validate.set_defaults(func=cmd_catalog_validate)

    release = sub.add_parser("release")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    scan_secrets = release_sub.add_parser("scan-secrets")
    scan_secrets.add_argument("--all-history", action="store_true")
    scan_secrets.add_argument("--hosted", action="store_true")
    scan_secrets.add_argument("--artifacts", required=True)
    scan_secrets.add_argument("--repo", required=True)
    scan_secrets.add_argument("--output")
    scan_secrets.add_argument(
        "--manifest",
        default="docs/release/public-surface-manifest.json",
    )
    scan_secrets.add_argument(
        "--allowlist",
        default="docs/release/secret-scan-allowlist.json",
    )
    scan_secrets.add_argument("--known-fingerprint-stdin", action="store_true")
    scan_secrets.add_argument("--known-fingerprint-file", action="append", default=[])
    scan_secrets.add_argument("--repo-root", default=".")
    scan_secrets.set_defaults(func=cmd_release_scan_secrets)

    ingest = sub.add_parser("ingest")
    ingest_sub = ingest.add_subparsers(dest="ingest_command", required=True)
    wiki = ingest_sub.add_parser("wiki")
    wiki.add_argument("--path", required=True)
    wiki.add_argument("--embedding-provider", choices=["openai", "hash"])
    wiki.set_defaults(func=cmd_ingest_wiki)

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--json", action="store_true")
    search.add_argument("--top-k", type=int, default=8)
    search.add_argument("--mode", choices=["hybrid", "keyword", "vector"], default="hybrid")
    search.add_argument("--jurisdiction")
    search.add_argument("--connector")
    search.add_argument("--doc-type")
    search.add_argument("--review-status")
    search.add_argument("--effective-date")
    search.add_argument("--action-id")
    search.add_argument("--version-id")
    search.add_argument("--environment")
    search.add_argument("--capability")
    search.add_argument("--accounting-use")
    search.add_argument("--embedding-provider", choices=["openai", "hash"])
    search.set_defaults(func=cmd_search)

    mcp = sub.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    serve = mcp_sub.add_parser("serve")
    serve.add_argument("--transport", choices=["stdio", "http", "streamable-http"])
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--require-auth", action="store_true")
    serve.add_argument("--allow-unauthenticated", action="store_true")
    serve.set_defaults(func=cmd_mcp_serve)
    serve_local = mcp_sub.add_parser("serve-local")
    serve_local.set_defaults(func=cmd_mcp_serve_local)

    remote = sub.add_parser("remote")
    remote_sub = remote.add_subparsers(dest="remote_command", required=True)
    verify = remote_sub.add_parser("verify")
    verify.add_argument("--url", default=DEFAULT_RENDER_URL)
    verify.add_argument("--mcp-path", default="/mcp")
    verify.add_argument("--token")
    verify.add_argument("--token-file", default=str(DEFAULT_TOKEN_FILE))
    verify.add_argument("--timeout", type=float, default=20)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_remote_verify)

    flow = sub.add_parser("flow")
    flow_sub = flow.add_subparsers(dest="flow_command", required=True)
    flow_validate = flow_sub.add_parser("validate")
    flow_validate.add_argument("path")
    flow_validate.add_argument("--json", action="store_true")
    flow_validate.set_defaults(func=cmd_flow_validate)

    flow_run = flow_sub.add_parser("run")
    flow_run.add_argument("path")
    flow_run.add_argument("--dry-run", action="store_true")
    flow_run.add_argument("-e", "--env", action="append", default=[])
    flow_run.add_argument("--json", action="store_true")
    flow_run.set_defaults(func=cmd_flow_run)

    flow_list = flow_sub.add_parser("list")
    flow_list.add_argument("path", nargs="?", default=".")
    flow_list.add_argument("--tag", action="append", default=[])
    flow_list.add_argument("--exclude-tag", action="append", default=[])
    flow_list.add_argument("--json", action="store_true")
    flow_list.set_defaults(func=cmd_flow_list)

    flow_manifest = flow_sub.add_parser("manifest")
    flow_manifest.add_argument("path", nargs="?", default=".")
    flow_manifest.add_argument("--tag", action="append", default=[])
    flow_manifest.add_argument("--exclude-tag", action="append", default=[])
    flow_manifest.add_argument("--json", action="store_true")
    flow_manifest.set_defaults(func=cmd_flow_manifest)

    flow_run_suite = flow_sub.add_parser("run-suite")
    flow_run_suite.add_argument("path", nargs="?", default=".")
    flow_run_suite.add_argument("--dry-run", action="store_true")
    flow_run_suite.add_argument("--tag", action="append", default=[])
    flow_run_suite.add_argument("--exclude-tag", action="append", default=[])
    flow_run_suite.add_argument("-e", "--env", action="append", default=[])
    flow_run_suite.add_argument("--format", choices=["junit", "html"])
    flow_run_suite.add_argument("--output")
    flow_run_suite.add_argument("--allow-failures", action="store_true")
    flow_run_suite.add_argument("--json", action="store_true")
    flow_run_suite.set_defaults(func=cmd_flow_run_suite)

    flow_watch = flow_sub.add_parser("watch")
    flow_watch.add_argument("path", nargs="?", default=".")
    flow_watch.add_argument("--dry-run", action="store_true")
    flow_watch.add_argument("--tag", action="append", default=[])
    flow_watch.add_argument("--exclude-tag", action="append", default=[])
    flow_watch.add_argument("-e", "--env", action="append", default=[])
    flow_watch.add_argument("--interval", type=float, default=1.0)
    flow_watch.add_argument("--max-runs", type=int)
    flow_watch.add_argument("--json", action="store_true")
    flow_watch.set_defaults(func=cmd_flow_watch)

    flow_push = flow_sub.add_parser("push")
    flow_push.add_argument("path", nargs="?", default=".")
    flow_push.add_argument("--url", default=DEFAULT_RENDER_URL)
    flow_push.add_argument("--client-token")
    flow_push.add_argument("--client-token-file")
    flow_push.add_argument("--tag", action="append", default=[])
    flow_push.add_argument("--exclude-tag", action="append", default=[])
    flow_push.add_argument("--timeout", type=float, default=30)
    flow_push.add_argument("--dry-run", action="store_true")
    flow_push.add_argument("--json", action="store_true")
    flow_push.set_defaults(func=cmd_flow_push)

    flow_init = flow_sub.add_parser("init")
    flow_init.add_argument("path")
    flow_init.add_argument("--template", choices=sorted(TEMPLATES), default="company-health")
    flow_init.add_argument("--force", action="store_true")
    flow_init.add_argument("--json", action="store_true")
    flow_init.set_defaults(func=cmd_flow_init)

    flow_init_workspace = flow_sub.add_parser("init-workspace")
    flow_init_workspace.add_argument("path")
    flow_init_workspace.add_argument("--connector", default="flowaccount")
    flow_init_workspace.add_argument("--jurisdiction", default="TH")
    flow_init_workspace.add_argument("--month")
    flow_init_workspace.add_argument("--force", action="store_true")
    flow_init_workspace.add_argument("--json", action="store_true")
    flow_init_workspace.set_defaults(func=cmd_flow_init_workspace)

    flow_cheat = flow_sub.add_parser("cheat-sheet")
    flow_cheat.add_argument("--json", action="store_true")
    flow_cheat.set_defaults(func=cmd_flow_cheat_sheet)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
