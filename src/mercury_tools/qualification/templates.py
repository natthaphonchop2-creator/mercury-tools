"""Deterministic Thai and English qualification status summaries."""

from __future__ import annotations

from mercury_tools.qualification.models import ValidationKnowledge, ValidationStatus

SUMMARY_TH = {
    ValidationStatus.LIVE_SUCCESS: "คำขอใน sandbox สำเร็จและได้ผลลัพธ์ตามที่ตรวจสอบไว้",
    ValidationStatus.LIVE_FAILED: "คำขอใน sandbox เสร็จสิ้นแต่พบความล้มเหลวที่จัดประเภทแล้ว",
    ValidationStatus.CONTRACT_VALIDATED: "ตรวจสอบสัญญา endpoint แล้วโดยยังไม่ได้เรียก provider",
    ValidationStatus.BLOCKED_MISSING_CREDENTIALS: (
        "ไม่มีข้อมูลรับรองของ provider " "สำหรับการตรวจสอบแบบ live"
    ),
    ValidationStatus.BLOCKED_MISSING_PREREQUISITE: "ยังไม่มี fixture หรือเงื่อนไขเบื้องต้นที่ผ่านการตรวจสอบ",
    ValidationStatus.BLOCKED_EXTERNAL_EFFECT: (
        "บล็อกการเรียกแบบ live เพราะการกระทำอาจกระทบปลายทางที่ควบคุมไม่ได้"
    ),
    ValidationStatus.UNSUPPORTED_BY_SANDBOX: "การกระทำที่ระบุไว้ไม่พร้อมใช้งานใน sandbox ของ provider",
    ValidationStatus.OUTCOME_UNKNOWN: "ไม่สามารถพิสูจน์ผลลัพธ์ของคำขอจาก provider และไม่ได้ลองซ้ำ",
}

SUMMARY_EN = {
    ValidationStatus.LIVE_SUCCESS: "Sandbox request completed with the reviewed expected outcome.",
    ValidationStatus.LIVE_FAILED: "Sandbox request completed with a classified failure.",
    ValidationStatus.CONTRACT_VALIDATED: "Endpoint contract validated without a provider call.",
    ValidationStatus.BLOCKED_MISSING_CREDENTIALS: (
        "Provider credentials are not available " "for live validation."
    ),
    ValidationStatus.BLOCKED_MISSING_PREREQUISITE: (
        "A reviewed prerequisite fixture is unavailable."
    ),
    ValidationStatus.BLOCKED_EXTERNAL_EFFECT: (
        "Live execution is blocked because the action may affect " "an uncontrolled target."
    ),
    ValidationStatus.UNSUPPORTED_BY_SANDBOX: (
        "The documented action is unavailable in the provider sandbox."
    ),
    ValidationStatus.OUTCOME_UNKNOWN: (
        "The provider request outcome could not be proven and was not retried."
    ),
}


def render_summary_th(record: ValidationKnowledge) -> str:
    return SUMMARY_TH[record.validation_status]


def render_summary_en(record: ValidationKnowledge) -> str:
    return SUMMARY_EN[record.validation_status]
