# report_site_47.py
import requests
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

# -------------------------
# CONFIG (передай з основного файлу)
# -------------------------
# WEBHOOK_URL, LOCAL_TZ_NAME, ZoneInfo
# TERM_FIELD, PHONE_REGION_FIELD, BOOKING_METHOD_FIELD
# PHONE_REGION_ENUM, BOOKING_METHOD_ENUM
# BASE_INACTIVITY_DAYS

# -------------------------
# CATEGORY (Site funnel)
# -------------------------
CAT_SITE = 47
CAT_ONLINE = 61
CAT_OFFLINE = 63
CAT_CHAT_SALES = 57
CAT_VG = 41
CAT_FAST_CONTACT = 65
APPOINTMENT_CATEGORIES = {CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG}

# -------------------------
# LEVELS
# -------------------------
LEVEL_NAMES = {1: "Взято", 2: "Дозвон", 3: "ЦА", 4: "Зацікавлені", 5: "Запис"}

def empty_counts():
    return {
        "Взято": 0,
        "Дозвон": 0,
        "ЦА": 0,
        "Зацікавлені": 0,
        "Запис": 0,
        "В дзвінку": 0,
        "В повідомленнях": 0,
    }

def add_levels(counter: dict, levels: set[int]):
    for lvl in sorted(levels):
        if lvl in LEVEL_NAMES:
            counter[LEVEL_NAMES[lvl]] += 1

def add_booking(counter: dict, booking_method: str):
    if booking_method == "Дзвінок":
        counter["В дзвінку"] += 1
    elif booking_method == "Повідомлення":
        counter["В повідомленнях"] += 1

# -------------------------
# HELPERS (потрібні для build_report_site)
# -------------------------
def _norm(s: str) -> str:
    return (s or "").strip().casefold()

def b24_get(WEBHOOK_URL: str, method: str, params=None) -> dict:
    url = f"{WEBHOOK_URL}{method}"
    r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{data['error']}: {data.get('error_description')}")
    return data

