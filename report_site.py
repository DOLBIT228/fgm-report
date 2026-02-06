import requests
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None


# =========================
# Constants for SITE funnel
# =========================
CAT_SITE = 47

# Appointment categories (treated as "Запис")
CAT_ONLINE = 61
CAT_OFFLINE = 63
CAT_CHAT_SALES = 57
CAT_VG = 41
APPOINTMENT_CATEGORIES = {CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG}

BASE_INACTIVITY_DAYS = 30

LEVEL_NAMES = {1: "Взято", 2: "Дозвон", 3: "ЦА", 4: "Зацікавлені", 5: "Запис"}


def empty_counts():
    return {LEVEL_NAMES[i]: 0 for i in LEVEL_NAMES}


def add_levels(counter: dict, levels: set[int]):
    for lvl in sorted(levels):
        if lvl in LEVEL_NAMES:
            counter[LEVEL_NAMES[lvl]] += 1


# =========================
# Stage mapping (CAT 47)
# =========================
# Логіка підрахунку:
# 1. Взято = Розсилка > Не дозвон (окремо Немає в месенджерах)
# 2. Дозвон = Додзвон (окремо Не ЦА, Угода провалена, Придбали, Не в пошуках обручок)
# 3. ЦА = ЦА
# 4. Зацікавлені = Зацікавлені > Очікуємо бронювання
# 5. Запис = Запланована консультація > Онлайн/Офлайн/Чат продаж/ВГ (через CATEGORY)

SITE_STAGE_TO_LEVEL = {
    # 0) не рахуємо
    "C47:NEW": 0,  # Новий

    # 1) Взято
    "C47:PREPARATION": 1,   # Розсилка
    "C47:EXECUTING": 1,     # не додзвон
    "C47:UC_D56N3S": 1,     # Немає в месенджерах

    # 2) Дозвон
    "C47:UC_3PMDY3": 2,     # Додзвон
    "C47:UC_GVG7E9": 2,     # Не ЦА
    "C47:LOSE": 2,          # Угода провалена
    "C47:UC_KOEVQT": 2,     # Придбали
    "C47:UC_0TGJPJ": 2,     # Не в пошуках обручок

    # 3) ЦА
    "C47:UC_U7J18A": 3,     # ЦА

    # 4) Зацікавлені
    "C47:UC_X314BU": 4,     # Зацікавлені
    "C47:UC_RYMD4E": 4,     # Очікуємо бронювання

    # 5) Запис (у воронці)
    "C47:UC_K9ZT4D": 5,     # Запланована консультація

    # Не рахуємо
    "C47:UC_DBKQMB": 0,     # Подвійні
    "C47:WON": 0,           # Успішна угода
    "C47:PREPAYMENT_INVOIC": 0,  # 2 дзвінок (не в рівнях)
    "C47:UC_FN3M0F": 0,          # 3 дзвінок (не в рівнях)
}


def level_from_stage(category_id: int, stage_id: str) -> int:
    if category_id == CAT_SITE:
        return SITE_STAGE_TO_LEVEL.get(stage_id, 0)
    if category_id in APPOINTMENT_CATEGORIES:
        return 5
    return 0


# =========================
# Sources mapping for SITE
# =========================
# SITE category: SOURCE_ID == "24" (Лендинг) -> bucket "Сайт"
# Landing category: specific landing sources -> bucket "Лендинг"

SOURCE_SITE = "24"  # Лендинг (основний сайт) -> bucket "Сайт"

LANDING_SOURCES = {
    "UC_JL9RSA", "UC_9FJEWZ", "UC_WEFXCG", "34", "35", "UC_61JD9N"
}


def site_bucket_from_source(source_id: str) -> str:
    s = (source_id or "").strip()
    if s == SOURCE_SITE:
        return "Сайт"
    if s in LANDING_SOURCES:
        return "Лендинг"
    return "Інше"


# =========================
# Bitrix helpers
# =========================
def b24_get(webhook_url: str, method: str, params=None) -> dict:
    url = f"{webhook_url}{method}"
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


def to_local_date(dt: datetime, local_tz_name: str):
    if not dt:
        return None
    if ZoneInfo is None or dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(ZoneInfo(local_tz_name)).date()


def is_modified_on(date_modify: str, target_day: date, local_tz_name: str) -> bool:
    dt = parse_dt(date_modify)
    return dt and to_local_date(dt, local_tz_name) == target_day


