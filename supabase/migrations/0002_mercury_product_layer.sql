create extension if not exists pgcrypto;

create table if not exists public.mercury_workspaces (
  id uuid primary key default gen_random_uuid(),
  workspace_key text not null unique,
  name text not null,
  plan text not null default 'invite-preview',
  status text not null default 'active',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.mercury_workspace_members (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  email text not null,
  role text not null default 'member',
  host_app text not null default 'generic',
  status text not null default 'active',
  created_at timestamptz not null default now(),
  last_seen_at timestamptz,
  unique (workspace_id, email)
);

create table if not exists public.mercury_client_tokens (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  member_id uuid not null references public.mercury_workspace_members(id) on delete cascade,
  token_jti text not null unique,
  subject_email text not null,
  host_app text not null default 'generic',
  scopes jsonb not null default '[]'::jsonb,
  status text not null default 'active',
  issued_at timestamptz not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.mercury_connector_profiles (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  connector_id text not null,
  environment text not null,
  display_name text not null,
  company_name text,
  status text not null default 'requires_credentials',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, connector_id, environment)
);

create table if not exists public.mercury_skill_catalog (
  skill_id text primary key,
  title text not null,
  category text not null default 'general',
  summary text not null default '',
  status text not null default 'available',
  version text not null default '0.1.0',
  required_connectors jsonb not null default '[]'::jsonb,
  tags jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.mercury_workspace_skills (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  skill_id text not null references public.mercury_skill_catalog(skill_id) on delete cascade,
  enabled boolean not null default true,
  configured_by_member_id uuid references public.mercury_workspace_members(id) on delete set null,
  configured_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (workspace_id, skill_id)
);

create table if not exists public.mercury_skill_uploads (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  member_id uuid references public.mercury_workspace_members(id) on delete set null,
  skill_id text not null,
  title text not null,
  markdown text not null,
  status text not null default 'draft',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.mercury_product_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  member_id uuid references public.mercury_workspace_members(id) on delete set null,
  created_at timestamptz not null default now(),
  event_type text not null,
  input_hash text not null,
  summary jsonb not null default '{}'::jsonb,
  status text not null default 'ok',
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists mercury_workspace_members_email_idx
  on public.mercury_workspace_members (email);

create index if not exists mercury_client_tokens_jti_idx
  on public.mercury_client_tokens (token_jti);

create index if not exists mercury_connector_profiles_workspace_idx
  on public.mercury_connector_profiles (workspace_id, connector_id, environment);

create index if not exists mercury_workspace_skills_workspace_idx
  on public.mercury_workspace_skills (workspace_id, enabled);

create index if not exists mercury_product_events_workspace_idx
  on public.mercury_product_events (workspace_id, created_at desc);

alter table public.mercury_workspaces enable row level security;
alter table public.mercury_workspace_members enable row level security;
alter table public.mercury_client_tokens enable row level security;
alter table public.mercury_connector_profiles enable row level security;
alter table public.mercury_skill_catalog enable row level security;
alter table public.mercury_workspace_skills enable row level security;
alter table public.mercury_skill_uploads enable row level security;
alter table public.mercury_product_events enable row level security;

revoke all on table public.mercury_workspaces from anon, authenticated;
revoke all on table public.mercury_workspace_members from anon, authenticated;
revoke all on table public.mercury_client_tokens from anon, authenticated;
revoke all on table public.mercury_connector_profiles from anon, authenticated;
revoke all on table public.mercury_skill_catalog from anon, authenticated;
revoke all on table public.mercury_workspace_skills from anon, authenticated;
revoke all on table public.mercury_skill_uploads from anon, authenticated;
revoke all on table public.mercury_product_events from anon, authenticated;

grant all on table public.mercury_workspaces to service_role;
grant all on table public.mercury_workspace_members to service_role;
grant all on table public.mercury_client_tokens to service_role;
grant all on table public.mercury_connector_profiles to service_role;
grant all on table public.mercury_skill_catalog to service_role;
grant all on table public.mercury_workspace_skills to service_role;
grant all on table public.mercury_skill_uploads to service_role;
grant all on table public.mercury_product_events to service_role;

insert into public.mercury_skill_catalog (
  skill_id,
  title,
  category,
  summary,
  status,
  version,
  required_connectors,
  tags
)
values
  (
    'company-health-check-th',
    'Company Health Check TH',
    'audit',
    'ตรวจสุขภาพบริษัทจากข้อมูลบัญชีและหลักฐานที่มี พร้อมจุดที่ควรให้บัญชีตรวจทาน',
    'available',
    '0.1.0',
    '["flowaccount"]'::jsonb,
    '["audit","thai","management"]'::jsonb
  ),
  (
    'vat-summary-th',
    'VAT Summary TH',
    'tax',
    'ช่วยสรุป VAT และบริบทภาษีซื้อ/ภาษีขายพร้อม citation จาก Mercury Wiki',
    'available',
    '0.1.0',
    '["flowaccount"]'::jsonb,
    '["vat","thai","tax"]'::jsonb
  ),
  (
    'invoice-review-th',
    'Invoice Review TH',
    'audit',
    'ตรวจใบแจ้งหนี้/ใบกำกับภาษีแบบอ่านอย่างเดียวและทำรายการประเด็นให้ฝ่ายบัญชี',
    'available',
    '0.1.0',
    '["flowaccount"]'::jsonb,
    '["invoice","audit","thai"]'::jsonb
  ),
  (
    'management-report-th',
    'Management Report TH',
    'reporting',
    'เตรียม context pack สำหรับรายงานผู้บริหาร: รายได้, VAT, cash flow, margin',
    'available',
    '0.1.0',
    '["flowaccount"]'::jsonb,
    '["report","thai","finance"]'::jsonb
  ),
  (
    'connector-setup-guide-th',
    'Connector Setup Guide TH',
    'setup',
    'แนะนำขั้นตอนเชื่อมโปรแกรมบัญชี โดยแยกข้อมูลที่ต้องถามผู้ใช้กับค่าที่ตั้งล่วงหน้าได้',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["setup","connector","thai"]'::jsonb
  )
on conflict (skill_id) do update set
  title = excluded.title,
  category = excluded.category,
  summary = excluded.summary,
  status = excluded.status,
  version = excluded.version,
  required_connectors = excluded.required_connectors,
  tags = excluded.tags,
  updated_at = now();
