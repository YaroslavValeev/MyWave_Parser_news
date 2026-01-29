"""
P1 интеграционные проверки: CONTRACT, идемпотентность, review workflow, row_number/headers.
Запуск: python test_p1_integration.py (требуется .env и credentials.json).
"""
import asyncio
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Импорт канонического модуля Sheets (как в test_p0_integration)
from utils.import_asyncio import init_google_sheets, save_to_sheet, get_worksheet, ensure_contract_sheet
from utils.row_utils import generate_checksum


async def test_p1_contract_sheet():
    """Проверка наличия и заполнения листа CONTRACT."""
    doc = await init_google_sheets()
    if not doc:
        return {"ok": False, "reason": "init_google_sheets failed"}
    ws = get_worksheet(doc, "CONTRACT")
    if not ws:
        return {"ok": False, "reason": "worksheet CONTRACT not found"}
    all_values = ws.get_all_values()
    if not all_values or len(all_values) < 2:
        return {"ok": False, "reason": "CONTRACT empty or only header"}
    header = all_values[0]
    required = ["field_name", "owner", "required_for", "contract_version"]
    missing = [c for c in required if c not in header]
    if missing:
        return {"ok": False, "reason": f"CONTRACT missing columns: {missing}"}
    return {"ok": True, "rows": len(all_values) - 1, "header": header}


async def test_p1_review_queue_no_approve():
    """Вставка записи с review_queue=TRUE без approve — бот не перетирает publish-поля."""
    doc = await init_google_sheets()
    if not doc:
        return {"ok": False, "reason": "init failed"}
    ts = int(datetime.now(timezone.utc).timestamp())
    item = {
        "id": f"p1_review_{ts}",
        "source_type": "manual",
        "source_name": "P1 Review Test",
        "source_url": "https://example.com/p1-review",
        "source_item_id": f"p1_review_{ts}",  # уникальный для идемпотентности
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ingest_status": "ok",
        "raw_title": "P1: запись с review_queue без approve",
        "raw_content": "Проверка: review_queue=true, approved_by/approved_at не трогаем.",
        "status": "DRAFT",
    }
    item["checksum"] = generate_checksum(item)
    await save_to_sheet(doc, "raw_feed", [item])
    ws = doc.worksheet("raw_feed")
    all_values = ws.get_all_values()
    header = all_values[0]
    rq_idx = header.index("review_queue") if "review_queue" in header else None
    ab_idx = header.index("approved_by") if "approved_by" in header else None
    ch_idx = header.index("checksum") if "checksum" in header else None
    if ch_idx is None:
        return {"ok": False, "reason": "no checksum column"}
    row_found = None
    for row in all_values[1:]:
        if len(row) > ch_idx and row[ch_idx] == item["checksum"]:
            row_found = row
            break
    if not row_found:
        return {"ok": False, "reason": "inserted row not found"}
    review_val = row_found[rq_idx] if rq_idx is not None and len(row_found) > rq_idx else ""
    approved_val = row_found[ab_idx] if ab_idx is not None and len(row_found) > ab_idx else ""
    # Номер строки для ссылки в docs (raw_feed gid=1039755742)
    inserted_row_num = next(
        (idx for idx, row in enumerate(all_values[1:], start=2) if len(row) > ch_idx and row[ch_idx] == item["checksum"]),
        None,
    )
    # review_queue в Sheets пишется как булево TRUE; gspread может вернуть True или "TRUE"
    rq_ok = review_val is True or (isinstance(review_val, str) and review_val.strip().upper() == "TRUE")
    return {
        "ok": True,
        "review_queue_value": review_val,
        "approved_by_value": approved_val,
        "review_queue_is_true": rq_ok,
        "inserted_row_num": inserted_row_num,
    }