# =========================
# Fetch deals + stagehistory
# =========================
def fetch_all_deals(webhook_url: str, manager_id: int):
    params = {
        "filter[ASSIGNED_BY_ID]": manager_id,
        "filter[CATEGORY_ID][]": [CAT_SITE] + list(APPOINTMENT_CATEGORIES),
        "select[]": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "DATE_MODIFY", "CONTACT_ID", "SOURCE_ID"],
        "start": 0
    }

    deals = []
    while True:
        data = b24_get(webhook_url, "crm.deal.list", params)
        batch = data.get("result", [])
        if not batch:
            break
        deals.extend(batch)
        if data.get("next") is None:
            break
        params["start"] = data["next"]

    return deals


def fetch_stagehistory(webhook_url: str, deal_id: int, limit: int = 2000):
    params = {
        "entityTypeId": 2,
        "filter[OWNER_ID]": deal_id,
        "order[CREATED_TIME]": "ASC",
        "select[]": ["CREATED_TIME", "STAGE_ID", "CATEGORY_ID"],
        "start": 0
    }

    rows = []
    while True:
        data = b24_get(webhook_url, "crm.stagehistory.list", params)
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


# =========================
# Contacts phone (batch)
# =========================
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


def fetch_contacts_phones(webhook_url: str, contact_ids: list[int]) -> dict[int, str]:
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
            data = b24_get(webhook_url, "crm.contact.list", params)
            res = data.get("result", [])
            for c in res:
                cid = int(c.get("ID"))
                phones[cid] = normalize_phone(c.get("PHONE"))
            if data.get("next") is None:
                break
            params["start"] = data["next"]

    return phones


# =========================
# Stage-history analysis (same principles)
# =========================
def last_stage_key_before_day(history_rows, target_day: date, local_tz_name: str):
    last_dt = None
    last_key = None
    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        d = to_local_date(dt, local_tz_name)
        if d is None or d >= target_day:
            continue
        if last_dt is None or dt > last_dt:
            last_dt = dt
            last_key = (int(row.get("CATEGORY_ID", -1)), row.get("STAGE_ID", ""))
    return last_dt, last_key


def has_real_stage_change_on_day(history_rows, target_day: date, local_tz_name: str) -> bool:
    _, prev_key = last_stage_key_before_day(history_rows, target_day, local_tz_name)

    if prev_key is None:
        for row in history_rows:
            dt = parse_dt(row.get("CREATED_TIME"))
            if not dt:
                continue
            if to_local_date(dt, local_tz_name) == target_day:
                return True
        return False

    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        if to_local_date(dt, local_tz_name) != target_day:
            continue
        key = (int(row.get("CATEGORY_ID", -1)), row.get("STAGE_ID", ""))
        if key != prev_key:
            return True

    return False


def last_stage_change_before_day(history_rows, target_day: date, local_tz_name: str):
    last_dt = None
    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        d = to_local_date(dt, local_tz_name)
        if d is None:
            continue
        if d < target_day:
            if last_dt is None or dt > last_dt:
                last_dt = dt
    return last_dt


def max_levels_before_and_on_day(history_rows, target_day: date, local_tz_name: str):
    max_before = 0
    max_today = 0
    had_today = False

    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue

        cat = int(row.get("CATEGORY_ID", -1))
        stg = row.get("STAGE_ID", "")
        lvl = level_from_stage(cat, stg)
        if lvl <= 0:
            continue

        d = to_local_date(dt, local_tz_name)
        if d is None:
            continue

        if d < target_day:
            max_before = max(max_before, lvl)
        elif d == target_day:
            had_today = True
            max_today = max(max_today, lvl)

    return had_today, max_before, max_today


def levels_gained_on_day(history_rows, target_day: date, local_tz_name: str):
    had_today, max_before, max_today = max_levels_before_and_on_day(history_rows, target_day, local_tz_name)

    if not had_today:
        return set(), "Не було змін статусів у цей день", max_before, max_today
    if max_today <= max_before:
        return set(), "Статус не піднявся вище (повторна робота)", max_before, max_today

    return set(range(max_before + 1, max_today + 1)), "OK", max_before, max_today


def levels_for_base_report(history_rows, target_day: date, local_tz_name: str):
    cutoff = target_day - timedelta(days=BASE_INACTIVITY_DAYS)

    last_before = last_stage_change_before_day(history_rows, target_day, local_tz_name)
    if not last_before:
        return set(), "База: немає історії до цього дня", None, 0

    last_before_date = to_local_date(last_before, local_tz_name)
    if last_before_date is None or last_before_date > cutoff:
        return set(), "База: не було паузи > 30 днів", last_before_date, 0

    had_today, _, max_today = max_levels_before_and_on_day(history_rows, target_day, local_tz_name)
    if not had_today or max_today <= 0:
        return set(), "База: у цей день не було статусного руху", last_before_date, max_today

    return set(range(1, max_today + 1)), "BASE_OK", last_before_date, max_today


