create table if not exists public.connector_write_requests (
  id uuid primary key default gen_random_uuid(),
  request_key text not null unique,
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  connector_profile_id uuid not null references public.mercury_connector_profiles(id) on delete cascade,
  connector_id text not null default 'flowaccount' check (connector_id = 'flowaccount'),
  environment text not null check (environment in ('production', 'sandbox')),
  operation text not null default 'journal.create' check (operation = 'journal.create'),
  input_hash text not null,
  encrypted_payload text not null,
  payload_version integer not null default 1,
  status text not null default 'previewed' check (
    status in (
      'previewed',
      'executing',
      'draft_created',
      'approved',
      'failed',
      'outcome_unknown',
      'expired',
      'cancelled'
    )
  ),
  flowaccount_record_id bigint,
  document_serial text,
  response_summary jsonb not null default '{}'::jsonb,
  expires_at timestamptz not null,
  executed_at timestamptz,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists connector_write_requests_workspace_idx
  on public.connector_write_requests (workspace_id, created_at desc);

create index if not exists connector_write_requests_record_idx
  on public.connector_write_requests (workspace_id, flowaccount_record_id)
  where flowaccount_record_id is not null;

create unique index if not exists connector_write_requests_dedupe_idx
  on public.connector_write_requests (
    workspace_id,
    connector_profile_id,
    operation,
    input_hash
  )
  where status in ('executing', 'draft_created', 'approved', 'outcome_unknown');

alter table public.connector_write_requests enable row level security;

revoke all on table public.connector_write_requests from anon, authenticated;
grant all on table public.connector_write_requests to service_role;
