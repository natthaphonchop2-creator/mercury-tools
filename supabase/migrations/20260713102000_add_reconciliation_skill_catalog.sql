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
    'accounts-receivable-reconciliation-th',
    'Accounts Receivable Reconciliation TH',
    'reconciliation',
    'กระทบยอดลูกหนี้ ใบแจ้งหนี้ ใบเสร็จ และหลักฐานรับชำระ พร้อมแสดงผลต่างอย่างชัดเจน',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["reconciliation","receivables","cross-mcp","thai"]'::jsonb
  ),
  (
    'accounts-payable-reconciliation-th',
    'Accounts Payable Reconciliation TH',
    'reconciliation',
    'กระทบยอดเจ้าหนี้ บิล ค่าใช้จ่าย และหลักฐานจ่ายเงิน พร้อมรายการที่ต้องตรวจทาน',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["reconciliation","payables","cross-mcp","thai"]'::jsonb
  ),
  (
    'bank-settlement-reconciliation-th',
    'Bank Settlement Reconciliation TH',
    'reconciliation',
    'กระทบยอดรายการ ERP กับ statement หรือ settlement โดยไม่อนุมานข้อมูลธนาคารที่ขาด',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["reconciliation","bank","settlement","cross-mcp","thai"]'::jsonb
  ),
  (
    'marketplace-settlement-review-th',
    'Marketplace Settlement Review TH',
    'reconciliation',
    'ตรวจ orders, fees, refunds และ payouts จาก marketplace เทียบหลักฐานบัญชีที่เชื่อมได้',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["marketplace","settlement","reconciliation","cross-mcp","thai"]'::jsonb
  ),
  (
    'month-end-evidence-gathering-th',
    'Month-End Evidence Gathering TH',
    'accounting',
    'รวบรวมและจัดกลุ่มหลักฐานปิดเดือนจากแหล่งที่เชื่อม โดยระบุรายการขาดและข้อขัดแย้ง',
    'available',
    '0.1.0',
    '[]'::jsonb,
    '["month-end","evidence","accounting","cross-mcp","thai"]'::jsonb
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
