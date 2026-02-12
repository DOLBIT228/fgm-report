import requests
import streamlit as st
import time
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# ======================================================
# STREAMLIT PAGE
# ======================================================
st.set_page_config(page_title="FGM Daily Report", page_icon="📊", layout="wide")

# ======================================================
# CONFIG (from Streamlit secrets)
# ======================================================
def get_secret(name: str, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

WEBHOOK_URL = get_secret("WEBHOOK_URL", "")
LOCAL_TZ_NAME = get_secret("LOCAL_TZ_NAME", "Europe/Kyiv")

if not WEBHOOK_URL:
    st.error("Не задано WEBHOOK_URL у secrets. Додайте його в Streamlit secrets.")
    st.stop()

USERS = get_secret("USERS", {})
if not USERS:
    st.error("Не задано USERS у secrets (логіни/паролі/ID менеджерів).")
    st.stop()

# ======================================================
# CONSTANTS
# ======================================================
CAT_CRM_FGM = 59
CAT_ONLINE = 61
CAT_OFFLINE = 63
CAT_CHAT_SALES = 57
CAT_VG = 41

CATEGORIES = [CAT_CRM_FGM, CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG]
APPOINTMENT_CATEGORIES = {CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG}

BASE_INACTIVITY_DAYS = 30
TERM_FIELD = "UF_CRM_1749123119"  # поле "Термін" (list)
PHONE_REGION_FIELD = "UF_CRM_1765791110365"  # поле "Номер країни" (list)

# Спосіб запису (list)
APPOINTMENT_METHOD_FIELD = "UF_CRM_1750870964613"
APPOINTMENT_METHOD_ENUM = {
    "47063": "Дзвінок",
    "47065": "Повідомлення",
}

PHONE_REGION_ENUM = {
    "54065": "Україна",
    "54067": "Закордон",
    "54069": "Немає номеру",
}

# ======================================================
# HELPERS
# ======================================================
def _norm(s: str) -> str:
    return (s or "").strip().casefold()

def b24_get(method: str, params=None) -> dict:
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

def to_local_date(dt: datetime):
    if not dt:
        return None
    if ZoneInfo is None or dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(ZoneInfo(LOCAL_TZ_NAME)).date()

def is_modified_on(date_modify: str, target_day: date) -> bool:
    dt = parse_dt(date_modify)
    return dt and to_local_date(dt) == target_day

def phone_region_group_from_raw(raw) -> str:
    # Для таблиць: Закордон -> "Закордон", решта -> "Україна"
    if raw is None:
        return "Україна"
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    if val == "54067":
        return "Закордон"
    return "Україна"

def phone_region_label_from_raw(raw) -> str:
    # Для рядка угоди: може бути "Немає номеру"
    if raw is None:
        return "Немає номеру"
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    return PHONE_REGION_ENUM.get(val, "Немає номеру")

def appointment_method_from_raw(raw) -> str:
    if raw is None:
        return "Невідомо"
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, dict):
        raw = raw.get("VALUE") or raw.get("value") or ""
    val = str(raw).strip()
    if not val:
        return "Невідомо"
    return APPOINTMENT_METHOD_ENUM.get(val, f"Невідомо ({val})")

# ======================================================
# LEVELS
# ======================================================
LEVEL_NAMES = {1: "Взято", 2: "Дозвон", 3: "ЦА", 4: "Зацікавлені", 5: "Запис"}

def empty_counts():
    return {LEVEL_NAMES[i]: 0 for i in LEVEL_NAMES}

def add_levels(counter: dict, levels: set[int]):
    for lvl in sorted(levels):
        if lvl in LEVEL_NAMES:
            counter[LEVEL_NAMES[lvl]] += 1

