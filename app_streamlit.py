import hashlib
import requests
import streamlit as st
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

# users: dict login -> {name, manager_id, password}
USERS = get_secret("USERS", {})
if not USERS:
    st.error("Не задано USERS у secrets (логіни/паролі/ID менеджерів).")
    st.stop()


# ======================================================
# MANAGERS (fixed list)
# ======================================================
MANAGERS = {
    "Наталія Ледвій": 28217,
    "Анна Звада": 28267,
    "Марія Маськовіта": 28307,
    "Марія Деревецька": 28427,
    "Карина Хопта": 28423,
    "Олена Рудзік": 28279,
    "Катерина Романова": 28421,
}

# ======================================================
# CATEGORIES
# ======================================================
CAT_CRM_FGM = 59
CAT_ONLINE = 61
CAT_OFFLINE = 63
CAT_CHAT_SALES = 57
CAT_VG = 41
CAT_SITE = 47  # Сайт (окрема воронка)

CATEGORIES = [CAT_CRM_FGM, CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG, CAT_SITE]
APPOINTMENT_CATEGORIES = {CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG}

BASE_INACTIVITY_DAYS = 30

# Field "Термін" (ваше UF поле)
TERM_FIELD = "UF_CRM_1749123119"


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
# SOURCE MAP (Bitrix SOURCE directory mapping)
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
    "20": "База інстаграм",
    "22": "Класичний каталог",
    "UC_1HZ0KB": "Каталог 375",
    "23": "Конструктор",
    "UC_FYN3AR": "Реклама Обручки",
    "24": "Лендинг",
    "25": "Обмін",
    "26": "По рекомендації друзів",
    "27": "Самі прийшли",
    "28": "Адміністратор",
    "29": "Телеграм канал",
    "31": "Фейсбук",
    "33": "Хочу додаток",
    "UC_O6X5A5": "Чат-бот",
    "UC_38YOV1": "Реклама Каблучки",
    "UC_6NOQF2": "Інші прикраси",
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
    "UC_DHJKYW": "5 Діамант в подарунок інст",
    "UC_JL9RSA": "Лендинг -2=1",
    "UC_9FJEWZ": "Лендинг 1 грам",
    "UC_WEFXCG": "Лендинг Каблучки 100$",
    "34": "Лендинг Каблучки 1 грам",
    "35": "Лендинг 2 за 1 ОФФЕР",
    "UC_61JD9N": "Лендинг - стара ціна 2025",
}


def source_name_from_id(source_id: str) -> str:
    sid = str(source_id or "")
    return SOURCE_ID_TO_NAME.get(sid, sid or "")


# ======================================================
# Instagram sources list (ONE category "Інстаграм")
# ======================================================
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
    # ВАЖЛИВО: тепер це теж джерела Інстаграму
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

# ======================================================
# SEGMENTS for Instagram (term field)
# ======================================================
TERM_FUTURE_VALUES = {"Завчасно", "Без терміну", "На майбутнє", "На майбутнє ", "Без терміну ", "Завчасно "}


def segment_for_instagram(term_text: str) -> str:
    """
    Підрозбивка всередині Інстаграму:
    - "Ближчий час" якщо term == "Ближчим часом"
    - "Майбутнє" якщо term у {Завчасно, Без терміну, На майбутнє} або якщо будь-яке інше/порожнє
    """
    t = (term_text or "").strip()
    if t == "Ближчим часом":
        return "Ближчий час"
    if t in TERM_FUTURE_VALUES:
        return "Майбутнє"
    # якщо "На майбутнє" прилітає як інша форма/порожнє — теж трактуємо як майбутнє
    return "Майбутнє"