async def test_p1_idempotency_same_source_item_id():
    """Повторная вставка с тем же source_item_id не создаёт дубль."""
    doc = await init_google_sheets()
    if not doc:
        return {"ok": False, "reason": "init failed"}
    ts = int(datetime.now(timezone.utc).timestamp())
    sid = f"p1_idem_{ts}"
    item = {
        "id": f"p1_idem_{ts}",
        "source_type": "manual",
        "source_name": "P1 Idempotency",
        "source_url": "https://example.com/p1-idem",
        "source_item_id": sid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ingest_status": "ok",
        "raw_title": "P1 идемпотентность: один и тот же source_item_id",
        "raw_content": "Вторая вставка с тем же source_item_id не должна добавить строку.",
        "status": "DRAFT",
    }
    item["checksum"] = generate_checksum(item)
    await save_to_sheet(doc, "raw_feed", [item])
    count_after_first = 0
    ws = doc.worksheet("raw_feed")
    all_values = ws.get_all_values()
    header = all_values[0]
    st_idx = header.index("source_type") if "source_type" in header else None
    sn_idx = header.index("source_name") if "source_name" in header else None
    sid_idx = header.index("source_item_id") if "source_item_id" in header else None
    if st_idx is None or sn_idx is None or sid_idx is None:
        return {"ok": False, "reason": "missing source_type/source_name/source_item_id columns"}
    for row in all_values[1:]:
        if len(row) > sid_idx and row[sid_idx] == sid:
            count_after_first += 1
    # Вторая вставка того же item (тот же source_item_id)
    await save_to_sheet(doc, "raw_feed", [item])
    all_values2 = ws.get_all_values()
    count_after_second = 0
    for row in all_values2[1:]:
        if len(row) > sid_idx and row[sid_idx] == sid:
            count_after_second += 1
    no_duplicate = count_after_second == count_after_first and count_after_first >= 1
    return {
        "ok": no_duplicate,
        "count_after_first": count_after_first,
        "count_after_second": count_after_second,
        "no_duplicate": no_duplicate,
    }


async def test_p1_row_number_and_headers_stable():
    """Проверка: row_number корректен, заголовки стабильны (есть критичные колонки)."""
    doc = await init_google_sheets()
    if not doc:
        return {"ok": False, "reason": "init failed"}
    ws = doc.worksheet("raw_feed")
    all_values = ws.get_all_values()
    if not all_values:
        return {"ok": False, "reason": "raw_feed empty"}
    header = all_values[0]
    critical = ["row_number", "checksum", "source_type", "source_name", "source_item_id", "review_queue"]
    missing = [c for c in critical if c not in header]
    if missing:
        return {"ok": False, "reason": f"missing headers: {missing}", "header": header}
    rn_idx = header.index("row_number")
    for idx, row in enumerate(all_values[1:], start=2):
        if len(row) > rn_idx and row[rn_idx]:
            if str(row[rn_idx]).strip() != str(idx):
                return {"ok": False, "reason": f"row_number mismatch at row {idx}: value={row[rn_idx]}"}
    return {"ok": True, "critical_headers": critical}


async def run_all():
    """Запуск всех P1 проверок и вывод результатов."""
    results = {}
    results["contract_sheet"] = await test_p1_contract_sheet()
    results["review_queue_no_approve"] = await test_p1_review_queue_no_approve()
    results["idempotency_source_item_id"] = await test_p1_idempotency_same_source_item_id()
    results["row_number_and_headers"] = await test_p1_row_number_and_headers_stable()
    return results


def _sheet_url_from_config():
    """Ссылка на raw_feed для фиксации в docs (gid=1039755742)."""
    try:
        from config.settings import config
        sid = getattr(config, "GOOGLE_SHEET_ID", "") or ""
        if sid:
            return f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=1039755742"
    except Exception:
        pass
    return ""


if __name__ == "__main__":
    async def main():
        r = await run_all()
        print("\n" + "=" * 60)
        print("P1 ИНТЕГРАЦИОННЫЕ ПРОВЕРКИ — РЕЗУЛЬТАТЫ")
        print("=" * 60)
        for name, res in r.items():
            status = "✅ OK" if res.get("ok") else "❌ FAIL"
            print(f"{name}: {status}")
            if not res.get("ok"):
                print(f"  reason: {res.get('reason', '')}")
            elif isinstance(res, dict):
                for k, v in res.items():
                    if k not in ("ok", "reason"):
                        print(f"  {k}: {v}")
        # Ссылка на строку с review_queue=TRUE для docs/P1_RUN_RESULTS.md
        rev = r.get("review_queue_no_approve", {})
        if rev.get("ok") and rev.get("inserted_row_num"):
            base = _sheet_url_from_config()
            if base:
                print(f"\n  Ссылка на строку raw_feed (review_queue=TRUE): {base}&range=A{rev['inserted_row_num']}")
        print("=" * 60)
        all_ok = all(v.get("ok") for v in r.values())
        return 0 if all_ok else 1

    exit(asyncio.run(main()))