# ======================================================
# STAGE -> LEVEL
# ======================================================
CRM_STAGE_TO_LEVEL = {
    "C59:UC_DN9449": 0,
    "C59:UC_IJZE1R": 0,

    "C59:NEW": 1,
    "C59:EXECUTING": 1,
    "C59:UC_25G325": 1,
    "C59:UC_G1DKQI": 1,
    "C59:UC_2118IT": 1,

    "C59:UC_XO1ZPS": 2,
    "C59:FINAL_INVOICE": 2,

    "C59:UC_XJ1V70": 3,

    "C59:UC_FDDLVQ": 4,
    "C59:1": 4,
    "C59:UC_PL0BXK": 4,
    "C59:UC_L3UWWD": 4,
    "C59:UC_MBXOE8": 4,
    "C59:UC_1H0IN4": 4,

    "C59:2": 5,
}

UNSUCCESSFUL_IGNORE = {
    "C59:UC_QA06H6",
    "C59:WON",
    "C59:UC_A7YB3V",
    "C59:15",
}
UNSUCCESSFUL_AS_TAKEN = {"C59:LOSE"}
UNSUCCESSFUL_AS_CALL = {"C59:APOLOGY", "C59:14", "C59:16", "C59:17", "C59:18"}

def level_from_stage(category_id: int, stage_id: str) -> int:
    if category_id == CAT_CRM_FGM:
        if stage_id in UNSUCCESSFUL_IGNORE:
            return 0
        if stage_id in UNSUCCESSFUL_AS_TAKEN:
            return 1
        if stage_id in UNSUCCESSFUL_AS_CALL:
            return 2
        return CRM_STAGE_TO_LEVEL.get(stage_id, 0)

    if category_id in APPOINTMENT_CATEGORIES:
        return 5

    return 0

# ======================================================
# SOURCES (ID -> NAME) + BUCKET RULES
# ======================================================
SOURCE_ID_TO_NAME = {
    "CALL": "Хочу каталог обручок",
    "WEBFORM": "Хочу каталог каблучок",
    "CALLBACK": "Ціна обручки",
    "RC_GENERATOR": "Ціна каблучки",
    "STORE": "Консультація обручки",
    "2|FACEBOOK": "Консультація каблучки",
    "12|TELEGRAM": "Платина каблучки",
    "UC_75HTWO": "Даймонд Обручки",
    "UC_SBD46Q": "Даймонд Каблучки",
    "REPEAT_SALE": "Даймонд",
    "17": "Даймонд платина",
    "18": "Сертифікат каблучки",
    "19": "Сертифікат 1 грам обручки",
    "22": "Класичний каталог",
    "UC_1HZ0KB": "Каталог 375",
    "23": "Конструктор",
    "UC_FYN3AR": "Реклама Обручки",
    "24": "Лендинг",
    "25": "Обмін",
    "27": "Самі прийшли",
    "28": "Адміністратор",
    "29": "Телеграм канал",
    "31": "Фейсбук",
    "33": "Хочу додаток",
    "UC_O6X5A5": "Чат-бот",
    "UC_38YOV1": "Реклама Каблучки",
    "UC_6NOQF2": "Інші прикраси",
    "26": "По рекомендації друзів",
    "UC_PA64VK": "З каблучок в обручки",
    "UC_FENQX0": "Обручки (сторіз)",
    "UC_C0TC57": "Обручки (сторіз) - Даймонд",
    "UC_3K0GOG": "Каблучка (сторіз)",
    "UC_LE749Q": "Каблучка (сторіз) - Даймонд",
    "UC_CVY9CE": "Підбірка каблучок",
    "UC_LKUF6G": "Діамант ЧП 2025",
    "UC_SSIUWU": "Розтермінування",
    "UC_WPFWO3": "2=1 ЧП 2025",
    "UC_YTU5Y0": "Класика ЧП 2025",
    "UC_IS25OB": "Платина ЧП 2025",
    "UC_4NEBL6": "1 грам ЧП 2025",
    "UC_4487XM": "Каблучка Діаманти",
    "UC_RHTET1": "Каблучка 2026",
    "UC_BVY6UB": "2 за 1 + розтермінування",
    "UC_A59GN4": "1 грам золота",
    "UC_EXVKWF": "Даймонд 1 грам",
    "UC_OQC3IZ": "Даймонд 2=1 2025",
    "UC_KTISNO": "1 грам - інст",
    "UC_H0FXZX": "Вікторія Гарденс",
    "UC_Q55M4S": "Платина Обручки",
    "UC_4ES7KL": "ТікТок",
    "UC_27P86X": "Квіз обручки",
    "UC_BCCISU": "5 Діаман в подарунок ТГ",
    "UC_DHJKYW": "5 Діамант в подарунок інст",
    "UC_JL9RSA": "Лендинг -2=1",
    "UC_9FJEWZ": "Лендинг 1 грам",
    "UC_WEFXCG": "Лендинг Каблучки 100$",
    "34": "Лендинг Каблучки 1 грам",
    "35": "Лендинг 2 за 1 ОФФЕР",
    "UC_61JD9N": "Лендинг - стара ціна 2025",
    "UC_UM9TLI": "Телеграм ширина ЧП 2025",
    "UC_WC44MV": "Телеграм діаманат ЧП 2025",
    "UC_ZCTGEP": "Телеграм платина ЧП 2025",
    "UC_MW9CGP": "Телеграм 2=1 ЧП 2025",
    "UC_CP8H2V": "Телеграм розтермінування ЧП 2025",
    "UC_J51RMG": "Телеграм 1 грам",
}

