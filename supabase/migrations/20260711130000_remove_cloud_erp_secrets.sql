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
    - 'credential_storage'
    - 'ciphertext'
    - 'credential_vault'
    - 'encrypted_credentials'
    - 'vault_record',
  updated_at = now()
where metadata ?| array[
  'server_vault',
  'credential_fingerprints',
  'credential_fields',
  'credentials_configured',
  'credentials_configured_at',
  'credential_storage',
  'ciphertext',
  'credential_vault',
  'encrypted_credentials',
  'vault_record'
];

drop table if exists public.connector_write_requests;

update public.mercury_skill_catalog
set
  tags = tags - 'private',
  summary = 'FlowAccount journal workflow through local catalog actions and confirmation gates',
  updated_at = now()
where skill_id = 'flowaccount-journal-posting-th'
  and (
    tags ? 'private'
    or summary is distinct from
      'FlowAccount journal workflow through local catalog actions and confirmation gates'
  );

commit;