def parse_dt(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None

def to_local_date(dt: datetime, LOCAL_TZ_NAME: str, ZoneInfo):
    if not dt:
        return None
    if ZoneInfo is None or dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(ZoneInfo(LOCAL_TZ_NAME)).date()

def is_modified_on(date_modify: str, target_day: date, LOCAL_TZ_NAME: str, ZoneInfo) -> bool:
    dt = parse_dt(date_modify)
    return dt and to_local_date(dt, LOCAL_TZ_NAME, ZoneInfo) == target_day

def phone_region_group_from_raw(raw) -> str:
    # Закордон -> "Закордон", інше/пусто -> "Україна"
    if raw is None:
        return "Україна"
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    if val == "54067":
        return "Закордон"
    return "Україна"

def phone_region_label_from_raw(raw, PHONE_REGION_ENUM: dict) -> str:
    if raw is None:
        return "Немає номеру"
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    return PHONE_REGION_ENUM.get(val, "Немає номеру")

def booking_method_from_raw(raw, BOOKING_METHOD_ENUM: dict) -> str:
    # "" якщо пусто/None
    if raw is None:
        return ""
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    if not val:
        return ""
    return BOOKING_METHOD_ENUM.get(val, "")

# -------------------------
# STAGE -> LEVEL (воронка 47)
# -------------------------
SITE_STAGE_TO_LEVEL = {
    # NEW = 0
    "C47:NEW": 0,

    # Взято: Розсилка > не додзвон (+ Немає в месенджерах)
    "C47:PREPARATION": 1,        # Розсилка
    "C47:PREPAYMENT_INVOIC": 1,  # 2 дзвінок
    "C47:UC_FN3M0F": 1,          # 3 дзвінок
    "C47:EXECUTING": 1,          # не додзвон
    "C47:UC_D56N3S": 1,          # Немає в месенджерах

    # Дозвон (в т.ч. спец-статуси)
    "C47:UC_3PMDY3": 2,          # Додзвон
    "C47:UC_GVG7E9": 2,          # Не ЦА
    "C47:LOSE": 2,               # Угода провалена
    "C47:UC_KOEVQT": 2,          # Придбали
    "C47:UC_0TGJPJ": 2,          # Не в пошуках обручок

    # ЦА
    "C47:UC_U7J18A": 3,

    # Зацікавлені > Очікуємо бронювання
    "C47:UC_X314BU": 4,
    "C47:UC_RYMD4E": 4,

    # Запис
    "C47:UC_K9ZT4D": 5,          # Запланована консультація
    "C47:WON": 5,                # Успішна угода (якщо хочете рахувати як запис)
}

def level_from_stage_site(category_id: int, stage_id: str) -> int:
    if category_id == CAT_SITE:
        return SITE_STAGE_TO_LEVEL.get(stage_id, 0)

    # appointment funnels = одразу Запис
    if category_id in APPOINTMENT_CATEGORIES:
        return 5

    # funnel 65 = Взято + Дозвон
    if category_id == CAT_FAST_CONTACT:
        return 2

    return 0

# -------------------------
# SOURCES / BUCKETS (для Сайт 47)
# -------------------------
SOURCE_ID_TO_NAME_SITE = {
    "24": "Лендинг",          # Сайт (bucket=Сайт)
    "UC_JL9RSA": "Лендинг -2=1",
    "UC_9FJEWZ": "Лендинг 1 грам",
    "UC_WEFXCG": "Лендинг Каблучки 100$",
    "34": "Лендинг Каблучки 1 грам",
    "35": "Лендинг 2 за 1 ОФФЕР",
    "UC_61JD9N": "Лендинг - стара ціна 2025",
}

LANDING_VARIANTS_IDS = {
    "UC_JL9RSA", "UC_9FJEWZ", "UC_WEFXCG", "34", "35", "UC_61JD9N"
}

def source_name_from_id_site(source_id: str) -> str:
    sid = str(source_id or "").strip()
    return SOURCE_ID_TO_NAME_SITE.get(sid, sid or "Без джерела")

def bucket_from_source_site(source_id: str) -> str:
    sid = str(source_id or "").strip()
    if sid == "24":
        return "Сайт"
    if sid in LANDING_VARIANTS_IDS:
        return "Лендинг"
    return "Інше"

# -------------------------
# STAGEHISTORY ANALYSIS (як у вас, але з level_from_stage_site)
# -------------------------
def last_stage_key_before_day(history_rows, target_day: date, LOCAL_TZ_NAME: str, ZoneInfo):
    last_dt = None
    last_key = None
    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        d = to_local_date(dt, LOCAL_TZ_NAME, ZoneInfo)
        if d is None or d >= target_day:
            continue
        if last_dt is None or dt > last_dt:
            last_dt = dt
            last_key = (int(row.get("CATEGORY_ID", -1)), row.get("STAGE_ID", ""))
    return last_dt, last_key

def has_real_stage_change_on_day(history_rows, target_day: date, LOCAL_TZ_NAME: str, ZoneInfo) -> bool:
    _, prev_key = last_stage_key_before_day(history_rows, target_day, LOCAL_TZ_NAME, ZoneInfo)

    if prev_key is None:
        for row in history_rows:
            dt = parse_dt(row.get("CREATED_TIME"))
            if not dt:
                continue
            if to_local_date(dt, LOCAL_TZ_NAME, ZoneInfo) == target_day:
                return True
        return False

    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        if to_local_date(dt, LOCAL_TZ_NAME, ZoneInfo) != target_day:
            continue
        key = (int(row.get("CATEGORY_ID", -1)), row.get("STAGE_ID", ""))
        if key != prev_key:
            return True

    return False

def last_stage_change_before_day(history_rows, target_day: date, LOCAL_TZ_NAME: str, ZoneInfo):
    last_dt = None
    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        d = to_local_date(dt, LOCAL_TZ_NAME, ZoneInfo)
        if d is None:
            continue
        if d < target_day:
            if last_dt is None or dt > last_dt:
                last_dt = dt
    return last_dt

def max_levels_before_and_on_day(history_rows, target_day: date, LOCAL_TZ_NAME: str, ZoneInfo):
    max_before = 0
    max_today = 0
    had_today = False
    had_appointment_today = False   # <--- НОВЕ

    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue

        cat = int(row.get("CATEGORY_ID", -1))
        stg = row.get("STAGE_ID", "")

        d = to_local_date(dt, LOCAL_TZ_NAME, ZoneInfo)
        if d is None:
            continue

        # якщо цього дня угода попала в appointment funnel — це автоматично "Запис"
        if d == target_day and cat in APPOINTMENT_CATEGORIES:
            had_today = True
            max_today = max(max_today, 5)
            continue
        
        if d == target_day and cat == CAT_FAST_CONTACT:
            had_today = True
            max_today = max(max_today, 2)
            continue

        lvl = level_from_stage_site(cat, stg)
        if lvl <= 0:
            continue

        if d < target_day:
            max_before = max(max_before, lvl)
        elif d == target_day:
            had_today = True
            max_today = max(max_today, lvl)

    return had_today, max_before, max_today

def levels_gained_on_day(history_rows, target_day: date, LOCAL_TZ_NAME: str, ZoneInfo):
    had_today, max_before, max_today = max_levels_before_and_on_day(history_rows, target_day, LOCAL_TZ_NAME, ZoneInfo)

    if not had_today:
        return set(), "Не було змін статусів у цей день", max_before, max_today
    if max_today <= max_before:
        return set(), "Статус не піднявся вище (повторна робота)", max_before, max_today

    return set(range(max_before + 1, max_today + 1)), "OK", max_before, max_today

def levels_for_base_report(history_rows, target_day: date, BASE_INACTIVITY_DAYS: int, LOCAL_TZ_NAME: str, ZoneInfo):
    cutoff = target_day - timedelta(days=BASE_INACTIVITY_DAYS)

    last_before = last_stage_change_before_day(history_rows, target_day, LOCAL_TZ_NAME, ZoneInfo)
    if not last_before:
        return set(), "База: немає історії до цього дня", None, 0

    last_before_date = to_local_date(last_before, LOCAL_TZ_NAME, ZoneInfo)
    if last_before_date is None or last_before_date > cutoff:
        return set(), "База: не було паузи > 30 днів", last_before_date, 0

    had_today, _, max_today = max_levels_before_and_on_day(history_rows, target_day, LOCAL_TZ_NAME, ZoneInfo)
    if not had_today or max_today <= 0:
        return set(), "База: у цей день не було статусного руху", last_before_date, max_today

    return set(range(1, max_today + 1)), "BASE_OK", last_before_date, max_today

# -------------------------
# FETCH
# -------------------------
def fetch_all_deals_site(WEBHOOK_URL: str, manager_id: int, TERM_FIELD: str, PHONE_REGION_FIELD: str, BOOKING_METHOD_FIELD: str):
    params = {
        "filter[ASSIGNED_BY_ID]": manager_id,
        "filter[CATEGORY_ID][]": [CAT_SITE, CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG],
        "select[]": [
            "ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "DATE_MODIFY",
            "CONTACT_ID", "SOURCE_ID",
            TERM_FIELD, PHONE_REGION_FIELD, BOOKING_METHOD_FIELD,
        ],
        "start": 0
    }

    deals = []
    while True:
        data = b24_get(WEBHOOK_URL, "crm.deal.list", params)
        batch = data.get("result", [])
        if not batch:
            break
        deals.extend(batch)
        if data.get("next") is None:
            break
        params["start"] = data["next"]
    return deals

def fetch_stagehistory(WEBHOOK_URL: str, deal_id: int, limit: int = 2000):
    params = {
        "entityTypeId": 2,
        "filter[OWNER_ID]": deal_id,
        "order[CREATED_TIME]": "ASC",
        "select[]": ["CREATED_TIME", "STAGE_ID", "CATEGORY_ID"],
        "start": 0
    }

    rows = []
    while True:
        data = b24_get(WEBHOOK_URL, "crm.stagehistory.list", params)
        items = (data.get("result") or {}).get("items", [])
        if isinstance(items, list):
            rows.extend(items)

        if data.get("next") is None:
            break
        params["start"] = data["next"]

        if len(rows) >= limit:
            rows = rows[-limit:]
            break

    return rows

def normalize_phone(phones):
    if not phones:
        return ""
    if isinstance(phones, str):
        return phones.strip()
    if isinstance(phones, list):
        for x in phones:
            if isinstance(x, dict):
                v = (x.get("VALUE") or "").strip()
                if v:
                    return v
    return ""

def fetch_contacts_phones(WEBHOOK_URL: str, contact_ids: list[int]) -> dict[int, str]:
    contact_ids = [int(x) for x in contact_ids if x]
    contact_ids = sorted(set(contact_ids))
    if not contact_ids:
        return {}

    phones = {}
    chunk_size = 50
    for i in range(0, len(contact_ids), chunk_size):
        chunk = contact_ids[i:i + chunk_size]
        params = {
            "filter[ID][]": chunk,
            "select[]": ["ID", "PHONE"],
            "start": 0
        }
        while True:
            data = b24_get(WEBHOOK_URL, "crm.contact.list", params)
            res = data.get("result", [])
            for c in res:
                cid = int(c.get("ID"))
                phones[cid] = normalize_phone(c.get("PHONE"))
            if data.get("next") is None:
                break
            params["start"] = data["next"]
    return phones

# -------------------------
# MAIN BUILD (Site 47)
# -------------------------
def build_report_site(
    WEBHOOK_URL: str,
    LOCAL_TZ_NAME: str,
    ZoneInfo,
    manager_id: int,
    target_day: date,
    BASE_INACTIVITY_DAYS: int,
    TERM_FIELD: str,
    PHONE_REGION_FIELD: str,
    BOOKING_METHOD_FIELD: str,
    PHONE_REGION_ENUM: dict,
    BOOKING_METHOD_ENUM: dict,
    term_text_from_raw,                # передай з основного файлу
    fetch_deal_userfield_enum_map,      # передай з основного файлу
):
    all_deals = fetch_all_deals_site(WEBHOOK_URL, manager_id, TERM_FIELD, PHONE_REGION_FIELD, BOOKING_METHOD_FIELD)
    deals_day = [d for d in all_deals if is_modified_on(d.get("DATE_MODIFY", ""), target_day, LOCAL_TZ_NAME, ZoneInfo)]

    contact_ids = []
    for d in deals_day:
        cid = d.get("CONTACT_ID")
        if cid:
            contact_ids.append(int(cid))
    phones_map = fetch_contacts_phones(WEBHOOK_URL, contact_ids)

    term_enum_map = fetch_deal_userfield_enum_map(TERM_FIELD)

    total_day = empty_counts()
    total_base = empty_counts()

    # region -> category_label -> source_name -> counts
    day_region_category_source = defaultdict(lambda: defaultdict(lambda: defaultdict(empty_counts)))

    ignored_no_real_stage_change = 0
    skipped = Counter()
    rows = []

    for d in deals_day:
        deal_id = int(d.get("ID"))
        title = d.get("TITLE", "—")
        cat_now = int(d.get("CATEGORY_ID", -1))
        stage_now = d.get("STAGE_ID", "")
        contact_id = int(d.get("CONTACT_ID") or 0)
        phone = phones_map.get(contact_id, "")

        source_id = str(d.get("SOURCE_ID") or "").strip()
        source_name = source_name_from_id_site(source_id)

        term_raw = d.get(TERM_FIELD, "")
        term_text = term_text_from_raw(term_raw, term_enum_map)

        region_raw = d.get(PHONE_REGION_FIELD)
        region_group = phone_region_group_from_raw(region_raw)
        region_label = phone_region_label_from_raw(region_raw, PHONE_REGION_ENUM)

        booking_raw = d.get(BOOKING_METHOD_FIELD)
        booking_method = booking_method_from_raw(booking_raw, BOOKING_METHOD_ENUM)  # "Дзвінок"/"Повідомлення"/""

        history = fetch_stagehistory(WEBHOOK_URL, deal_id)

        if not has_real_stage_change_on_day(history, target_day, LOCAL_TZ_NAME, ZoneInfo):
            ignored_no_real_stage_change += 1
            continue

        base_levels, base_reason, last_before_date, _ = levels_for_base_report(
            history, target_day, BASE_INACTIVITY_DAYS, LOCAL_TZ_NAME, ZoneInfo
        )
        is_base = (base_reason == "BASE_OK" and bool(base_levels))

        bucket = bucket_from_source_site(source_id)
        category_label = bucket

        # ---------- BASE ----------
        if is_base:
            add_levels(total_base, base_levels)
            if 5 in base_levels:
                add_booking(total_base, booking_method)

            base_counted_to = "БАЗА: " + ", ".join(LEVEL_NAMES[l] for l in sorted(base_levels))
            base_reason_text = f"Оживлення після паузи > {BASE_INACTIVITY_DAYS} днів (останній рух: {last_before_date})"

            rows.append({
                "Угода №": deal_id,
                "Номер телефона": phone,
                "Назва картки": title,
                "Поточний статус": f"{cat_now}:{stage_now}",
                "Джерело (ID)": source_id,
                "Джерело": source_name,
                "Термін": term_text,
                "Країна номера": region_label,
                "Категорія": category_label,
                "Спосіб запису": booking_method,
                "Результат": base_counted_to,
                "Причина / коментар": base_reason_text,
            })
            continue

        # ---------- DAY ----------
        day_levels, day_reason, _, _ = levels_gained_on_day(history, target_day, LOCAL_TZ_NAME, ZoneInfo)

        counted_to = ""
        reason_text = ""

        if day_levels:
            add_levels(total_day, day_levels)
            if 5 in day_levels:
                add_booking(total_day, booking_method)

            add_levels(day_region_category_source[region_group][category_label][source_name], day_levels)
            if 5 in day_levels:
                add_booking(day_region_category_source[region_group][category_label][source_name], booking_method)

            counted_to = "ДЕНЬ: " + ", ".join(LEVEL_NAMES[l] for l in sorted(day_levels))
        else:
            skipped[day_reason] += 1
            reason_text = day_reason

        rows.append({
            "Угода №": deal_id,
            "Номер телефона": phone,
            "Назва картки": title,
            "Поточний статус": f"{cat_now}:{stage_now}",
            "Джерело (ID)": source_id,
            "Джерело": source_name,
            "Термін": term_text,
            "Країна номера": region_label,
            "Категорія": category_label,
            "Спосіб запису": booking_method,
            "Результат": counted_to,
            "Причина / коментар": reason_text,
        })

    meta = {
        "deals_modified": len(deals_day),
        "ignored_no_real_stage_change": ignored_no_real_stage_change,
        "skipped_total": int(sum(skipped.values())),
        "skipped_reasons": dict(skipped),
    }

    return (total_day, total_base, day_region_category_source, rows, meta)