INSTAGRAM_SOURCE_NAMES = {
    "Хочу каталог обручок",
    "Хочу каталог каблучок",
    "Ціна обручки",
    "Ціна каблучки",
    "Консультація обручки",
    "Консультація каблучки",
    "Платина каблучки",
    "Даймонд Обручки",
    "Даймонд Каблучки",
    "Даймонд",
    "Даймонд платина",
    "Класичний каталог",
    "Каталог 375",
    "Реклама Обручки",
    "Обмін",
    "Хочу додаток",
    "Самі прийшли",
    "Адміністратор",
    "Фейсбук",
    "Реклама Каблучки",
    "Інші прикраси",
    "По рекомендації друзів",
    "З каблучок в обручки",
    "Обручки (сторіз)",
    "Обручки (сторіз) - Даймонд",
    "Каблучка (сторіз)",
    "Каблучка (сторіз) - Даймонд",
    "Підбірка каблучок",
    "Діамант ЧП 2025",
    "Розтермінування",
    "2=1 ЧП 2025",
    "Класика ЧП 2025",
    "Платина ЧП 2025",
    "1 грам ЧП 2025",
    "Каблучка Діаманти",
    "Каблучка 2026",
    "2 за 1 + розтермінування",
    "Даймонд 1 грам",
    "Даймонд 2=1 2025",
    "1 грам - інст",
    "Вікторія Гарденс",
    "Платина Обручки",
    "ТікТок",
    "5 Діамант в подарунок інст",
    "Конструктор",
    "Сертифікат каблучки",
    "Сертифікат 1 грам обручки",
    "1 грам золота",
}

LANDING_SOURCE_NAMES = {
    "Лендинг -2=1",
    "Лендинг 1 грам",
    "Лендинг Каблучки 100$",
    "Лендинг Каблучки 1 грам",
    "Лендинг 2 за 1 ОФФЕР",
    "Лендинг - стара ціна 2025",
}

TELEGRAM_SOURCE_NAMES = {
    "Телеграм канал",
    "Телеграм ширина ЧП 2025",
    "Телеграм діаманат ЧП 2025",
    "Телеграм платина ЧП 2025",
    "Телеграм 2=1 ЧП 2025",
    "Телеграм розтермінування ЧП 2025",
    "Телеграм 1 грам",
    "5 Діаман в подарунок ТГ",
}

def source_name_from_id(source_id: str) -> str:
    return SOURCE_ID_TO_NAME.get(str(source_id or "").strip(), "")

