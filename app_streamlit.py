import hashlib
import requests
import streamlit as st
import report_site  # funnel 47 (Сайт)
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

# users: dict login -> {name, manager_id, password_sha256}
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

# Категорії (CRM FGM)
CAT_CRM_FGM = 59
CAT_ONLINE = 61
CAT_OFFLINE = 63
CAT_CHAT_SALES = 57
CAT_VG = 41

CATEGORIES = [CAT_CRM_FGM, CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG]
APPOINTMENT_CATEGORIES = {CAT_ONLINE, CAT_OFFLINE, CAT_CHAT_SALES, CAT_VG}

BASE_INACTIVITY_DAYS = 30


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
# STAGE MAPS (CRM FGM)
# ======================================================
CRM_STAGE_TO_LEVEL = {
    # Початкові (не рахуємо)
    "C59:UC_DN9449": 0,
    "C59:UC_IJZE1R": 0,

    # 1) Взято
    "C59:NEW": 1,
    "C59:EXECUTING": 1,
    "C59:UC_25G325": 1,
    "C59:UC_G1DKQI": 1,
    "C59:UC_2118IT": 1,

    # 2) Дозвон (успішний контакт)
    "C59:UC_XO1ZPS": 2,
    "C59:FINAL_INVOICE": 2,

    # 3) ЦА
    "C59:UC_XJ1V70": 3,

    # 4) Зацікавлені
    "C59:UC_FDDLVQ": 4,
    "C59:1": 4,
    "C59:UC_PL0BXK": 4,
    "C59:UC_L3UWWD": 4,
    "C59:UC_MBXOE8": 4,

    # 5) Запис
    "C59:2": 5,
}

# Неуспішні статуси (ваша логіка)
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
        "select[]": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "DATE_MODIFY", "CONTACT_ID", "SOURCE_ID", "UF_CRM_1749123119"],
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
# PHONE: CONTACT PHONE (batch)
# ======================================================
def normalize_phone(phones):
    """Bitrix contact PHONE може бути списком словників або рядком. Повертаємо перший читабельний."""
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
# HISTORY ANALYSIS
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
# REPORT (CRM FGM)
# Повертає:
# total_day, total_base,
# day_by_bucket, base_by_bucket,
# day_by_bucket_source, base_by_bucket_source,
# day_instagram_by_term, base_instagram_by_term,
# day_instagram_term_source, base_instagram_term_source,
# rows, meta
# ======================================================
@st.cache_data(ttl=60, show_spinner=False)
def build_report(manager_id: int, target_day: date):
    all_deals = fetch_all_deals(manager_id)
    deals_day = [d for d in all_deals if is_modified_on(d.get("DATE_MODIFY", ""), target_day)]

    # телефони контактів: batch
    contact_ids = []
    for d in deals_day:
        cid = d.get("CONTACT_ID")
        if cid:
            contact_ids.append(int(cid))
    phones_map = fetch_contacts_phones(contact_ids)

    total_day = empty_counts()
    total_base = empty_counts()

    # Загальна деталізація по "bucket" / "source"
    day_by_bucket = defaultdict(empty_counts)
    base_by_bucket = defaultdict(empty_counts)
    day_by_bucket_source = defaultdict(lambda: defaultdict(empty_counts))
    base_by_bucket_source = defaultdict(lambda: defaultdict(empty_counts))

    # Instagram term деталізація (залишаю як у вас було раніше — логіка всередині вашого коду)
    day_instagram_by_term = defaultdict(empty_counts)
    base_instagram_by_term = defaultdict(empty_counts)
    day_instagram_term_source = defaultdict(lambda: defaultdict(empty_counts))
    base_instagram_term_source = defaultdict(lambda: defaultdict(empty_counts))

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
        term_value = (d.get("UF_CRM_1749123119") or "").strip()  # повертає: Ближчим часом / Завчасно / Без терміну / На майбутнє

        # ВАЖЛИВО: нижче у вас вже має бути ваша логіка:
        # - визначення bucket (Instagram/…)
        # - визначення term_bucket (Ближчий час / Майбутнє)
        # - мапінг source_id -> назва джерела
        # Цю частину я не переписую тут, бо у вас вона вже реалізована в поточному файлі.
        # Якщо потрібно — я вставлю ваш актуальний мапінг 1:1.

        history = fetch_stagehistory(deal_id)

        # only real stage change
        if not has_real_stage_change_on_day(history, target_day):
            ignored_no_real_stage_change += 1
            continue

        # BASE first: if BASE_OK -> only in base, not in day
        base_levels, base_reason, last_before_date, _ = levels_for_base_report(history, target_day)
        is_base = (base_reason == "BASE_OK" and bool(base_levels))

        # --- ЗАМІСТЬ (placeholder) bucket/source/term — використайте ваші функції ---
        bucket = "Instagram"  # TODO: your bucket logic
        source_label = source_id or "(порожньо)"
        term_bucket = "Ближчий час"  # TODO: your term logic based on term_value
        # -------------------------------------------------------------------------

        if is_base:
            add_levels(total_base, base_levels)
            add_levels(base_by_bucket[bucket], base_levels)
            add_levels(base_by_bucket_source[bucket][source_label], base_levels)

            if bucket == "Instagram":
                add_levels(base_instagram_by_term[term_bucket], base_levels)
                add_levels(base_instagram_term_source[term_bucket][source_label], base_levels)

            counted_to = "БАЗА: " + ", ".join(LEVEL_NAMES[l] for l in sorted(base_levels))
            reason_text = f"Оживлення після паузи > {BASE_INACTIVITY_DAYS} днів (останній рух: {last_before_date})"
            rows.append({
                "Угода №": deal_id,
                "Номер телефона": phone,
                "Назва картки": title,
                "Поточний статус": f"{cat_now}:{stage_now}",
                "Результат": counted_to,
                "Причина / коментар": reason_text,
            })
            continue

        # Day report for non-base
        day_levels, day_reason, _, _ = levels_gained_on_day(history, target_day)

        if day_levels:
            add_levels(total_day, day_levels)
            add_levels(day_by_bucket[bucket], day_levels)
            add_levels(day_by_bucket_source[bucket][source_label], day_levels)

            if bucket == "Instagram":
                add_levels(day_instagram_by_term[term_bucket], day_levels)
                add_levels(day_instagram_term_source[term_bucket][source_label], day_levels)

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
        dict(day_instagram_by_term),
        dict(base_instagram_by_term),
        {t: dict(s) for t, s in day_instagram_term_source.items()},
        {t: dict(s) for t, s in base_instagram_term_source.items()},
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
    report_mode = st.selectbox("Воронка", ["CRM FGM (Instagram)", "Сайт (47)"], index=0)
