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
    'connector-credential-setup-th',
    'Connector Credential Setup TH',
    'setup',
    'นำผู้ใช้เชื่อม ERP ทีละขั้นและหยุดรอจนแต่ละขั้นตรวจสอบสำเร็จ',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["setup","credentials","connector","thai"]'::jsonb
  ),
  (
    'flowaccount-connector-setup-th',
    'FlowAccount Connector Setup TH',
    'setup',
    'เชื่อมและตรวจสอบ FlowAccount แบบ guided setup โดยไม่เปิดเผย credential',
    'available',
    '0.1.0',
    '["flowaccount"]'::jsonb,
    '["setup","connector","flowaccount","thai"]'::jsonb
  ),
  (
    'peak-connector-setup-th',
    'PEAK Connector Setup TH',
    'setup',
    'แนะนำการเชื่อม PEAK Open API, credential ที่ต้องใช้, เอกสารอ้างอิง, และ setup validation ก่อนใช้งาน GET/POST endpoint',
    'available',
    '0.1.0',
    '["peak"]'::jsonb,
    '["setup","connector","peak","thai"]'::jsonb
  ),
  (
    'mercury-flow-runner',
    'Mercury Flow Runner',
    'automation',
    'วางแผน บันทึก และรัน workflow บัญชีแบบ read-only พร้อม capability gate',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["flow","workflow","automation","read-only"]'::jsonb
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