def bucket_from_source(source_id: str, term_text: str, is_base: bool) -> str:
    if is_base:
        return "База"

    sname = source_name_from_id(source_id)

    if sname in INSTAGRAM_SOURCE_NAMES:
        return "Інстаграм"
    if sname in LANDING_SOURCE_NAMES:
        return "Лендинг"
    if sname == "Чат-бот":
        return "Чат-бот"
    if sname == "Лендинг":
        return "Сайт"
    if sname == "Квіз обручки":
        return "Лідогенерація"
    if sname in TELEGRAM_SOURCE_NAMES:
        return "Телеграм"
    return "Інше"

def instagram_term_segment(term_text: str) -> str:
    return "Ближчий час" if _norm(term_text) == _norm("Ближчим часом") else "Майбутнє"

# ======================================================
# USERFIELD ENUM MAP (TERM)
# ======================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_deal_userfield_enum_map(field_name: str) -> dict:
    params = {"filter[FIELD_NAME]": field_name}
    data = b24_get("crm.deal.userfield.list", params)
    res = data.get("result", [])
    if isinstance(res, dict):
        res = [res]
    if not isinstance(res, list) or not res:
        return {}

    field = res[0] if isinstance(res[0], dict) else {}
    enum_list = field.get("LIST") or field.get("list") or []
    out = {}
    if isinstance(enum_list, list):
        for it in enum_list:
            if not isinstance(it, dict):
                continue
            eid = str(it.get("ID") or "").strip()
            val = str(it.get("VALUE") or "").strip()
            if eid:
                out[eid] = val
    return out

def term_text_from_raw(raw, enum_map: dict) -> str:
    if raw is None:
        return ""
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, dict):
        raw = raw.get("VALUE") or raw.get("value") or ""
    s = str(raw).strip()
    if not s:
        return ""
    return enum_map.get(s, s)

# ======================================================
# FETCH: DEALS + STAGEHISTORY
# ======================================================
def fetch_all_deals(manager_id: int):
    params = {
        "filter[ASSIGNED_BY_ID]": manager_id,
        "filter[CATEGORY_ID][]": CATEGORIES,
        "select[]": [
            "ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "DATE_MODIFY",
            "CONTACT_ID", "SOURCE_ID", TERM_FIELD, PHONE_REGION_FIELD, APPOINTMENT_METHOD_FIELD
        ],
        "start": 0
    }

    deals = []
    while True:
        data = b24_get("crm.deal.list", params)
        batch = data.get("result", [])
        if not batch:
            break
        deals.extend(batch)
        if data.get("next") is None:
            break
        params["start"] = data["next"]
    return deals

def fetch_stagehistory(deal_id: int, limit: int = 2000):
    params = {
        "entityTypeId": 2,
        "filter[OWNER_ID]": deal_id,
        "order[CREATED_TIME]": "ASC",
        "select[]": ["CREATED_TIME", "STAGE_ID", "CATEGORY_ID"],
        "start": 0
    }

    rows = []
    while True:
        data = b24_get("crm.stagehistory.list", params)
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

# ======================================================
# CONTACT PHONES
# ======================================================
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

def fetch_contacts_phones(contact_ids: list[int]) -> dict[int, str]:
    contact_ids = [int(x) for x in contact_ids if x]
    contact_ids = sorted(set(contact_ids))
    if not contact_ids:
        return {}

    phones = {}
    chunk_size = 50
    for i in range(0, len(contact_ids), chunk_size):
        chunk = contact_ids[i:i + chunk_size]
        params = {"filter[ID][]": chunk, "select[]": ["ID", "PHONE"], "start": 0}
        while True:
            data = b24_get("crm.contact.list", params)
            res = data.get("result", [])
            for c in res:
                cid = int(c.get("ID"))
                phones[cid] = normalize_phone(c.get("PHONE"))
            if data.get("next") is None:
                break
            params["start"] = data["next"]
    return phones

# ======================================================
# STAGEHISTORY ANALYSIS
# ======================================================
def last_stage_key_before_day(history_rows, target_day: date):
    last_dt = None
    last_key = None
    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        d = to_local_date(dt)
        if d is None or d >= target_day:
            continue
        if last_dt is None or dt > last_dt:
            last_dt = dt
            last_key = (int(row.get("CATEGORY_ID", -1)), row.get("STAGE_ID", ""))
    return last_dt, last_key

