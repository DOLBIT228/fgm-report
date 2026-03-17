import requests
import streamlit as st
import streamlit.components.v1 as components
import time
from datetime import datetime, date
from collections import Counter, defaultdict

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# ======================================================
# STREAMLIT PAGE
# ======================================================
st.set_page_config(page_title="FGM Daily Report", page_icon="📊", layout="wide")

def canonical_streamlit_url(url: str) -> str:
    """
    Streamlit migrated hosted apps from *.share.streamlit.io to *.streamlit.app.
    If a legacy URL leaks into config/browser history, it can trigger redirect loops.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    return raw.replace(".share.streamlit.io", ".streamlit.app")

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

DASHBOARD_URL = "https://panel-for-manager-call.streamlit.app/"
st.link_button("⬅ Назад до панелі менеджера", DASHBOARD_URL)
st.divider()

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
CAT_SITE = 47  # ✅ Сайт (окрема воронка)
CAT_ONLINE = 61
CAT_OFFLINE = 63
CAT_CHAT_SALES = 57
CAT_VG = 41
CAT_RINGS_TO_OTHER = 65  # Каблучки ЦА Ближчим часом

ALL_CATEGORIES = [CAT_CRM_FGM, CAT_SITE, CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG, CAT_RINGS_TO_OTHER]

APPOINTMENT_CATEGORIES = {CAT_ONLINE, CAT_OFFLINE, CAT_VG, CAT_RINGS_TO_OTHER}

TERM_FIELD = "UF_CRM_1749123119"              # "Термін" (list)
PHONE_REGION_FIELD = "UF_CRM_1765791110365"   # "Номер країни" (list)
BOOKING_METHOD_FIELD = "UF_CRM_1750870964613" # "Спосіб запису" (list)

PHONE_REGION_ENUM = {
    "54065": "Україна",
    "54067": "Закордон",
    "54069": "Немає номеру",
}

BOOKING_METHOD_ENUM = {
    "47063": "Дзвінок",
    "47065": "Повідомлення",
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
    # Закордон -> "Закордон", все інше -> "Україна"
    if raw is None:
        return "Україна"
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    return "Закордон" if val == "54067" else "Україна"

def phone_region_label_from_raw(raw) -> str:
    # Для рядка угоди (може бути "Немає номеру")
    if raw is None:
        return "Немає номеру"
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    return PHONE_REGION_ENUM.get(val, "Немає номеру")

def booking_method_from_raw(raw) -> str:
    # "" якщо пусте (без "Невідомо")
    if raw is None:
        return ""
    if isinstance(raw, list) and raw:
        raw = raw[0]
    val = str(raw).strip()
    if not val:
        return ""
    return BOOKING_METHOD_ENUM.get(val, "")

# ======================================================
# LEVELS
# ======================================================
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

# ======================================================
# STAGE -> LEVEL (INSTAGRAM / CRM FGM)
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

UNSUCCESSFUL_IGNORE_59 = {"C59:UC_QA06H6", "C59:WON", "C59:UC_A7YB3V", "C59:15"}
UNSUCCESSFUL_AS_TAKEN_59 = {"C59:LOSE"}
UNSUCCESSFUL_AS_CALL_59 = {"C59:APOLOGY", "C59:14", "C59:16", "C59:17", "C59:18"}

# ======================================================
# STAGE -> LEVEL (SITE funnel 47)
# ======================================================
SITE_STAGE_TO_LEVEL = {
    # Взято = Розсилка > ... > не додзвон (включно) (+ Немає в месенджерах)
    "C47:PREPARATION": 1,      # Розсилка
    "C47:PREPAYMENT_INVOIC": 1,# 2 дзвінок
    "C47:UC_FN3M0F": 1,        # 3 дзвінок
    "C47:EXECUTING": 1,        # не додзвон
    "C47:UC_D56N3S": 1,        # Немає в месенджерах

    # Дозвон
    "C47:UC_3PMDY3": 2,        # Додзвон

    # ЦА
    "C47:UC_U7J18A": 3,        # ЦА

    # Зацікавлені > Очікуємо бронювання
    "C47:UC_X314BU": 4,        # Зацікавлені
    "C47:UC_RYMD4E": 4,        # Очікуємо бронювання

    # Запис
    "C47:UC_K9ZT4D": 5,        # Запланована консультація
}

SITE_IGNORE = {
    "C47:NEW",          # Новий
    "C47:UC_DBKQMB",    # Подвійні
    "C47:WON",          # Успішна угода
}
SITE_AS_TAKEN = {"C47:LOSE"}  # Угода провалена
SITE_AS_CALL = {              # Не ЦА/Придбали/Не в пошуках — рахувати як "Дозвон"
    "C47:UC_GVG7E9",          # Не ЦА
    "C47:UC_KOEVQT",          # Придбали
    "C47:UC_0TGJPJ",          # Не в пошуках обручок
}

def level_from_stage(direction_key: str, category_id: int, stage_id: str) -> int:
    # direction_key: "instagram" або "site"
    if direction_key == "site":
        if stage_id in SITE_IGNORE:
            return 0
        if stage_id in SITE_AS_TAKEN:
            return 1
        if stage_id in SITE_AS_CALL:
            return 2
        return SITE_STAGE_TO_LEVEL.get(stage_id, 0)

    # instagram/default
    if category_id == CAT_CRM_FGM:
        if stage_id in UNSUCCESSFUL_IGNORE_59:
            return 0
        if stage_id in UNSUCCESSFUL_AS_TAKEN_59:
            return 1
        if stage_id in UNSUCCESSFUL_AS_CALL_59:
            return 2
        return CRM_STAGE_TO_LEVEL.get(stage_id, 0)

    if category_id == CAT_CHAT_SALES:
        return 4

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
    "Хочу каталог обручок","Хочу каталог каблучок","Ціна обручки","Ціна каблучки",
    "Консультація обручки","Консультація каблучки","Платина каблучки",
    "Даймонд Обручки","Даймонд Каблучки","Даймонд","Даймонд платина",
    "Класичний каталог","Каталог 375","Реклама Обручки","Обмін","Хочу додаток",
    "Самі прийшли","Адміністратор","Фейсбук","Реклама Каблучки","Інші прикраси",
    "По рекомендації друзів","З каблучок в обручки",
    "Обручки (сторіз)","Обручки (сторіз) - Даймонд","Каблучка (сторіз)",
    "Каблучка (сторіз) - Даймонд","Підбірка каблучок","Діамант ЧП 2025",
    "Розтермінування","2=1 ЧП 2025","Класика ЧП 2025","Платина ЧП 2025",
    "1 грам ЧП 2025","Каблучка Діаманти","Каблучка 2026","2 за 1 + розтермінування",
    "Даймонд 1 грам","Даймонд 2=1 2025","1 грам - інст","Вікторія Гарденс",
    "Платина Обручки","ТікТок","5 Діамант в подарунок інст",
    "Конструктор","Сертифікат каблучки","Сертифікат 1 грам обручки","1 грам золота",
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

def bucket_from_source_instagram(source_id: str, term_text: str) -> str:
    sname = source_name_from_id(source_id)
    if sname == "Лендинг" or sname in LANDING_SOURCE_NAMES:
        return None
    if sname in INSTAGRAM_SOURCE_NAMES:
        return "Інстаграм"
    if sname == "Чат-бот":
        return "Чат-бот"
    if sname == "Квіз обручки":
        return "Лідогенерація"
    if sname in TELEGRAM_SOURCE_NAMES:
        return "Телеграм"
    return "Інше"

def bucket_from_source_site(source_id: str) -> str:
    sname = source_name_from_id(source_id)
    # ✅ Сайт: "Лендинг" (джерело 24) -> bucket "Сайт"
    if sname == "Лендинг":
        return "Сайт"
    # ✅ всі інші "лендинги" -> bucket "Лендинг"
    if sname in LANDING_SOURCE_NAMES:
        return "Лендинг"
    return None

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
def fetch_all_deals(manager_id: int, categories: list[int]):
    params = {
        "filter[ASSIGNED_BY_ID]": manager_id,
        "filter[CATEGORY_ID][]": categories,
        "select[]": [
            "ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "DATE_MODIFY",
            "CONTACT_ID", "SOURCE_ID",
            TERM_FIELD, PHONE_REGION_FIELD, BOOKING_METHOD_FIELD,
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
        params = {
            "filter[ID][]": chunk,
            "select[]": ["ID", "PHONE"],
            "start": 0
        }
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

def max_levels_before_and_on_day(direction_key: str, history_rows, target_day: date):
    max_before = 0
    max_today = 0
    had_today = False

    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue

        cat = int(row.get("CATEGORY_ID", -1))
        stg = row.get("STAGE_ID", "")

        lvl = level_from_stage(direction_key, cat, stg)
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

def levels_gained_on_day(direction_key: str, history_rows, target_day: date):
    had_today, max_before, max_today = max_levels_before_and_on_day(direction_key, history_rows, target_day)

    if not had_today:
        return set(), "Не було змін статусів у цей день", max_before, max_today
    if max_today <= max_before:
        return set(), "Статус не піднявся вище (повторна робота)", max_before, max_today

    return set(range(max_before + 1, max_today + 1)), "OK", max_before, max_today

def moved_to_category_on_day(history_rows, target_day: date, category_id: int) -> bool:
    _, prev_key = last_stage_key_before_day(history_rows, target_day)
    prev_cat = prev_key[0] if prev_key else None

    day_rows = []
    for row in history_rows:
        dt = parse_dt(row.get("CREATED_TIME"))
        if not dt:
            continue
        if to_local_date(dt) == target_day:
            day_rows.append((dt, int(row.get("CATEGORY_ID", -1))))

    for _, cat in sorted(day_rows, key=lambda x: x[0]):
        if cat == category_id and prev_cat != category_id:
            return True
        prev_cat = cat

    return False

# ======================================================
# REPORT
# ======================================================
def build_report(manager_id: int, target_day: date, direction_key: str):
    categories = ALL_CATEGORIES

    all_deals = fetch_all_deals(manager_id, categories)
    deals_day = [d for d in all_deals if is_modified_on(d.get("DATE_MODIFY", ""), target_day)]

    contact_ids = []
    for d in deals_day:
        cid = d.get("CONTACT_ID")
        if cid:
            contact_ids.append(int(cid))
    phones_map = fetch_contacts_phones(contact_ids)

    term_enum_map = fetch_deal_userfield_enum_map(TERM_FIELD)

    total_day = empty_counts()
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
        source_name = source_name_from_id(source_id) or source_id or "Без джерела"

        term_raw = d.get(TERM_FIELD, "")
        term_text = term_text_from_raw(term_raw, term_enum_map)

        region_raw = d.get(PHONE_REGION_FIELD)
        region_group = phone_region_group_from_raw(region_raw)
        region_label = phone_region_label_from_raw(region_raw)

        booking_raw = d.get(BOOKING_METHOD_FIELD)
        booking_method = booking_method_from_raw(booking_raw)

        if direction_key == "site" and stage_now == "C47:UC_DBKQMB":
            continue

        if direction_key == "instagram":
            preview_bucket = bucket_from_source_instagram(source_id, term_text)
        else:
            preview_bucket = bucket_from_source_site(source_id)

        history = fetch_stagehistory(deal_id)
        moved_to_rings_today = moved_to_category_on_day(history, target_day, CAT_RINGS_TO_OTHER)

        if moved_to_rings_today:
            category_label = preview_bucket or "Інше"
            add_levels(total_day, {1, 2})
            add_levels(day_region_category_source[region_group][category_label][source_name], {1, 2})

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
                "Інстаграм сегмент": "",
                "Спосіб запису": booking_method,
                "Результат": "ДЕНЬ: Взято, Дозвон",
                "Причина / коментар": "CATEGORY 65: перехід у воронку Каблучки ЦА Ближчим часом",
            })
            continue

        if cat_now == CAT_RINGS_TO_OTHER:
            skipped["CATEGORY 65: не було переходу в цю воронку у цей день"] += 1
            continue

        if preview_bucket is None:
            continue

        if not has_real_stage_change_on_day(history, target_day):
            ignored_no_real_stage_change += 1
            continue

        if direction_key == "instagram":
            bucket = bucket_from_source_instagram(source_id, term_text)
            insta_term = instagram_term_segment(term_text) if bucket == "Інстаграм" else ""
            category_label = f"Інстаграм {insta_term}" if (bucket == "Інстаграм" and insta_term) else bucket
        else:
            bucket = bucket_from_source_site(source_id)
            insta_term = ""
            category_label = bucket

        if bucket is None:
            continue

        # ---------- DAY ----------
        day_levels, day_reason, _, _ = levels_gained_on_day(direction_key, history, target_day)

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
            continue

        rows.append({
            "Угода №": deal_id,
            "Номер телефона": phone,
            "Назва картки": title,
            "Поточний статус": f"{cat_now}:{stage_now}",
            "Джерело (ID)": source_id,
            "Джерело": source_name,
            "Термін": term_text,
            "Країна номера": region_label,
            "Категорія": bucket,
            "Інстаграм сегмент": insta_term,
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

    return (total_day, day_region_category_source, rows, meta)

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
def grouped_region_table(region_data: dict):
    out = []
    for category_label in sorted(region_data.keys(), key=lambda x: str(x)):
        sources = region_data.get(category_label, {})
        category_totals = empty_counts()
        for source_name in sorted(sources.keys(), key=lambda x: str(x)):
            counts = sources[source_name]
            for key in category_totals:
                category_totals[key] += counts.get(key, 0)
            out.append({
                "Джерело": f"{category_label} — {source_name}",
                "Взято": counts.get("Взято", 0),
                "Дозвон": counts.get("Дозвон", 0),
                "ЦА": counts.get("ЦА", 0),
                "Зацікавлені": counts.get("Зацікавлені", 0),
                "Запис": counts.get("Запис", 0),
                "В дзвінку": counts.get("В дзвінку", 0),
                "В повідомленнях": counts.get("В повідомленнях", 0),
            })

        if sources:
            out.append({
                "Джерело": f"{category_label} — Разом по категорії",
                "Взято": category_totals.get("Взято", 0),
                "Дозвон": category_totals.get("Дозвон", 0),
                "ЦА": category_totals.get("ЦА", 0),
                "Зацікавлені": category_totals.get("Зацікавлені", 0),
                "Запис": category_totals.get("Запис", 0),
                "В дзвінку": category_totals.get("В дзвінку", 0),
                "В повідомленнях": category_totals.get("В повідомленнях", 0),
            })
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

cols = st.columns([2, 2, 2, 4])
with cols[0]:
    st.metric("Менеджер", manager_name)
with cols[1]:
    target_day = st.date_input("Дата звіту", value=date.today())
with cols[2]:
    # ✅ ВИПАДАЮЧИЙ СПИСОК
    direction_ui = st.selectbox(
        "Напрямок",
        ["Інстаграм (CRM FGM)", "Сайт (воронка 47)"],
        index=0
    )
with cols[3]:
    st.caption("Звіт рахує лише реальні зміни статусів.")

direction_key = "instagram" if direction_ui.startswith("Інстаграм") else "site"

# ✅ Ключ звіту має включати напрямок
report_key = f"{manager_id}:{target_day.isoformat()}:{direction_key}"

if st.button("🔎 Сформувати звіт", type="primary"):
    t0 = time.time()
    with st.spinner("Формую звіт..."):
        report = build_report(manager_id, target_day, direction_key)
    elapsed = time.time() - t0
    st.session_state["report"] = report
    st.session_state["report_key"] = report_key
    st.session_state["report_elapsed"] = elapsed

if st.session_state.get("report_key") != report_key or "report" not in st.session_state:
    st.info("Натисніть «Сформувати звіт», щоб завантажити дані.")
    st.stop()

(total_day, day_region_category_source, rows, meta) = st.session_state["report"]

elapsed = st.session_state.get("report_elapsed")
if elapsed is not None:
    st.success(f"✅ Звіт сформовано за {elapsed:.1f} сек")

# --------------------------------------------------
# TOP TOTALS
# --------------------------------------------------
st.subheader("✅ Підсумок за день")
c = st.columns(7)
c[0].metric("Взято", total_day["Взято"])
c[1].metric("Дозвон", total_day["Дозвон"])
c[2].metric("ЦА", total_day["ЦА"])
c[3].metric("Зацікавлені", total_day["Зацікавлені"])
c[4].metric("Запис", total_day["Запис"])
c[5].metric("В дзвінку", total_day["В дзвінку"])
c[6].metric("В повідомленнях", total_day["В повідомленнях"])

st.divider()

# --------------------------------------------------
# УКРАЇНА / ЗАКОРДОН — ТАБЛИЦІ (ДЕНЬ)
# --------------------------------------------------
st.subheader("🇺🇦 Україна")
ua_data = day_region_category_source.get("Україна", {})
st.dataframe(grouped_region_table(ua_data), use_container_width=True)

st.subheader("🌍 Закордон")
foreign_data = day_region_category_source.get("Закордон", {})
st.dataframe(grouped_region_table(foreign_data), use_container_width=True)

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
    "Джерело (ID)", "Джерело", "Термін", "Країна номера", "Категорія",
    "Інстаграм сегмент", "Спосіб запису",
    "Результат", "Причина / коментар"
]
w = csv.DictWriter(buf, fieldnames=fieldnames)
w.writeheader()
for r in rows:
    w.writerow({k: r.get(k, "") for k in fieldnames})

st.download_button(
    "⬇️ Завантажити CSV",
    data=buf.getvalue().encode("utf-8"),
    file_name=f"report_{manager_name}_{target_day.isoformat()}_{direction_key}.csv",
    mime="text/csv",
)
