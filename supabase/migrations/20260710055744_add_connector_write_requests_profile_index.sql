create index if not exists connector_write_requests_profile_idx
  on public.connector_write_requests (connector_profile_id);