def has_real_stage_change_on_day(history_rows, target_day: date) -> bool:
    _, prev_key = last_stage_key_before_day(history_rows, target_day)

    if prev_key is None:
        for row in history_rows:
            dt = parse_dt(row.get("CREATED_TIME"))
            if not dt:
                continue
            if to_local_date(dt) == target_day:
                return True
        return False

    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        if to_local_date(dt) != target_day:
            continue
        key = (int(row.get("CATEGORY_ID", -1)), row.get("STAGE_ID", ""))
        if key != prev_key:
            return True

    return False

def last_stage_change_before_day(history_rows, target_day: date):
    last_dt = None
    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        d = to_local_date(dt)
        if d is None:
            continue
        if d < target_day:
            if last_dt is None or dt > last_dt:
                last_dt = dt
    return last_dt

def max_levels_before_and_on_day(history_rows, target_day: date):
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

        d = to_local_date(dt)
        if d is None:
            continue

        if d < target_day:
            max_before = max(max_before, lvl)
        elif d == target_day:
            had_today = True
            max_today = max(max_today, lvl)

    return had_today, max_before, max_today

def levels_gained_on_day(history_rows, target_day: date):
    had_today, max_before, max_today = max_levels_before_and_on_day(history_rows, target_day)

    if not had_today:
        return set(), "Не було змін статусів у цей день", max_before, max_today
    if max_today <= max_before:
        return set(), "Статус не піднявся вище (повторна робота)", max_before, max_today

    return set(range(max_before + 1, max_today + 1)), "OK", max_before, max_today

def levels_for_base_report(history_rows, target_day: date):
    cutoff = target_day - timedelta(days=BASE_INACTIVITY_DAYS)

    last_before = last_stage_change_before_day(history_rows, target_day)
    if not last_before:
        return set(), "База: немає історії до цього дня", None, 0

    last_before_date = to_local_date(last_before)
    if last_before_date is None or last_before_date > cutoff:
        return set(), "База: не було паузи > 30 днів", last_before_date, 0

    had_today, _, max_today = max_levels_before_and_on_day(history_rows, target_day)
    if not had_today or max_today <= 0:
        return set(), "База: у цей день не було статусного руху", last_before_date, max_today

    return set(range(1, max_today + 1)), "BASE_OK", last_before_date, max_today

# ======================================================
# REPORT
# ======================================================
def build_report(manager_id: int, target_day: date):
    all_deals = fetch_all_deals(manager_id)
    deals_day = [d for d in all_deals if is_modified_on(d.get("DATE_MODIFY", ""), target_day)]

    contact_ids = []
    for d in deals_day:
        cid = d.get("CONTACT_ID")
        if cid:
            contact_ids.append(int(cid))
    phones_map = fetch_contacts_phones(contact_ids)

    term_enum_map = fetch_deal_userfield_enum_map(TERM_FIELD)

    total_day = empty_counts()
    total_base = empty_counts()

    # Спосіб запису -> totals (ДЕНЬ/БАЗА)
    day_by_method = defaultdict(empty_counts)
    base_by_method = defaultdict(empty_counts)

    # region -> category_label -> method -> source_name -> counts
    day_region_category_method_source = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(empty_counts)))
    )

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
        source_name = source_name_from_id(source_id) or source_id or "Без джерела"

        term_raw = d.get(TERM_FIELD, "")
        term_text = term_text_from_raw(term_raw, term_enum_map)

        region_raw = d.get(PHONE_REGION_FIELD)
        region_group = phone_region_group_from_raw(region_raw)  # Україна / Закордон (для таблиць)
        region_label = phone_region_label_from_raw(region_raw)  # Україна / Закордон / Немає номеру (для рядка угоди)

        method_raw = d.get(APPOINTMENT_METHOD_FIELD)
        appointment_method = appointment_method_from_raw(method_raw)

        history = fetch_stagehistory(deal_id)

        if not has_real_stage_change_on_day(history, target_day):
            ignored_no_real_stage_change += 1
            continue

        base_levels, base_reason, last_before_date, _ = levels_for_base_report(history, target_day)
        is_base = (base_reason == "BASE_OK" and bool(base_levels))

        bucket = bucket_from_source(source_id, term_text, is_base)
        insta_term = instagram_term_segment(term_text) if bucket == "Інстаграм" else ""
        category_label = f"Інстаграм {insta_term}" if (bucket == "Інстаграм" and insta_term) else bucket

        # ---------- BASE ----------
        if is_base:
            add_levels(total_base, base_levels)
            add_levels(base_by_method[appointment_method], base_levels)

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
                "Спосіб запису": appointment_method,
                "Категорія": bucket,
                "Інстаграм сегмент": insta_term,
                "Результат": base_counted_to,
                "Причина / коментар": base_reason_text,
            })
            continue

        # ---------- DAY ----------
        day_levels, day_reason, _, _ = levels_gained_on_day(history, target_day)

        counted_to = ""
        reason_text = ""

        if day_levels:
            add_levels(total_day, day_levels)
            add_levels(day_by_method[appointment_method], day_levels)

            add_levels(
                day_region_category_method_source[region_group][category_label][appointment_method][source_name],
                day_levels
            )

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
            "Спосіб запису": appointment_method,
            "Категорія": bucket,
            "Інстаграм сегмент": insta_term,
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
        day_by_method,
        base_by_method,
        day_region_category_method_source,
        rows,
        meta
    )