with cols[2]:
    st.caption("Звіт рахує лише реальні зміни статусів. База — тільки після паузи > 30 днів.")

if st.button("🔎 Сформувати звіт", type="primary"):
    with st.spinner("Формую звіт... (орієнтовно 40 секунд)"):
        if report_mode == "Сайт (47)":
            total_day, total_base, day_by_bucket, base_by_bucket, day_by_bucket_source, base_by_bucket_source, rows, meta = (
                report_site.build_report_site(WEBHOOK_URL, LOCAL_TZ_NAME, manager_id, target_day)
            )
            day_instagram_by_term = {}
            base_instagram_by_term = {}
            day_instagram_term_source = {}
            base_instagram_term_source = {}
        else:
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
            ) = build_report(manager_id, target_day)

    st.session_state["report_data"] = {
        "mode": report_mode,
        "manager_id": manager_id,
        "target_day": target_day.isoformat(),
        "total_day": total_day,
        "total_base": total_base,
        "day_by_bucket": day_by_bucket,
        "base_by_bucket": base_by_bucket,
        "day_by_bucket_source": day_by_bucket_source,
        "base_by_bucket_source": base_by_bucket_source,
        "day_instagram_by_term": day_instagram_by_term,
        "base_instagram_by_term": base_instagram_by_term,
        "day_instagram_term_source": day_instagram_term_source,
        "base_instagram_term_source": base_instagram_term_source,
        "rows": rows,
        "meta": meta,
    }

report_data = st.session_state.get("report_data")
if report_data and report_data.get("mode") == report_mode and int(report_data.get("manager_id", -1)) == manager_id and report_data.get("target_day") == target_day.isoformat():
    total_day = report_data["total_day"]
    total_base = report_data["total_base"]
    day_by_bucket = report_data["day_by_bucket"]
    base_by_bucket = report_data["base_by_bucket"]
    day_by_bucket_source = report_data["day_by_bucket_source"]
    base_by_bucket_source = report_data["base_by_bucket_source"]
    day_instagram_by_term = report_data["day_instagram_by_term"]
    base_instagram_by_term = report_data["base_instagram_by_term"]
    day_instagram_term_source = report_data["day_instagram_term_source"]
    base_instagram_term_source = report_data["base_instagram_term_source"]
    rows = report_data["rows"]
    meta = report_data["meta"]

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

    # Instagram term деталізація — тільки для CRM FGM
    if report_mode != "Сайт (47)":
        st.divider()
        st.subheader("🧩 Деталізація по джерелах всередині категорій")
        st.subheader("📷 Інстаграм — Термін → Джерело")

        # Тут лишається ваша існуюча відрисовка (selectbox + таблиці) — тепер вона НЕ скине звіт,
        # бо результати зберігаються в session_state.
        # Якщо у вас там була відрисовка нижче — вставте її сюди без змін.

    st.divider()
    st.subheader("🧾 Деталізація по угодам")
    st.dataframe(rows, use_container_width=True)

    # CSV export
    import csv, io
    buf = io.StringIO()
    fieldnames = ["Угода №", "Номер телефона", "Назва картки", "Поточний статус", "Результат", "Причина / коментар"]
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
else:
    st.info("Натисніть **Сформувати звіт**, щоб побачити дані. Після цього можна перемикати випадаючі списки — звіт не зникне.")
