"""Класифікатор причин падіння install.

Це найважливіший файл проєкту. Якщо не розділяти «модуль несумісний» від
«на машині немає python-пакета» — цифрам ніхто не повірить, і весь індекс нічого не вартий.
"""
import re

# Порядок має значення: перший збіг виграє.
RULES = [
    # --- не вина модуля: інфраструктура ---
    ("env_missing_python", r"ModuleNotFoundError: No module named '([\w\.]+)'",
     "Немає зовнішнього python-пакета: {0}"),
    ("env_missing_python", r"Unmet external Python dependencies?:?\s*(.+)",
     "Незадоволена зовнішня python-залежність: {0}"),
    ("env_binary", r"(?:wkhtmltopdf|OSError: \[Errno 2\].*)",
     "Немає системної утиліти в образі"),
    ("env_db", r"(could not connect to server|FATAL:\s+.*database|OperationalError)",
     "Проблема з підключенням до БД"),
    ("timeout", r"__RUNNER_TIMEOUT__", "Прогін перевищив ліміт часу"),

    # --- залежності в межах Odoo ---
    ("dep_missing_module", r"module (\S+) not found|Some modules are not loaded, some dependencies or manifest may be missing: \[?'?([\w_]+)",
     "Залежний модуль недоступний у цій серії: {0}"),
    ("dep_uninstallable", r"'installable': False|module is not installable",
     "Модуль позначений як installable=False"),

    # --- справжня несумісність з версією ---
    # assets/OWL перевіряємо ПЕРЕД загальним XML: типова поломка на 17→18→19
    ("assets_owl", r"(web\.assets_\w+|assets_backend|assets_frontend|OwlError|owl\b|template .{0,60} not found)",
     "Зміни в OWL / asset bundle: {0}"),
    ("views_xml", r"(ParseError|Invalid view|cannot be located in parent view|External ID not found in the system: ([\w\.]+))",
     "Помилка в XML або представленні: {0}"),
    ("orm_api", r"(unexpected keyword argument '(\w+)'|has no attribute '(\w+)'|TypeError: .{0,80}\(\) (?:missing|takes))",
     "Змінений ORM / Python API: {0}"),
    ("field_removed", r"(Field .{0,60} does not exist|Unknown field ([\w\.]+)|column \"?(\w+)\"? does not exist)",
     "Поле відсутнє в цій версії: {0}"),
    ("access_model", r"(Model .* does not exist|Invalid model name|_inherit .* not found)",
     "Модель відсутня або перейменована"),
    ("sql_error", r"(psycopg2\.\w+Error|IntegrityError|ProgrammingError)",
     "Помилка SQL при установці"),
    ("py_syntax", r"(SyntaxError|IndentationError)", "Синтаксична помилка python"),
    ("registry", r"Failed to (load|initialize) registry", "Реєстр не зібрався"),
]

WARN_PATTERNS = [
    (r"DeprecationWarning|is deprecated", "Використовує застарілий API"),
    (r"WARNING .*not found in|WARNING .*invalid", "Попередження при завантаженні"),
]

# Рядки, які описують НАШ харнес або рендер README, а не результат install.
# Без цього фільтра WARN_PATTERNS ловить наші ж попередження і кожен модуль
# отримує warn замість ok. Перевірено 19.08.2026 на odoo:19.0: у чистому
# успішному прогоні весь лог — це рівно ці рядки.
#   odoo.tools.config — наші прапорці й addons-path ('/mnt/extra-addons' з образу,
#                       '--without-demo=all' застарів у 19.0)
#   <string>:N:       — docutils при рендері опису модуля
NOISE = re.compile(r"odoo\.tools\.config|^<string>:\d+:")

# Модуль не знайдено в addons-path — Odoo друкує це і виходить з кодом 0.
# Тобто install НЕ відбувався, а зовні виглядає як успіх. Це збій харнесу,
# не властивість модуля, тому статус env і в несумісність НЕ зараховується.
IGNORED = re.compile(r"invalid module names, ignored:\s*(.+)")


def _denoise(log: str) -> str:
    return "\n".join(l for l in log.splitlines() if not NOISE.search(l))


def classify(log: str, returncode: int, timed_out: bool = False):
    """→ (status, cause, detail)

    status: ok | warn | dep | env | fail | timeout
    Розділення status для env і dep критичне: це НЕ несумісність модуля.
    """
    if timed_out:
        return "timeout", "timeout", "Прогін перевищив ліміт часу"

    text = log[-200_000:] if len(log) > 200_000 else log
    text = _denoise(text)

    # Перевіряємо ДО коду виходу: тут rc=0 не означає успіху.
    m = IGNORED.search(text)
    if m:
        return ("env", "env_module_not_found",
                f"Модуль не знайдено в addons-path: {m.group(1).strip()[:160]}")

    if returncode == 0:
        for pat, msg in WARN_PATTERNS:
            if re.search(pat, text):
                return "warn", "deprecated", msg
        return "ok", None, None

    for cause, pat, tmpl in RULES:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            # беремо найконкретнішу непорожню групу: остання непорожня зазвичай і є назвою
            groups = [g.strip() for g in (m.groups() or ()) if g and g.strip()]
            pick = groups[-1] if groups else (m.group(0) or "")
            detail = tmpl.replace("{0}", pick[:160]) if "{0}" in tmpl else tmpl
            status = {
                "env_missing_python": "env", "env_binary": "env", "env_db": "env",
                "dep_missing_module": "dep", "dep_uninstallable": "dep",
                "timeout": "timeout",
            }.get(cause, "fail")
            return status, cause, detail.strip()

    last = [l for l in text.strip().splitlines() if l.strip()][-1:] or [""]
    return "fail", "unknown", last[0][:300]


def tail(log: str, lines: int = 60) -> str:
    """Хвіст логу для публікації: тільки помилкові рядки і контекст."""
    ls = log.strip().splitlines()
    interesting = [i for i, l in enumerate(ls) if re.search(r"ERROR|CRITICAL|Traceback", l)]
    if interesting:
        start = max(0, interesting[0] - 5)
        return "\n".join(ls[start:start + lines])
    return "\n".join(ls[-lines:])