# ======================================================
# AUTH
# ======================================================
def auth_block():
    st.sidebar.header("🔐 Вхід")
    login = st.sidebar.text_input("Логін")
    password = st.sidebar.text_input("Пароль", type="password")

    if st.sidebar.button("Увійти"):
        user = USERS.get(login)
        if not user:
            st.sidebar.error("Невірний логін або пароль")
            return None
        if password != user.get("password"):
            st.sidebar.error("Невірний логін або пароль")
            return None

        st.session_state["user"] = {"login": login, **dict(user)}
        st.sidebar.success(f"Вітаю, {user['name']}!")
        return st.session_state["user"]

    return st.session_state.get("user")

# ======================================================
# UI HELPERS
# ======================================================
def method_totals_table(d: dict):
    out = []
    for method_name in sorted(d.keys(), key=lambda x: str(x)):
        counts = d[method_name]
        out.append({
            "Спосіб запису": method_name,
            "Взято": counts.get("Взято", 0),
            "Дозвон": counts.get("Дозвон", 0),
            "ЦА": counts.get("ЦА", 0),
            "Зацікавлені": counts.get("Зацікавлені", 0),
            "Запис": counts.get("Запис", 0),
        })
    return out

def grouped_region_table(region_data: dict):
    """
    region_data = day_region_category_method_source["Україна"]
      -> {category_label: {method: {source_name: counts}}}
    """
    out = []
    for category_label in sorted(region_data.keys(), key=lambda x: str(x)):
        methods = region_data.get(category_label, {})
        first_cat = True
        for method in sorted(methods.keys(), key=lambda x: str(x)):
            sources = methods.get(method, {})
            first_method = True
            for source_name in sorted(sources.keys(), key=lambda x: str(x)):
                counts = sources[source_name]
                out.append({
                    "Категорія": category_label if first_cat else "",
                    "Спосіб запису": method if first_method else "",
                    "Джерело": source_name,
                    "Взято": counts.get("Взято", 0),
                    "Дозвон": counts.get("Дозвон", 0),
                    "ЦА": counts.get("ЦА", 0),
                    "Зацікавлені": counts.get("Зацікавлені", 0),
                    "Запис": counts.get("Запис", 0),
                })
                first_cat = False
                first_method = False
    return out

# ======================================================
# UI
# ======================================================
st.title("📊 Звіт менеджера")

user = auth_block()
if not user:
    st.info("Увійдіть зліва (логін + пароль), щоб отримати звіт.")
    st.stop()

manager_id = int(user["manager_id"])
manager_name = user.get("name", "Менеджер")