def bucket_from_source(source_id: str, term_value: str, is_base: bool, deal_category_id: int) -> str:
    """
    1) Якщо is_base -> "База"
    2) Якщо CATEGORY_ID=47 (Сайт):
       - SOURCE=Лендинг -> "Сайт"
       - інші "лендингові" джерела -> "Лендинг"
       - інакше -> "Інше"
    3) Якщо джерело входить у перелік Інстаграм -> "Інстаграм"
       (а підрозбивка робиться окремим полем Segment)
    4) Інакше: Сайт (SOURCE=Лендинг) / Лендинги / Чат-бот / Лідоген / Телеграм / Інше
    """
    if is_base:
        return "База"

    sid = str(source_id or "")
    sname = source_name_from_id(sid)

    # Спеціальна логіка тільки для воронки "Сайт" (CATEGORY_ID=47)
    if deal_category_id == CAT_SITE:
        if sname == "Лендинг":
            return "Сайт"
        if sname in LANDING_SOURCE_NAMES:
            return "Лендинг"
        return "Інше"

    # Основна логіка (CRM FGM / консультації)
    if sname in INSTAGRAM_SOURCE_NAMES:
        return "Інстаграм"

    if sname == "Лендинг":
        return "Сайт"

    if sname in LANDING_SOURCE_NAMES:
        return "Лендинг"

    if sname == "Чат-бот":
        return "Чат-бот"

    if sname == "Квіз обручки":
        return "Лідогенерація"

    if sname in TELEGRAM_SOURCE_NAMES:
        return "Телеграм"

    return "Інше"


# ======================================================
# STAGE MAPS
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

    "C59:2": 5,
}

# Воронка Сайт (CATEGORY_ID = 47) -> рівні
SITE_STAGE_TO_LEVEL = {
    # Початкові (не рахуємо)
    "C47:NEW": 0,  # Новий

    # 1) Взято (є робота, але ще не контакт)
    "C47:PREPARATION": 1,       # Розсилка
    "C47:PREPAYMENT_INVOIC": 1, # 2 дзвінок
    "C47:UC_FN3M0F": 1,         # 3 дзвінок
    "C47:EXECUTING": 1,         # не додзвон
    "C47:UC_D56N3S": 1,         # Немає в месенджерах

    # 2) Дозвон (контакт або фіксація неуспішності)
    "C47:UC_3PMDY3": 2,         # Додзвон
    "C47:UC_GVG7E9": 2,         # Не ЦА
    "C47:LOSE": 2,              # Угода провалена
    "C47:UC_KOEVQT": 2,         # Придбали
    "C47:UC_0TGJPJ": 2,         # Не в пошуках обручок

    # 3) ЦА
    "C47:UC_U7J18A": 3,

    # 4) Зацікавлені
    "C47:UC_X314BU": 4,         # Зацікавлені
    "C47:UC_RYMD4E": 4,         # Очікуємо бронювання

    # 5) Запис
    "C47:UC_K9ZT4D": 5,         # Запланована консультація

    # Не рахуємо
    "C47:UC_DBKQMB": 0,         # Подвійні
    "C47:WON": 0,               # Успішна угода
}

# Неуспішні статуси (CRM FGM 59)
UNSUCCESSFUL_IGNORE = {
    "C59:UC_QA06H6",  # Консультація не відбулася - нікуди
    "C59:WON",        # Успішна угода - нікуди
    "C59:UC_A7YB3V",  # Подвійні - нікуди
    "C59:15",         # Зустріч не відбулась - нікуди
}
UNSUCCESSFUL_AS_TAKEN = {
    "C59:LOSE",       # Недозвон - як Взято
}
UNSUCCESSFUL_AS_CALL = {
    "C59:APOLOGY",    # Просто переглянути - як Дозвон
    "C59:14",         # Не ЦА - як Дозвон
    "C59:16",         # Дорого - як Дозвон
    "C59:17",         # Вже купили - як Дозвон
    "C59:18",         # Бояться замовляти дистанційно - як Дозвон
}


def level_from_stage(category_id: int, stage_id: str) -> int:
    # CRM FGM (основна воронка Інстаграму)
    if category_id == CAT_CRM_FGM:
        if stage_id in UNSUCCESSFUL_IGNORE:
            return 0
        if stage_id in UNSUCCESSFUL_AS_TAKEN:
            return 1
        if stage_id in UNSUCCESSFUL_AS_CALL:
            return 2
        return CRM_STAGE_TO_LEVEL.get(stage_id, 0)

    # Сайт (окрема воронка)
    if category_id == CAT_SITE:
        return SITE_STAGE_TO_LEVEL.get(stage_id, 0)

    # Консультаційні воронки = "Запис"
    if category_id in APPOINTMENT_CATEGORIES:
        return 5

    return 0


