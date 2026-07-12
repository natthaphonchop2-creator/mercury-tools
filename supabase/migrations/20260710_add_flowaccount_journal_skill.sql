insert into public.mercury_skill_catalog (
  skill_id,
  title,
  category,
  summary,
  status,
  version,
  required_connectors,
  tags,
  metadata
)
values (
  'flowaccount-journal-posting-th',
  'FlowAccount Journal Posting TH',
  'accounting',
  'เตรียม ตรวจสมดุล สร้างร่าง และอนุมัติรายการสมุดรายวัน FlowAccount โดยแยกการยืนยันแต่ละขั้น',
  'available',
  '0.1.0',
  '["flowaccount"]'::jsonb,
  '["flowaccount","journal","write","private","thai"]'::jsonb,
  '{"mcp_surface":"private"}'::jsonb
)
on conflict (skill_id) do update set
  title = excluded.title,
  category = excluded.category,
  summary = excluded.summary,
  status = excluded.status,
  version = excluded.version,
  required_connectors = excluded.required_connectors,
  tags = excluded.tags,
  metadata = excluded.metadata,
  updated_at = now();
