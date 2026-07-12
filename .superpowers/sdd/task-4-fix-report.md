# Task 4 Review Fix Report

## RED

- Added focused regression coverage for record-shaped sensitive identifiers, Basic/Cookie/free-text assignments, URL query and fragment rejection, structured format precedence, YAML directives/document markers, Postman body modes, fail-closed peer verification, total streaming deadline, constant remote/local errors, and validation error secrecy.
- Initial focused run: `15 failed, 45 passed`.
- Parser-precedence follow-up: `1 failed, 2 passed`, proving OpenAPI detection still used Swagger parser semantics when both markers were present.

## GREEN

- Sanitization now uses Task 3 sensitive-key/header classifiers for record values, handles cookie containers, and redacts Basic credentials plus sensitive header/assignment text idempotently.
- Remote policy rejects every query or fragment before DNS. HTTP, transport, timeout, peer, and local OS failures expose constant importer codes without URL, body, or path echo.
- Structured selection is OpenAPI 3, Swagger 2, Postman 2.1, then Markdown. Multiple markers select the first format, JSON remains first, YAML directives/document markers parse safely, and duplicate keys/aliases remain rejected.
- Postman form-data splits text fields into `body` and file fields into `files`; URL-encoded and raw bodies now carry normalized content types.
- Successful remote imports require verified peer metadata in the pre-resolved safe set. MockTransport success tests deliberately attach a test stream extension.
- Remote reads use an injected monotonic 20-second deadline, bounded HTTPX timeouts, and checks before request, after headers, and before/after every streamed chunk.
- Focused suite: `64 passed`.
- Full non-integration suite: `553 passed, 1 deselected, 1 existing deprecation warning`.
- Ruff and `git diff --check`: passed.

## Remaining Scope

- Full peer connection pinning is intentionally deferred to Task 11. Task 4 fails closed when peer metadata is absent and does not claim pinning beyond metadata verification.
