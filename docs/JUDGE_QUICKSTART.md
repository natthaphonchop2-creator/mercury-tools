# Mercury Finance Judge Quickstart

Mercury Finance installs one repository-local `stdio` MCP server. It uses the
Mercury `v0.2.0` runtime for FlowAccount and PEAK endpoint search, safe reads,
and approval-gated writes. The plugin does not register a hosted HTTP MCP and
does not contain tokens, environment values, or ERP credentials.

## Install After The Task 18 Release

Use an isolated Codex home for evaluation. Install only the marketplace and
plugin paths below.

```bash
export MERCURY_TEST_CODEX_HOME="$(mktemp -d)"
CODEX_HOME="$MERCURY_TEST_CODEX_HOME" codex plugin marketplace add \
  natthaphonchop2-creator/mercury-tools \
  --ref v0.2.0 \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
CODEX_HOME="$MERCURY_TEST_CODEX_HOME" codex plugin add mercury-finance@mercury-tools
```

The installed plugin launches exactly this local command through `uvx`:

```text
uvx --from git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.0 mercury mcp serve-local
```

## Evaluate In A Repository

Open the repository that will own the ERP connection. Mercury stores local
connection material and its audit ledger in that repository; do not paste
secrets into chat or plugin configuration.

Use these prompts in Codex:

1. `Set up local FlowAccount access for this repository and verify it.`
2. `Search the local ERP action catalog and run a safe read action.`
3. `Preview an approval-gated PEAK write for this repository without executing it.`

Writes require the runtime's returned confirmation contract. A preview does not
execute an ERP mutation.

## Task16 Validation Boundary

Task 16 validates the `v0.2.0` launcher statically and runs a local wheel-only
`uvx` CLI and stdio smoke test. The immutable `v0.2.0` tag is available only
after the Task 18 release, when remote-tag smoke can run. Do not bypass this
boundary by editing an installed Codex plugin cache; update the repository
source and reinstall the plugin instead.
