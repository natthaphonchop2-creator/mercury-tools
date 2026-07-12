begin;

update public.mercury_connector_profiles
set
  status = 'requires_credentials',
  metadata = metadata
    - 'server_vault'
    - 'credential_fingerprints'
    - 'credential_fields'
    - 'credentials_configured'
    - 'credentials_configured_at'
    - 'credential_storage',
  updated_at = now()
where metadata ?| array[
  'server_vault',
  'credential_fingerprints',
  'credential_fields',
  'credentials_configured',
  'credentials_configured_at',
  'credential_storage'
];

drop table if exists public.connector_write_requests;

update public.mercury_skill_catalog
set
  tags = tags - 'private',
  updated_at = now()
where tags ? 'private';

update public.mercury_skill_catalog
set
  summary = 'FlowAccount journal workflow through local catalog actions and confirmation gates',
  updated_at = now()
where skill_id = 'flowaccount-journal-posting-th';

commit;