# ======================================================
# BITRIX HELPERS
# ======================================================
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


# ======================================================
# FETCH: DEALS + STAGEHISTORY
# ======================================================
def fetch_all_deals(manager_id: int):
    params = {
        "filter[ASSIGNED_BY_ID]": manager_id,
        "filter[CATEGORY_ID][]": CATEGORIES,
        "select[]": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "DATE_MODIFY", "CONTACT_ID", "SOURCE_ID", TERM_FIELD],
        "start": 0,
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
        "start": 0,
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
# PHONE: CONTACT PHONE (batch)
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
        chunk = contact_ids[i : i + chunk_size]
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
# HISTORY ANALYSIS (avoid comment-only triggers)
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
# HELPERS: defaultdict -> dict (safe for cache pickle)
# ======================================================
def dd_counts_to_dict(dd):
    out = {}
    for k, v in dd.items():
        if isinstance(v, dict):
            out[k] = dict(v)
        else:
            out[k] = v
    return out


def dd2_counts_to_dict(dd2):
    out = {}
    for k, inner in dd2.items():
        out[k] = dd_counts_to_dict(inner)
    return out


# ======================================================
# REPORT
# ======================================================
@st.cache_data(ttl=60, show_spinner=False)
def build_report(manager_id: int, target_day: date):
    all_deals = fetch_all_deals(manager_id)
    deals_day = [d for d in all_deals if is_modified_on(d.get("DATE_MODIFY", ""), target_day)]

    contact_ids = []
    for d in deals_day:
        cid = d.get("CONTACT_ID")
        if cid:
            contact_ids.append(int(cid))
    phones_map = fetch_contacts_phones(contact_ids)

    total_day = empty_counts()
    total_base = empty_counts()

    # bucket totals
    day_by_bucket = defaultdict(lambda: empty_counts())
    base_by_bucket = defaultdict(lambda: empty_counts())

    # bucket -> source -> counts
    day_by_bucket_source = defaultdict(lambda: defaultdict(lambda: empty_counts()))
    base_by_bucket_source = defaultdict(lambda: defaultdict(lambda: empty_counts()))

    # Instagram segmented
    day_instagram_by_term = defaultdict(lambda: empty_counts())
    base_instagram_by_term = defaultdict(lambda: empty_counts())
    day_instagram_term_source = defaultdict(lambda: defaultdict(lambda: empty_counts()))
    base_instagram_term_source = defaultdict(lambda: defaultdict(lambda: empty_counts()))

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

        source_id = str(d.get("SOURCE_ID") or "")
        source_name = source_name_from_id(source_id)

        term_val = d.get(TERM_FIELD, "")
        if isinstance(term_val, dict):
            # sometimes UF could be returned like {"VALUE": "..."} in some configs
            term_val = term_val.get("VALUE", "")
        term_val = (term_val or "").strip()

        history = fetch_stagehistory(deal_id)

        # only real stage change
        if not has_real_stage_change_on_day(history, target_day):
            ignored_no_real_stage_change += 1
            continue

        # BASE first: if BASE_OK -> only in base, not in day
        base_levels, base_reason, last_before_date, base_today_lvl = levels_for_base_report(history, target_day)
        is_base = (base_reason == "BASE_OK" and bool(base_levels))

        bucket = bucket_from_source(source_id, term_val, is_base, cat_now)
        segment = segment_for_instagram(term_val) if bucket == "Інстаграм" else ""

        if is_base:
            add_levels(total_base, base_levels)
            add_levels(base_by_bucket[bucket], base_levels)
            add_levels(base_by_bucket_source[bucket][source_name], base_levels)

            if bucket == "Інстаграм":
                add_levels(base_instagram_by_term[segment], base_levels)
                add_levels(base_instagram_term_source[segment][source_name], base_levels)

            counted_to = "БАЗА: " + ", ".join(LEVEL_NAMES[l] for l in sorted(base_levels))
            reason_text = (
                f"Оживлення після паузи > {BASE_INACTIVITY_DAYS} днів (останній рух: {last_before_date})"
            )

            rows.append(
                {
                    "Угода №": deal_id,
                    "Номер телефона": phone,
                    "Назва картки": title,
                    "Категорія": cat_now,
                    "Джерело": source_name,
                    "Термін": term_val,
                    "Bucket": bucket,
                    "Segment": segment,
                    "Поточний статус": f"{cat_now}:{stage_now}",
                    "Результат": counted_to,
                    "Причина / коментар": reason_text,
                }
            )
            continue

        # Day report for non-base
        day_levels, day_reason, before, today_lvl = levels_gained_on_day(history, target_day)

        if day_levels:
            add_levels(total_day, day_levels)
            add_levels(day_by_bucket[bucket], day_levels)
            add_levels(day_by_bucket_source[bucket][source_name], day_levels)

            if bucket == "Інстаграм":
                add_levels(day_instagram_by_term[segment], day_levels)
                add_levels(day_instagram_term_source[segment][source_name], day_levels)

            counted_to = "ДЕНЬ: " + ", ".join(LEVEL_NAMES[l] for l in sorted(day_levels))
            reason_text = ""
        else:
            skipped[day_reason] += 1
            counted_to = ""
            reason_text = day_reason

        rows.append(
            {
                "Угода №": deal_id,
                "Номер телефона": phone,
                "Назва картки": title,
                "Категорія": cat_now,
                "Джерело": source_name,
                "Термін": term_val,
                "Bucket": bucket,
                "Segment": segment,
                "Поточний статус": f"{cat_now}:{stage_now}",
                "Результат": counted_to,
                "Причина / коментар": reason_text,
            }
        )

    meta = {
        "deals_modified": len(deals_day),
        "ignored_no_real_stage_change": ignored_no_real_stage_change,
        "skipped_total": int(sum(skipped.values())),
        "skipped_reasons": dict(skipped),
    }

    return (
        total_day,
        total_base,
        dd_counts_to_dict(day_by_bucket),
        dd_counts_to_dict(base_by_bucket),
        dd2_counts_to_dict(day_by_bucket_source),
        dd2_counts_to_dict(base_by_bucket_source),
        dd_counts_to_dict(day_instagram_by_term),
        dd_counts_to_dict(base_instagram_by_term),
        dd2_counts_to_dict(day_instagram_term_source),
        dd2_counts_to_dict(base_instagram_term_source),
        rows,
        meta,
    )


