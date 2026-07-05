"""Prompt templates exposed through MCP."""

from __future__ import annotations

PROMPTS = {
    "company_health_check_th": (
        "ตรวจสุขภาพบริษัทจาก context ที่ Mercury Tools ให้มา สรุปสั้น กระชับ "
        "แยกสิ่งที่พบ ความเสี่ยง และจุดที่ควรให้นักบัญชีตรวจทาน พร้อมอ้างอิง citation"
    ),
    "vat_summary_th": (
        "สรุป VAT จากข้อมูลที่ค้นคืนมา ระบุเดือน จำนวนเอกสาร ยอด VAT โดยประมาณ "
        "และข้อจำกัดของข้อมูล ห้ามแต่งตัวเลขที่ไม่มีใน context"
    ),
    "invoice_review_th": (
        "รีวิว invoice โดยเน้นความครบถ้วนของลูกค้า รายการสินค้า VAT สถานะเอกสาร "
        "และ anomaly ที่ควรตรวจ"
    ),
    "management_report_th": (
        "ทำ management report แบบผู้บริหาร อ่านง่าย มี revenue/cost/profit/cash-flow "
        "ถ้าข้อมูลไม่พอให้ระบุ missing data ชัดเจน"
    ),
    "connector_setup_guide_th": (
        "อธิบายขั้นตอนเชื่อมต่อโปรแกรมบัญชีแบบเป็นกลาง: เลือกโปรแกรม เลือก environment "
        "กรอก credentials ตรวจ connection และเปิด capabilities"
    ),
}


def get_prompt(name: str) -> str:
    if name not in PROMPTS:
        raise KeyError(f"Unknown prompt: {name}")
    return PROMPTS[name]