cols = st.columns([2, 2, 4])
with cols[0]:
    st.metric("Менеджер", manager_name)
with cols[1]:
    target_day = st.date_input("Дата звіту", value=date.today())
with cols[2]:
    st.caption("Звіт рахує лише реальні зміни статусів. База — тільки після паузи > 30 днів.")

report_key = f"{manager_id}:{target_day.isoformat()}"

if st.button("🔎 Сформувати звіт", type="primary"):
    t0 = time.time()
    with st.spinner("Формую звіт..."):
        report = build_report(manager_id, target_day)
    elapsed = time.time() - t0
    st.session_state["report"] = report
    st.session_state["report_key"] = report_key
    st.session_state["report_elapsed"] = elapsed

if st.session_state.get("report_key") != report_key or "report" not in st.session_state:
    st.info("Натисніть «Сформувати звіт», щоб завантажити дані.")
    st.stop()

(total_day, total_base, day_by_method, base_by_method, day_region_category_method_source, rows, meta) = st.session_state["report"]

elapsed = st.session_state.get("report_elapsed")
if elapsed is not None:
    st.success(f"✅ Звіт сформовано за {elapsed:.1f} сек")

# --------------------------------------------------
# TOP TOTALS
# --------------------------------------------------
st.subheader("✅ Підсумок за день")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Взято", total_day["Взято"])
c2.metric("Дозвон", total_day["Дозвон"])
c3.metric("ЦА", total_day["ЦА"])
c4.metric("Зацікавлені", total_day["Зацікавлені"])
c5.metric("Запис", total_day["Запис"])

st.subheader(f"🧱 Підсумок БАЗА (пауза > {BASE_INACTIVITY_DAYS} днів)")
b1, b2, b3, b4, b5 = st.columns(5)
b1.metric("Взято", total_base["Взято"])
b2.metric("Дозвон", total_base["Дозвон"])
b3.metric("ЦА", total_base["ЦА"])
b4.metric("Зацікавлені", total_base["Зацікавлені"])
b5.metric("Запис", total_base["Запис"])

st.divider()

# --------------------------------------------------
# СПОСІБ ЗАПИСУ — ДЕТАЛІЗАЦІЯ (ДЕНЬ/БАЗА)
# --------------------------------------------------
st.subheader("📞 Спосіб запису — День")
st.dataframe(method_totals_table(day_by_method), use_container_width=True)

st.subheader("📞 Спосіб запису — База")
st.dataframe(method_totals_table(base_by_method), use_container_width=True)

st.divider()

# --------------------------------------------------
# УКРАЇНА / ЗАКОРДОН — ТАБЛИЦІ (ДЕНЬ)
# --------------------------------------------------
st.subheader("🇺🇦 Україна")
ua_data = day_region_category_method_source.get("Україна", {})
ua_table = grouped_region_table(ua_data)
st.dataframe(ua_table, use_container_width=True)

st.subheader("🌍 Закордон")
foreign_data = day_region_category_method_source.get("Закордон", {})
foreign_table = grouped_region_table(foreign_data)
st.dataframe(foreign_table, use_container_width=True)

st.divider()

# --------------------------------------------------
# DEAL LIST
# --------------------------------------------------
st.subheader("🧾 Деталізація по угодам")
st.dataframe(rows, use_container_width=True)

# CSV export
import csv, io
buf = io.StringIO()
fieldnames = [
    "Угода №", "Номер телефона", "Назва картки", "Поточний статус",
    "Джерело (ID)", "Джерело", "Термін", "Країна номера", "Спосіб запису",
    "Категорія", "Інстаграм сегмент",
    "Результат", "Причина / коментар"
]
w = csv.DictWriter(buf, fieldnames=fieldnames)
w.writeheader()
for r in rows:
    w.writerow({k: r.get(k, "") for k in fieldnames})

st.download_button(
    "⬇️ Завантажити CSV",
    data=buf.getvalue().encode("utf-8"),
    file_name=f"report_{manager_name}_{target_day.isoformat()}.csv",
    mime="text/csv",
)
