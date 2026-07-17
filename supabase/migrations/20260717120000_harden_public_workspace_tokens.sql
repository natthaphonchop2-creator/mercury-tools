update public.mercury_client_tokens
set
  status = 'revoked',
  revoked_at = coalesce(revoked_at, now()),
  expires_at = least(expires_at, now())
where scopes ? 'public:contest';

update public.mercury_client_tokens
set expires_at = least(expires_at, issued_at + interval '30 days')
where
  subject_email like 'public-%@workspace.invalid'
  and scopes ? 'public:workspace';