# ======================================================
# AUTH
# ======================================================
def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


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

def render_report(report_data, manager_name, target_day):
    (
        total_day,
        total_base,
        day_by_bucket,
        base_by_bucket,
        day_by_bucket_source,
        base_by_bucket_source,
        day_instagram_by_term,
        base_instagram_by_term,
        day_instagram_term_source,
        base_instagram_term_source,
        rows,
        meta,
    ) = report_data

    st.subheader("✅ Підсумок за день:")
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
    st.subheader("🧩 Деталізація по джерелах всередині категорій")

    st.markdown("### День: Bucket → Джерело")
    bucket_opt = ["(всі)"] + sorted(day_by_bucket_source.keys())
    chosen_bucket = st.selectbox("Bucket (День)", bucket_opt, index=0, key="bucket_day")
    show_buckets = sorted(day_by_bucket_source.keys()) if chosen_bucket == "(всі)" else [chosen_bucket]

    for bkt in show_buckets:
        st.markdown(f"#### {bkt}")
        src_dict = day_by_bucket_source.get(bkt, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append({
                "Джерело": src,
                "Взято": cnts["Взято"],
                "Дозвон": cnts["Дозвон"],
                "ЦА": cnts["ЦА"],
                "Зацікавлені": cnts["Зацікавлені"],
                "Запис": cnts["Запис"],
            })
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.markdown("### База: Bucket → Джерело")
    bucket_opt_b = ["(всі)"] + sorted(base_by_bucket_source.keys())
    chosen_bucket_b = st.selectbox("Bucket (База)", bucket_opt_b, index=0, key="bucket_base")
    show_buckets_b = sorted(base_by_bucket_source.keys()) if chosen_bucket_b == "(всі)" else [chosen_bucket_b]

    for bkt in show_buckets_b:
        st.markdown(f"#### {bkt}")
        src_dict = base_by_bucket_source.get(bkt, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append({
                "Джерело": src,
                "Взято": cnts["Взято"],
                "Дозвон": cnts["Дозвон"],
                "ЦА": cnts["ЦА"],
                "Зацікавлені": cnts["Зацікавлені"],
                "Запис": cnts["Запис"],
            })
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📷 Інстаграм — Термін → Джерело")

    st.markdown("### День (Інстаграм): Термін → Джерело")
    term_opts = ["(всі)"] + sorted(day_instagram_term_source.keys())
    chosen_term = st.selectbox("Термін (День / Інстаграм)", term_opts, index=0, key="ig_term_day")
    show_terms = sorted(day_instagram_term_source.keys()) if chosen_term == "(всі)" else [chosen_term]

    for t in show_terms:
        st.markdown(f"#### {t}")
        src_dict = day_instagram_term_source.get(t, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append({
                "Джерело": src,
                "Взято": cnts["Взято"],
                "Дозвон": cnts["Дозвон"],
                "ЦА": cnts["ЦА"],
                "Зацікавлені": cnts["Зацікавлені"],
                "Запис": cnts["Запис"],
            })
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.markdown("### База (Інстаграм): Термін → Джерело")
    term_opts_b = ["(всі)"] + sorted(base_instagram_term_source.keys())
    chosen_term_b = st.selectbox("Термін (База / Інстаграм)", term_opts_b, index=0, key="ig_term_base")
    show_terms_b = sorted(base_instagram_term_source.keys()) if chosen_term_b == "(всі)" else [chosen_term_b]

    for t in show_terms_b:
        st.markdown(f"#### {t}")
        src_dict = base_instagram_term_source.get(t, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append({
                "Джерело": src,
                "Взято": cnts["Взято"],
                "Дозвон": cnts["Дозвон"],
                "ЦА": cnts["ЦА"],
                "Зацікавлені": cnts["Зацікавлені"],
                "Запис": cnts["Запис"],
            })
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🧾 Деталізація по угодам")
    st.dataframe(rows, use_container_width=True)

    # CSV export
    import csv, io
    buf = io.StringIO()
    fieldnames = [
        "Угода №","Номер телефона","Назва картки","Категорія","Джерело","Термін",
        "Bucket","Segment","Поточний статус","Результат","Причина / коментар"
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

# ======================================================
# UI
# ======================================================
st.title("📊 Звіт менеджера:")

user = auth_block()
if not user:
    st.info("Увійдіть зліва (логін + пароль), щоб отримати звіт.")
    st.stop()

manager_id = int(user["manager_id"])
manager_name = user.get("name", "Менеджер")

cols = st.columns([2, 2, 3])
with cols[0]:
    st.metric("Менеджер", manager_name)
with cols[1]:
    target_day = st.date_input("Дата звіту", value=date.today())
with cols[2]:
    st.caption("Звіт рахує лише реальні зміни статусів. База — тільки після паузи > 30 днів.")

# Кнопка формування
if st.button("🔎 Сформувати звіт", type="primary"):
    with st.spinner("Формую звіт..."):
        report_data = build_report(manager_id, target_day)

    # зберігаємо, щоб при перемиканні selectbox нічого не скидалося
    st.session_state["report_data"] = report_data
    st.session_state["report_meta"] = {"manager_name": manager_name, "target_day": target_day}

# Якщо звіт вже сформовано раніше — показуємо його після будь-якого rerun
if "report_data" in st.session_state:
    meta = st.session_state.get("report_meta", {})
    render_report(
        st.session_state["report_data"],
        meta.get("manager_name", manager_name),
        meta.get("target_day", target_day),
    )
else:
    st.info("Натисніть «Сформувати звіт», щоб побачити результати.")

    st.subheader("✅ Підсумок за день:")
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

    st.subheader("🧩 Деталізація по джерелах всередині категорій")

    st.markdown("### День: Bucket → Джерело")
    bucket_opt = ["(всі)"] + sorted(day_by_bucket_source.keys())
    chosen_bucket = st.selectbox("Bucket (День)", bucket_opt, index=0, key="bucket_day")
    if chosen_bucket == "(всі)":
        show_buckets = sorted(day_by_bucket_source.keys())
    else:
        show_buckets = [chosen_bucket]

    for bkt in show_buckets:
        st.markdown(f"#### {bkt}")
        src_dict = day_by_bucket_source.get(bkt, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append(
                {
                    "Джерело": src,
                    "Взято": cnts["Взято"],
                    "Дозвон": cnts["Дозвон"],
                    "ЦА": cnts["ЦА"],
                    "Зацікавлені": cnts["Зацікавлені"],
                    "Запис": cnts["Запис"],
                }
            )
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.markdown("### База: Bucket → Джерело")
    bucket_opt_b = ["(всі)"] + sorted(base_by_bucket_source.keys())
    chosen_bucket_b = st.selectbox("Bucket (База)", bucket_opt_b, index=0, key="bucket_base")
    if chosen_bucket_b == "(всі)":
        show_buckets_b = sorted(base_by_bucket_source.keys())
    else:
        show_buckets_b = [chosen_bucket_b]

    for bkt in show_buckets_b:
        st.markdown(f"#### {bkt}")
        src_dict = base_by_bucket_source.get(bkt, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append(
                {
                    "Джерело": src,
                    "Взято": cnts["Взято"],
                    "Дозвон": cnts["Дозвон"],
                    "ЦА": cnts["ЦА"],
                    "Зацікавлені": cnts["Зацікавлені"],
                    "Запис": cnts["Запис"],
                }
            )
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("📷 Інстаграм — Термін → Джерело")

    st.markdown("### День (Інстаграм): Термін → Джерело")
    term_opts = ["(всі)"] + sorted(day_instagram_term_source.keys())
    chosen_term = st.selectbox("Термін (День / Інстаграм)", term_opts, index=0, key="ig_term_day")
    if chosen_term == "(всі)":
        show_terms = sorted(day_instagram_term_source.keys())
    else:
        show_terms = [chosen_term]

    for t in show_terms:
        st.markdown(f"#### {t}")
        src_dict = day_instagram_term_source.get(t, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append(
                {
                    "Джерело": src,
                    "Взято": cnts["Взято"],
                    "Дозвон": cnts["Дозвон"],
                    "ЦА": cnts["ЦА"],
                    "Зацікавлені": cnts["Зацікавлені"],
                    "Запис": cnts["Запис"],
                }
            )
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.markdown("### База (Інстаграм): Термін → Джерело")
    term_opts_b = ["(всі)"] + sorted(base_instagram_term_source.keys())
    chosen_term_b = st.selectbox("Термін (База / Інстаграм)", term_opts_b, index=0, key="ig_term_base")
    if chosen_term_b == "(всі)":
        show_terms_b = sorted(base_instagram_term_source.keys())
    else:
        show_terms_b = [chosen_term_b]

    for t in show_terms_b:
        st.markdown(f"#### {t}")
        src_dict = base_instagram_term_source.get(t, {})
        if not src_dict:
            st.caption("—")
            continue
        src_rows = []
        for src, cnts in sorted(src_dict.items(), key=lambda x: x[0]):
            src_rows.append(
                {
                    "Джерело": src,
                    "Взято": cnts["Взято"],
                    "Дозвон": cnts["Дозвон"],
                    "ЦА": cnts["ЦА"],
                    "Зацікавлені": cnts["Зацікавлені"],
                    "Запис": cnts["Запис"],
                }
            )
        st.dataframe(src_rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🧾 Деталізація по угодам")

    st.dataframe(rows, use_container_width=True)

    # CSV export
    import csv, io

    buf = io.StringIO()
    fieldnames = [
        "Угода №",
        "Номер телефона",
        "Назва картки",
        "Категорія",
        "Джерело",
        "Термін",
        "Bucket",
        "Segment",
        "Поточний статус",
        "Результат",
        "Причина / коментар",
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
