"""Класифікатор причин падіння install.

Це найважливіший файл проєкту. Якщо не розділяти «модуль несумісний» від
«на машині немає python-пакета» — цифрам ніхто не повірить, і весь індекс нічого не вартий.
"""
import re

# Порядок має значення: перший збіг виграє.
RULES = [
    # --- не вина модуля: інфраструктура ---
    # Формулювання самого Odoo при незадоволеній external_dependencies. Це НЕ
    # ModuleNotFoundError: Odoo ловить ImportError і піднімає власний
    # MissingDependency, тому старе правило нижче їх не бачило — і всі такі
    # прогони падали аж у "registry", тобто зараховувалися модулю як
    # несумісність із версією. Спіймано 19.08.2026 на перших 113 прогонах:
    # 5 падінь з 5 були саме цим (pandas, openupgradelib, cssselect).
    # Джерело рядків — odoo/modules/module.py у самих образах 18.0 і 19.0:
    #   18.0: f"External dependency {pydep} not installed: {e}"
    #   19.0: "External dependency {dependency!r} not installed: %s"
    #   обидва: 'Unable to find %r in path' для бінарників
    ("env_missing_python",
     r"External dependency ['\"]?([\w\.\-]+)['\"]? not installed",
     "Немає зовнішнього python-пакета: {0}"),
    ("env_missing_python", r"External dependency version mismatch: (\S+)",
     "Версія зовнішнього python-пакета не підходить: {0}"),
    ("env_missing_python",
     r"external dependency is not met: ['\"]?([\w\.\-]+)",
     "Незадоволена зовнішня залежність: {0}"),
    ("env_binary", r"Unable to find ['\"]?([\w\.\-]+)['\"]? in path",
     "Немає системної утиліти в образі: {0}"),
    ("env_missing_python",
     r"Package .packaging. is required to parse .([^`']+). external dependency",
     "Немає зовнішнього python-пакета: {0}"),
    # Структурний запобіжник: не за текстом повідомлення, а за шляхом у стеку.
    # Формулювань у Odoo щонайменше п'ять і вони міняються між серіями — ганятися
    # за кожним означає щоразу дізнаватися про сліпу зону вже з опублікованих
    # цифр. Ці три функції існують РІВНО для перевірки external_dependencies,
    # тому їхня поява в трейсбеку однозначно означає проблему оточення, а не
    # несумісність модуля з версією. Правило стоїть після точних: ті дають
    # назву пакета, це — лише гарантію, що ми не зарахуємо env як fail.
    ("env_missing_python",
     r"check_python_external_dependency|check_manifest_dependencies"
     r"|check_external_dependencies|MissingDependency",
     "Незадоволена зовнішня залежність модуля"),
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
    # Формулювання Odoo при відсутньому МОДУЛІ-залежності (не python-пакеті):
    #   You try to install module "X" that depends on module "Y".
    #   But the latter module is not available in your system.
    # Спіймано 19.08.2026: три модулі field-service лягли в fail/registry, тобто
    # були зараховані як несумісні з 19.0, хоча насправді просто немає модуля
    # agreement / agreement_sale / sign_oca. Це dep, і в несумісність не йде.
    ("dep_missing_module",
     r"that depends on module ['\"]?([\w\.]+)",
     "Залежний модуль недоступний: {0}"),
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
    # `column rc.social_mastodon does not exist` — псевдонім таблиці робить імʼя
    # складеним, і \w+ його не ловив: 19.08.2026 такий випадок дійшов аж до
    # «НЕРОЗПІЗНАНО», хоча це найтиповіша несумісність — поле прибрали з версії.
    ("field_removed", r"(Field .{0,60} does not exist|Unknown field ([\w\.]+)|column \"?([\w.]+)\"? does not exist)",
     "Поле відсутнє в цій версії: {0}"),
    ("access_model", r"(Model .* does not exist|Invalid model name|_inherit .* not found)",
     "Модель відсутня або перейменована"),
    ("sql_error", r"(psycopg2\.errors\.\w+|psycopg2\.\w+Error|IntegrityError|ProgrammingError)",
     "Помилка SQL при установці"),
    ("py_syntax", r"(SyntaxError|IndentationError)", "Синтаксична помилка python"),
    # "Failed to load registry" присутнє в БУДЬ-ЯКОМУ падінні install, тому це
    # не причина, а симптом. Правило лишається останнім, але деталь тепер несе
    # справжній рядок помилки: інакше кожна нерозпізнана причина виглядала б
    # упевнено діагностованою («Реєстр не зібрався») і мовчки зараховувалась
    # модулю як несумісність. Саме так тричі за один день сюди потрапляли
    # відсутні python-пакети й відсутні модулі-залежності.
    ("registry", r"Failed to (?:load|initialize) registry", "НЕРОЗПІЗНАНО: {0}"),
]

# Рядок, який найімовірніше пояснює падіння: останній «Тип: текст» винятку.
EXC_LINE = re.compile(r"^(?:[\w\.]+\.)?(\w*(?:Error|Exception|Warning)): (.+)$")

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
            if cause == "registry":
                pick = _exception_line(text) or pick
            detail = tmpl.replace("{0}", pick[:160]) if "{0}" in tmpl else tmpl
            status = {
                "env_missing_python": "env", "env_binary": "env", "env_db": "env",
                "dep_missing_module": "dep", "dep_uninstallable": "dep",
                "timeout": "timeout",
            }.get(cause, "fail")
            return status, cause, detail.strip()

    last = _exception_line(text) or (
        ([l for l in text.strip().splitlines() if l.strip()][-1:] or [""])[0])
    return "fail", "unknown", last[:300]


def _exception_line(text: str):
    """Останній рядок виду `SomeError: пояснення` — саме він пояснює падіння.

    Без цього деталь нерозпізнаного падіння була рядком трейсбеку («raise
    UserError(_(»), з якого причину не видно, і сліпа зона класифікатора
    лишалася непоміченою до наступного ручного перегляду логів.
    """
    for line in reversed(text.strip().splitlines()):
        m = EXC_LINE.match(line.strip())
        if m:
            return f"{m.group(1)}: {m.group(2)}"
    return None


def tail(log: str, lines: int = 60) -> str:
    """Хвіст логу для публікації: тільки помилкові рядки і контекст."""
    ls = log.strip().splitlines()
    interesting = [i for i, l in enumerate(ls) if re.search(r"ERROR|CRITICAL|Traceback", l)]
    if interesting:
        start = max(0, interesting[0] - 5)
        return "\n".join(ls[start:start + lines])
    return "\n".join(ls[-lines:])