# =========================
# Build SITE report
# =========================
def build_report_site(webhook_url: str, local_tz_name: str, manager_id: int, target_day: date):
    all_deals = fetch_all_deals(webhook_url, manager_id)
    deals_day = [d for d in all_deals if is_modified_on(d.get("DATE_MODIFY", ""), target_day, local_tz_name)]

    contact_ids = []
    for d in deals_day:
        cid = d.get("CONTACT_ID")
        if cid:
            contact_ids.append(int(cid))
    phones_map = fetch_contacts_phones(webhook_url, contact_ids)

    total_day = empty_counts()
    total_base = empty_counts()

    day_by_bucket = defaultdict(empty_counts)
    base_by_bucket = defaultdict(empty_counts)

    day_by_bucket_source = defaultdict(lambda: defaultdict(empty_counts))
    base_by_bucket_source = defaultdict(lambda: defaultdict(empty_counts))

    skipped = Counter()
    ignored_no_real_stage_change = 0

    rows = []

    for d in deals_day:
        deal_id = int(d.get("ID"))
        title = d.get("TITLE", "—")
        cat_now = int(d.get("CATEGORY_ID", -1))
        stage_now = d.get("STAGE_ID", "")
        source_id = str(d.get("SOURCE_ID") or "").strip()

        contact_id = int(d.get("CONTACT_ID") or 0)
        phone = phones_map.get(contact_id, "")

        bucket = site_bucket_from_source(source_id)
        source_label = source_id or "(порожньо)"

        history = fetch_stagehistory(webhook_url, deal_id)

        if not has_real_stage_change_on_day(history, target_day, local_tz_name):
            ignored_no_real_stage_change += 1
            continue

        # BASE first: if BASE_OK -> only in base
        base_levels, base_reason, last_before_date, _ = levels_for_base_report(history, target_day, local_tz_name)
        is_base = (base_reason == "BASE_OK" and bool(base_levels))

        if is_base:
            add_levels(total_base, base_levels)
            add_levels(base_by_bucket[bucket], base_levels)
            add_levels(base_by_bucket_source[bucket][source_label], base_levels)

            counted_to = "БАЗА: " + ", ".join(LEVEL_NAMES[l] for l in sorted(base_levels))
            reason_text = f"Оживлення після паузи > {BASE_INACTIVITY_DAYS} днів (останній рух: {last_before_date})"

            rows.append({
                "Угода №": deal_id,
                "Номер телефона": phone,
                "Назва картки": title,
                "Категорія": "Сайт (47)",
                "Джерело": source_label,
                "Bucket": bucket,
                "Поточний статус": f"{cat_now}:{stage_now}",
                "Результат": counted_to,
                "Причина / коментар": reason_text,
            })
            continue

        # Day report
        day_levels, day_reason, _, _ = levels_gained_on_day(history, target_day, local_tz_name)

        if day_levels:
            add_levels(total_day, day_levels)
            add_levels(day_by_bucket[bucket], day_levels)
            add_levels(day_by_bucket_source[bucket][source_label], day_levels)
            counted_to = "ДЕНЬ: " + ", ".join(LEVEL_NAMES[l] for l in sorted(day_levels))
            reason_text = ""
        else:
            skipped[day_reason] += 1
            counted_to = ""
            reason_text = day_reason

        rows.append({
            "Угода №": deal_id,
            "Номер телефона": phone,
            "Назва картки": title,
            "Категорія": "Сайт (47)",
            "Джерело": source_label,
            "Bucket": bucket,
            "Поточний статус": f"{cat_now}:{stage_now}",
            "Результат": counted_to,
            "Причина / коментар": reason_text,
        })

    meta = {
        "deals_modified": len(deals_day),
        "ignored_no_real_stage_change": ignored_no_real_stage_change,
        "skipped_total": int(sum(skipped.values())),
        "skipped_reasons": dict(skipped),
    }

    return (
        total_day,
        total_base,
        dict(day_by_bucket),
        dict(base_by_bucket),
        {b: dict(s) for b, s in day_by_bucket_source.items()},
        {b: dict(s) for b, s in base_by_bucket_source.items()},
        rows,
        meta,
    )