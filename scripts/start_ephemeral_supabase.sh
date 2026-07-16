#!/usr/bin/env bash

set -euo pipefail

: "${GITHUB_ENV:?GITHUB_ENV is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"

START_LOG="$RUNNER_TEMP/supabase-start.log"
ENV_FILE="$RUNNER_TEMP/supabase.env"
STATUS_LOG="$RUNNER_TEMP/supabase-status.log"
umask 077
trap 'rm -f "$START_LOG" "$ENV_FILE" "$STATUS_LOG"' EXIT
if ! supabase start \
  --exclude edge-runtime,imgproxy,logflare,mailpit,postgres-meta,realtime,storage-api,studio,supavisor,vector \
  >"$START_LOG" 2>&1
then
  echo "failed to start ephemeral Supabase" >&2
  exit 1
fi
rm -f "$START_LOG"

if ! supabase status -o env \
  --override-name api.url=SUPABASE_URL \
  --override-name auth.anon_key=SUPABASE_ANON_KEY \
  --override-name auth.service_role_key=SUPABASE_SERVICE_ROLE_KEY \
  >"$ENV_FILE" 2>"$STATUS_LOG"
then
  echo "failed to read ephemeral Supabase status" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a
rm -f "$ENV_FILE"
rm -f "$STATUS_LOG"
trap - EXIT

test "$SUPABASE_URL" = "http://127.0.0.1:54321"
test -n "$SUPABASE_ANON_KEY"
test -n "$SUPABASE_SERVICE_ROLE_KEY"
printf '::add-mask::%s\n' \
  "$SUPABASE_ANON_KEY" \
  "$SUPABASE_SERVICE_ROLE_KEY"

EMAIL="mercury-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}@example.invalid"
PASSWORD="$(openssl rand -hex 24)"
AUTH_JSON="$(curl --fail --silent --show-error \
  -X POST "$SUPABASE_URL/auth/v1/signup" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  --data "$(jq -nc --arg email "$EMAIL" --arg password "$PASSWORD" \
    '{email:$email,password:$password}')")"
SUPABASE_AUTHENTICATED_TEST_JWT="$(
  jq -er '.access_token | strings | select(length > 0)' <<<"$AUTH_JSON"
)"

printf '::add-mask::%s\n' "$SUPABASE_AUTHENTICATED_TEST_JWT"
printf '%s=%s\n' \
  "SUPABASE_URL" "$SUPABASE_URL" \
  "SUPABASE_ANON_KEY" "$SUPABASE_ANON_KEY" \
  "SUPABASE_SERVICE_ROLE_KEY" "$SUPABASE_SERVICE_ROLE_KEY" \
  "SUPABASE_AUTHENTICATED_TEST_JWT" "$SUPABASE_AUTHENTICATED_TEST_JWT" \
  >>"$GITHUB_ENV"
