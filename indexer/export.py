#!/usr/bin/env python3
"""Генератор статики. Ні Node, ні Hugo — щоб на 4 vCPU нічого не зжирало ресурс.

Двомовний: англійська за замовчуванням у корені, українська під /uk/.

Робить у var/site:
  index.html  /  uk/index.html              табло міграції
  m/<repo>/<module>/  /  uk/m/...           сторінка модуля
  r/<repo>/  /  uk/r/...                    сторінка репозиторію
  methodology.html  /  uk/methodology.html
  data/index.html  /  uk/data/index.html
  data/modules.csv                          датасет, один на обидві мови
  modules.json                              індекс пошуку, мовно-нейтральний
  robots.txt, sitemap.xml, llms.txt         одні, англійською, у корені
  favicon.svg                               власний знак

Логотипів Odoo і OCA не використовуємо — ні їхніх зображень, ні похідних.
Візуальна мова — версійні чипи і власний знак. У підвалі кожної сторінки
стоїть дисклеймер про непов'язаність із Odoo S.A. та OCA.
"""
import csv, html, json, os, pathlib, re, shutil, subprocess, sys, urllib.parse, datetime
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT
from state import derive_state, label as state_label, breakdown
from db import SERIES as TESTED_SERIES

SITE = ROOT / "var" / "site"
NOW = datetime.datetime.now(datetime.timezone.utc)
BASE = os.environ.get("SITE_BASE", "https://allservices.one")
TITLE = os.environ.get("SITE_TITLE", "Module Health Index")

# Зворотний зв'язок. Два канали, і порядок не випадковий.
#
# GitHub Issues — основний: аудиторія проєкту живе там, історія публічна, і кожне
# виправлення стає доказом сумлінності замість приватного листування. Це прямо
# працює на єдину тезу проєкту («факт прогону, а не оцінка вендора»): якщо ми
# кажемо «покажемо лог або перепрогонимо», то місце, де це видно, мусить бути
# публічним.
#
# Пошта — окремий аліас `hello@`, а НЕ робоча адреса: адреса з публічної
# сторінки збирається спам-ботами за тижні, а аліас у разі потопу видаляється й
# заводиться новий без наслідків для решти пошти домену.
REPO_URL = os.environ.get("REPO_URL", "https://github.com/allservices-one/o-modules")
CONTACT = os.environ.get("SITE_CONTACT", "hello@allservices.one")

LANGS = ("en", "uk")
DEFAULT_LANG = "en"

# Константи, які не виводяться з даних.
NEXT_SERIES = "20.0"                             # серія, гілок якої ще нема
V20_KEYNOTE = datetime.date(2026, 9, 24)         # кейноут Odoo Experience
V19_RELEASED = datetime.date(2025, 9, 1)         # для «N місяців після релізу 19.0»

# Палітра статусів install. НЕ використовувати для смуги розриву портування:
# ці кольори означають результат прогону, а «не перенесено» — не помилка.
STATUS_CLS = {
    "ok": ("✓", "good"), "warn": ("!", "warning"), "dep": ("▲", "serious"),
    "env": ("~", "muted"), "fail": ("✗", "critical"), "timeout": ("⏱", "critical"),
    # Похідні стани (indexer/state.py). Жоден не є помилкою модуля, тому
    # критичної палітри тут немає: not_installable — це намір автора
    # (метапакет, залишок _unported), а не поломка.
    "pending": ("·", "muted"), "not_installable": ("◌", "muted"),
    "not_verifiable": ("?", "muted"), "out_of_scope": ("–", "muted"),
    # «гілки немає» і «гілка є, але серію не проганяємо» — різні твердження,
    # і плутати їх у матриці означає відповідати не на те питання.
    "absent": ("×", "muted"),
    None: ("—", "muted"),
}

# Смуга серії. Сегменти — це РЕЗУЛЬТАТИ прогонів (`ok`…`timeout`) плюс стани,
# для яких прогону не було. Раніше цикл ішов лише по ключах станів, а
# `breakdown()` станами і віддає — тому єдиним сегментом, який узагалі збігався,
# лишався `not_installable`, і смуга показувала виключену меншість замість
# результату: у 17.0 «1915 verified» під смугою з 15 невстановлюваних на всю
# ширину (ops/inbox/0021 B2). Джерело тепер одне — `by_status` + стани.
BAR_ORDER = ["ok", "warn", "dep", "env", "fail", "timeout",
             "pending", "not_installable", "not_verifiable", "out_of_scope",
             "absent"]

# Клас і змінна CSS — різні простори імен, і саме на цьому смуги втратили колір:
# `.muted` існує як КЛАС (`color:var(--m)`), але `var(--muted)` не існує ніде,
# тому `background` не застосовувався взагалі (ops/inbox/0021 B1). Тут — лише
# імена змінних із `:root`. Перевіряє це `check_css_vars()`, а не пильність.
BAR_VAR = {"ok": "good", "warn": "warning", "dep": "serious", "env": "m",
           # timeout НЕ фарбуємо як fail: одиничний таймаут — властивість
           # стенду, не вердикт модулю (ops/inbox/0019 E), а два сегменти
           # одного кольору поруч читаються як один.
           "fail": "critical", "timeout": "stall",
           "pending": "l", "not_installable": "ax", "not_verifiable": "ax",
           "out_of_scope": "notported", "absent": "notported"}


def bar_parts(b, series=""):
    """Сегменти смуги однієї серії: [(ключ, n, змінна CSS)].

    Сума сегментів мусить дорівнювати `total` серії. Не сходиться — `raise`, а
    не рендер: тиха втрата сегментів уже проходила і синтаксис, і генерацію, і
    годинний таймер (ops/inbox/0021 B3). Це третій випадок того самого класу за
    тиждень, тому тут виняток, а не попередження.
    """
    counts = dict(b.get("by_status") or {})
    for st in ("pending", "not_installable", "not_verifiable",
               "out_of_scope", "absent"):
        if b.get(st):
            counts[st] = b[st]
    unknown = sorted(set(counts) - set(BAR_ORDER))
    if unknown:
        raise RuntimeError(
            f"смуга {series}: невідомі ключі {unknown} — "
            f"додати в BAR_ORDER і BAR_VAR, інакше сегмент зникне молча")
    parts = [(k, counts[k], BAR_VAR[k]) for k in BAR_ORDER if counts.get(k)]
    got, want = sum(n for _, n, _ in parts), b.get("total", 0)
    if got != want:
        raise RuntimeError(
            f"смуга {series}: сегментів {got}, модулів у серії {want}; "
            f"розклад {b}")
    return parts


T = {
    "en": {
        "lang_name": "EN", "other_name": "УК",
        "nav_methodology": "methodology", "nav_dataset": "dataset",
        "h1": "Which OCA modules actually run on which Odoo version",
        "sub": ("We install every public OCA module into a clean database of every Odoo "
                "series and publish what happened — the install log, with a date. "
                "Not vendor claims."),
        "why_exists_h": "Why this exists",
        "why_exists_p": ("Odoo ships a major version every year. Third-party and custom "
                         "modules are not covered by Odoo's own upgrade service, and nobody "
                         "publishes whether a given module actually installs on a given "
                         "version. So partners find out during the upgrade, by hand."),
        "why_now_h": "Why now",
        "why_now_p": ("Odoo {next} is unveiled on {keynote}. As of today, {gap} of the "
                      "{base} modules available on {prev} have never been ported to {new} "
                      "— {months} months after {new} shipped. {zero} repositories have a "
                      "{next} branch yet."),
        "who_h": "Who it's for",
        "who_p": ("Odoo partners scoping an upgrade, OCA maintainers, and anyone choosing "
                  "which modules to build on."),
        "not_h": "What this is not",
        "not_p": ("An install check, not a functional test — a module can install and still "
                  "misbehave. Paid Apps Store modules are outside this index entirely: "
                  "we neither run nor list them. Failures caused by a "
                  "missing Python package in the image are reported as environment "
                  "problems, never as version incompatibility."),
        "search": "Search a module or repository…",
        "tile_modules_on": "Modules on {s}", "tile_latest": "latest series",
        "tile_ported": "Ported {a}→{b}", "tile_still": "{n} not yet",
        "gap_h": "Porting gap {a} → {b}",
        "gap_ported": "Ported to {b}", "gap_not": "Not ported",
        "status_h": "Install status on {s}",
        "tested": "Tested {n} of {m} modules.",
        "modules_h": "Modules", "col_module": "Module",
        "showing": 'Full list also in the <a href="{dataset}">dataset</a>.',
        "f_cat": "Category", "f_vendor": "Vendor", "f_state": "State",
        "f_series": "Series", "f_any": "any", "f_clear": "Clear filters",
        "f_group": "Group by category", "f_shown": "Showing {n} of {m}",
        "f_none": "Nothing matches these filters.",
        "col_vendor": "Vendor", "col_cat": "Category", "col_repo": "Repository",
        "sort_hint": "Click a header to sort",
        "status_all_h": "Install status by series",
        "bar_note": "{v} verified of {r} runnable ({tot} in this series)",
        "bar_none": "not run — {tot} modules indexed from git only",
        "dep_h": "Depends on", "dep_col": "Module", "dep_when": "Checked",
        "dep_core": "core", "dep_core_note": "ships with Odoo",
        "dep_absent": "not in the index for {s}",
        "dep_none": "No Odoo dependencies declared.",
        "dep_unknown_manifest": "Manifest not read for this series — "
                                "we only index which series have a branch here.",
        "dep_blocked": "Blocked by {n} of {m} dependencies on {s}:",
        "ext_h": "External dependencies", "ext_kind": "Kind", "ext_pkg": "Package",
        "ext_state": "In our image", "ext_in": "yes", "ext_out": "NO",
        "ext_note": "Checked against {img}.",
        "dep_in_image": "in image", "dep_not_in_image": "NOT in image",
        "rev_h": "Depended on by",
        "rev_p": "{n} modules on {s} declare this one as a dependency.",
        "git_h": "History", "git_last": "Last change", "git_work": "Code commits, 12 months",
        "git_authors": "Most active", "git_files": "Files",
        "git_note": "Translation and bot commits are excluded from the commit count.",
        "f_link": "This selection is a link — copy the address bar to share it.",
        "denom": "{ok} of {ver} tested modules on {s} install cleanly ({pct}%). "
                 "Runnable: {run} of {total} — {noninst} not installable by "
                 "manifest, {pending} still pending.",
        "m_series": "Series", "m_status": "Status", "m_details": "Details",
        "m_nobranch": "no branch", "m_run": "run {d}", "m_log": "Install log {s}",
        "m_source": "source on GitHub", "m_in": "in",
        "r_modules": "{n} modules",
        "d_h1": "Open dataset",
        "d_intro": "Module × series × status × cause. Licence: CC BY 4.0. Please cite the source.",
        "d_csv": "modules.csv — module × series × status × cause",
        "d_json": "modules.json — search index",
        "meth_h1": "Methodology",
        "footer_updated": "Data updated {d} UTC",
        "footer_fact": "we publish the install log with a date, not a vendor rating",
        "footer_open": "CSV and JSON are open",
        "footer_contact": "Questions or a wrong result",
        "footer_issues": "open an issue",
        "footer_or": "or",
        "m_wrong": "Result looks wrong?",
        "m_wrong_cta": "Tell us — we will show the log or run it again.",
        "m_wrong_link": "Report a wrong result",
        "independent": ("Independent project. Not affiliated with Odoo S.A. or the "
                        "Odoo Community Association."),
        "st_ok": "Installs", "st_warn": "Installs with warnings",
        "st_dep": "Blocked by a dependency", "st_env": "Environment problem",
        "st_fail": "Install error", "st_timeout": "Timed out", "st_none": "Not tested",
    },
    "uk": {
        "lang_name": "УК", "other_name": "EN",
        "nav_methodology": "методологія", "nav_dataset": "датасет",
        "h1": "Які модулі OCA справді працюють на якій версії Odoo",
        "sub": ("Ми встановлюємо кожен публічний модуль OCA у чисту базу кожної серії "
                "Odoo і публікуємо, що сталося — лог install і дату. Не заяви вендорів."),
        "why_exists_h": "Чому це існує",
        "why_exists_p": ("Odoo випускає мажорну версію щороку. Сторонні й кастомні модулі "
                         "офіційна служба апгрейду не мігрує, і ніде не публікується, чи "
                         "конкретний модуль узагалі встановлюється на конкретну версію. "
                         "Партнери дізнаються це під час апгрейду, вручну."),
        "why_now_h": "Чому зараз",
        "why_now_p": ("Odoo {next} презентують {keynote}. Станом на сьогодні {gap} із "
                      "{base} модулів, доступних на {prev}, так і не перенесені на {new} "
                      "— через {months} місяців після релізу {new}. Гілки {next} не має "
                      "{zero} репозиторій."),
        "who_h": "Для кого",
        "who_p": ("Партнери Odoo, які планують апгрейд, мейнтейнери OCA, і будь-хто, "
                  "хто вибирає модулі під проєкт."),
        "not_h": "Чим це не є",
        "not_p": ("Перевірка install, а не функціональний тест — модуль може встановитися "
                  "й працювати неправильно. Платні модулі Apps Store поза цим індексом "
                  "повністю: ми їх не проганяємо і не перелічуємо. Падіння через "
                  "відсутній python-пакет в образі позначається як проблема середовища, "
                  "а не як несумісність із версією."),
        "search": "Пошук модуля або репозиторію…",
        "tile_modules_on": "Модулів на {s}", "tile_latest": "остання серія",
        "tile_ported": "Перенесено {a}→{b}", "tile_still": "{n} ще ні",
        "gap_h": "Розрив портування {a} → {b}",
        "gap_ported": "Перенесені на {b}", "gap_not": "Не перенесені",
        "status_h": "Статус install на {s}",
        "tested": "Протестовано {n} з {m} модулів.",
        "modules_h": "Модулі", "col_module": "Модуль",
        "showing": 'Повний перелік також у <a href="{dataset}">датасеті</a>.',
        "f_cat": "Категорія", "f_vendor": "Вендор", "f_state": "Стан",
        "f_series": "Серія", "f_any": "будь-яка", "f_clear": "Скинути фільтри",
        "f_group": "Групувати за категорією", "f_shown": "Показано {n} з {m}",
        "f_none": "За цими фільтрами нічого немає.",
        "col_vendor": "Вендор", "col_cat": "Категорія", "col_repo": "Репозиторій",
        "sort_hint": "Клік на заголовку — сортування",
        "status_all_h": "Статус install по серіях",
        "bar_note": "перевірено {v} з {r} прогонабельних ({tot} у цій серії)",
        "bar_none": "не проганялась — {tot} модулів індексовано лише з git",
        "dep_h": "Залежить від", "dep_col": "Модуль", "dep_when": "Перевірено",
        "dep_core": "ядро", "dep_core_note": "йде в самому Odoo",
        "dep_absent": "немає в індексі для {s}",
        "dep_none": "Залежностей Odoo не оголошено.",
        "dep_unknown_manifest": "Манифест для цієї серії не читався — тут ми "
                                "індексуємо лише наявність гілки.",
        # Форма навмисно без узгодження числівника: «1 залежностей» неграмотно,
        # а перебирати відмінки заради одного рядка — зайве. «Не вистачає N з M»
        # правильне для будь-якого числа.
        "dep_blocked": "Не вистачає {n} з {m} залежностей на {s}:",
        "ext_h": "Зовнішні залежності", "ext_kind": "Вид", "ext_pkg": "Пакет",
        "ext_state": "У нашому образі", "ext_in": "так", "ext_out": "НІ",
        "ext_note": "Перевірено проти {img}.",
        "dep_in_image": "є в образі", "dep_not_in_image": "НЕМАЄ в образі",
        "rev_h": "Від нього залежать",
        "rev_p": "{n} модулів на {s} оголошують його своєю залежністю.",
        "git_h": "Історія", "git_last": "Остання зміна", "git_work": "Комітів коду за 12 міс",
        "git_authors": "Найактивніші", "git_files": "Файлів",
        "git_note": "Переклади й коміти ботів у підрахунок не входять.",
        "f_link": "Цей відбір — посилання: скопіюйте адресний рядок, щоб поділитися.",
        "denom": "{ok} з {ver} перевірених модулів на {s} встановлюються чисто "
                 "({pct}%). Прогонабельних: {run} з {total} — {noninst} не "
                 "встановлювані за манифестом, {pending} ще чекають прогону.",
        "m_series": "Серія", "m_status": "Статус", "m_details": "Деталі",
        "m_nobranch": "гілки немає", "m_run": "прогін {d}", "m_log": "Лог прогону {s}",
        "m_source": "джерело на GitHub", "m_in": "у",
        "r_modules": "{n} модулів",
        "d_h1": "Відкритий датасет",
        "d_intro": "Модуль × серія × статус × причина. Ліцензія: CC BY 4.0. Посилайтеся на джерело.",
        "d_csv": "modules.csv — модуль × серія × статус × причина",
        "d_json": "modules.json — індекс пошуку",
        "meth_h1": "Методологія",
        "footer_updated": "Дані оновлено {d} UTC",
        "footer_fact": "публікуємо факт прогону з логом і датою, не оцінку вендора",
        "footer_open": "CSV і JSON відкриті",
        "footer_contact": "Питання або хибний результат",
        "footer_issues": "issue на GitHub",
        "footer_or": "чи",
        "m_wrong": "Результат виглядає неправильним?",
        "m_wrong_cta": "Повідомте — ми покажемо лог або перепрогонимо.",
        "m_wrong_link": "Повідомити про хибний результат",
        "independent": ("Незалежний проєкт. Не пов'язаний з Odoo S.A. чи Odoo Community "
                        "Association."),
        "st_ok": "Встановлюється", "st_warn": "Із попередженнями",
        "st_dep": "Блокує залежність", "st_env": "Проблема середовища",
        "st_fail": "Помилка install", "st_timeout": "Таймаут", "st_none": "Не тестовано",
    },
}

METHODOLOGY = {
    "en": """<h2>How a module is checked</h2>
<p>Every module is installed into a <b>clean database</b> of the matching Odoo series, built
from the official <code>odoo:&lt;series&gt;</code> image, with no demo data, using
<code>-i &lt;module&gt; --stop-after-init</code>. The result is the process exit code and the log.</p>
<h2>Statuses</h2>{table}
<h2>What we deliberately do NOT count as incompatibility</h2>
<p>A failure caused by a missing external Python package or a missing system utility in the
image is recorded as an <b>environment problem</b> and is <b>not</b> counted against the
module. The same applies when the harness itself fails to expose the module to Odoo. This is
deliberate: without that separation the statistics would be untrue, and an untrue index is
worth nothing.</p>
<h2>What the percentages are measured against</h2>
<p>Every published percentage names its own denominator, because a percentage without one
can be moved simply by changing what you count. A module counts as <b>runnable</b> only if
we can obtain it, it declares itself installable, and the series is one we actually run.
Concretely, these are excluded from both the numerator and the denominator:</p><ul>
<li><b>Not installable by manifest</b> — the module itself sets <code>installable: False</code>.
These are meta-packages, leftovers of unported code and deprecation shims. Counting them as
&laquo;broken&raquo; would move our headline number with the number of meta-packages, which has
nothing to do with version compatibility.</li>
<li><b>Series we do not run</b> — 16.0 and 17.0 are indexed from git but never installed, so
they are marked as not covered rather than pretended to be pending.</li>
<li><b>Cannot be verified</b> — see the note on the Apps Store below.</li></ul>
<p>So &laquo;91% install cleanly&raquo; is always written out in full: how many of how many
<i>tested</i>, and separately how many are runnable out of the total.</p>
<h2>Limits</h2><ul>
<li>Paid Apps Store modules cannot be installed without a licence, and the Store provides no
way to enumerate its listings that its own <code>robots.txt</code> permits. They are outside
this index entirely: we neither run nor list them.</li>
<li>An install check is not a functional test: a module can install and still misbehave.</li>
<li>Batch mode: on a mass pass modules are installed in groups; if a group fails, each member
is retried alone. The dataset records this in the <code>batched</code> field.</li></ul>
<p>We publish the fact of a run, with its date and log — not a rating of a vendor.</p>
<h2>Result looks wrong?</h2>
<p>Tell us — we will show the full log or run the module again. This is not a formality:
of the first fifteen <code>fail</code> verdicts, nine turned out to be <b>our</b> mistakes
rather than module incompatibilities, and they were found only because someone opened
the log.</p>
<p>The main channel is <a href="{issues}">a GitHub issue</a>: the history is public, and it
shows that we answer. Every module page carries a link that pre-fills the module, series,
run date and verdict. By mail: <a href="mailto:{contact}">{contact}</a>.</p>
<p class="mut">Maintainer, Module Health Index</p>""",
    "uk": """<h2>Як перевіряється модуль</h2>
<p>Кожен модуль встановлюється в <b>чисту базу</b> відповідної серії Odoo з офіційного образу
<code>odoo:&lt;серія&gt;</code>, без демо-даних, командою <code>-i &lt;module&gt; --stop-after-init</code>.
Результат — код виходу процесу і лог.</p>
<h2>Статуси</h2>{table}
<h2>Що ми свідомо НЕ вважаємо несумісністю</h2>
<p>Падіння через відсутній зовнішній python-пакет або системну утиліту в образі позначається як
<b>проблема середовища</b> і <b>не</b> зараховується модулю. Так само — якщо сам харнес не подав
модуль до Odoo. Це навмисно: без такого розділення статистика була б неправдивою, а неправдивий
індекс не вартий нічого.</p>
<h2>Від чого рахуються відсотки</h2>
<p>Кожен опублікований відсоток називає свій знаменник, бо відсоток без знаменника
пересувається простою зміною того, що рахувати. Модуль вважається <b>прогонабельним</b>,
лише якщо ми можемо його дістати, він сам заявляє себе встановлюваним, і серія — з тих,
які ми справді проганяємо. Не входять ні в чисельник, ні в знаменник:</p><ul>
<li><b>Не встановлювані за манифестом</b> — модуль сам ставить <code>installable: False</code>.
Це метапакети, залишки неперенесеного коду й оболонки для депрекації. Зарахувати їх до
&laquo;зламаних&raquo; означало б рухати головну цифру разом із кількістю метапакетів, а це
не має жодного стосунку до сумісності з версією.</li>
<li><b>Серії, які ми не проганяємо</b> — 16.0 і 17.0 індексуються з git, але не встановлюються,
тому позначені як не охоплені, а не як такі, що чекають прогону.</li>
<li><b>Неможливі до перевірки</b> — див. нижче про Apps Store.</li></ul>
<p>Тому &laquo;91% встановлюються чисто&raquo; завжди пишеться повністю: скільки зі скількох
<i>перевірених</i>, і окремо скільки прогонабельних із загальної кількості.</p>
<h2>Обмеження</h2><ul>
<li>Платні модулі Apps Store без ліцензії не встановлюються, а сам Store не має способу
перелічити свої листинги, який дозволяв би його власний <code>robots.txt</code>. Вони поза
цим індексом повністю: ми їх не проганяємо і не перелічуємо.</li>
<li>Install-прогін не є тестом функціональності: модуль може встановитися і працювати неправильно.</li>
<li>Батч-режим: при масовому проході модулі ставляться групами, при падінні групи кожен
перевіряється окремо. У даних це позначено полем <code>batched</code>.</li></ul>
<p>Публікуємо факт прогону з датою і логом, а не оцінку якості вендора.</p>
<h2>Результат виглядає неправильним?</h2>
<p>Повідомте — ми покажемо повний лог або перепрогонимо модуль. Це не формальність:
з перших пʼятнадцяти вердиктів <code>fail</code> девʼять виявилися <b>нашими</b>
помилками, а не несумісністю модулів, і знайшлися вони лише тому, що хтось
відкрив лог.</p>
<p>Основний канал — <a href="{issues}">issue на GitHub</a>: історія публічна, і видно,
що ми відповідаємо. На кожній сторінці модуля є посилання, яке вже підставляє модуль,
серію, дату прогону й вирок. Пошта — <a href="mailto:{contact}">{contact}</a>.</p>
<p class="mut">Maintainer, Module Health Index</p>""",
}

# Власний знак: три смуги спадної довжини — рівно та історія, що й у даних.
# Не містить і не наслідує символіку Odoo чи OCA.
MARK = ('<svg class="mk" viewBox="0 0 24 24" aria-hidden="true">'
        '<rect x="2" y="3" width="20" height="18" rx="4" fill="none" '
        'stroke="currentColor" stroke-width="2"/>'
        '<rect x="6" y="7"  width="12" height="2.4" rx="1.2" fill="currentColor"/>'
        '<rect x="6" y="11" width="8"  height="2.4" rx="1.2" fill="currentColor"/>'
        '<rect x="6" y="15" width="4"  height="2.4" rx="1.2" fill="currentColor"/>'
        '</svg>')

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect width="24" height="24" rx="5" fill="#0b0b0b"/>
<rect x="5" y="6"  width="14" height="2.6" rx="1.3" fill="#fcfcfb"/>
<rect x="5" y="10.7" width="9" height="2.6" rx="1.3" fill="#2a78d6"/>
<rect x="5" y="15.4" width="4.5" height="2.6" rx="1.3" fill="#c3c2b7"/>
</svg>
"""

CSS = """
:root{--s:#fcfcfb;--p:#f9f9f7;--i:#0b0b0b;--i2:#52514e;--m:#898781;--l:#e1e0d9;--ax:#c3c2b7;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--a:#2a78d6;
--stall:#7d6bb0;
--ported:#2a78d6;--notported:#c3c2b7}
@media(prefers-color-scheme:dark){:root{--s:#1a1a19;--p:#0d0d0d;--i:#fff;--i2:#c3c2b7;--m:#898781;
--l:#2c2c2a;--ax:#383835;--a:#3987e5;--ported:#3987e5;--notported:#4a4a46}}
*{box-sizing:border-box}body{margin:0;background:var(--p);color:var(--i);
font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
.w{max-width:980px;margin:0 auto;padding:28px 18px 72px}
a{color:var(--a);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:26px;letter-spacing:-.02em;margin:0 0 4px}h2{font-size:18px;margin:34px 0 8px}
.mut{color:var(--m);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--l);
border:1px solid var(--l);border-radius:8px;overflow:hidden;margin:16px 0 22px}
.t{background:var(--s);padding:12px 13px}.t .k{font-size:11px;text-transform:uppercase;
letter-spacing:.04em;color:var(--m)}.t .v{font-size:24px;font-weight:600;letter-spacing:-.02em}
.t .n{font-size:11.5px;color:var(--m)}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:var(--s)}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--m);
padding:8px 10px;border-bottom:1px solid var(--ax)}
td{padding:8px 10px;border-bottom:1px solid var(--l);font-variant-numeric:tabular-nums}
.c{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;white-space:nowrap}
.good{color:var(--good)}.warning{color:var(--warning)}.serious{color:var(--serious)}
.critical{color:var(--critical)}.muted{color:var(--m)}
pre{background:var(--l);padding:11px 13px;border-radius:7px;overflow-x:auto;
font:11.5px/1.6 ui-monospace,Menlo,monospace;color:var(--i2)}
input{width:100%;padding:11px 13px;font-size:14px;border:1px solid var(--ax);border-radius:7px;
background:var(--s);color:var(--i)}
/* Панель відбору. Мультиколонковий grid, щоб на телефоні лягало в стовпчик
   без медіазапиту. */
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
gap:9px;align-items:end;margin:14px 0 10px}
.filters label{display:flex;flex-direction:column;gap:4px;font-size:11px;
text-transform:uppercase;letter-spacing:.05em;color:var(--m)}
.filters select,.filters button{padding:9px 11px;font-size:13.5px;border-radius:7px;
border:1px solid var(--ax);background:var(--s);color:var(--i)}
.filters button{cursor:pointer;text-transform:none;letter-spacing:0}
.filters .cb{flex-direction:row;align-items:center;gap:7px;text-transform:none;
letter-spacing:0;font-size:13.5px;color:var(--i)}
.filters .cb input{width:auto}
/* Напрямок сортування — стрілкою і aria-sort, НЕ кольором: інакше для
   дальтоніка стан колонки нерозрізненний. */
th[data-sort]{cursor:pointer;user-select:none}
th[data-sort]:focus-visible{outline:2px solid var(--good);outline-offset:-2px}
th[data-arrow]:not([data-arrow=""])::after{content:" " attr(data-arrow);font-weight:700}
/* Матриця модуль × серія: чотири клітинки в рядок, згортається на телефоні. */
.mxr{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 18px}
.mx{display:flex;flex-direction:column;gap:5px;padding:9px 12px;
border:1px solid var(--l);border-radius:8px;background:var(--s);min-width:104px}
.bh{font-size:13px;margin:16px 0 4px;font-weight:600}
.bh .mut{font-weight:400;font-size:12px}
tr.grp td{background:var(--l);font-weight:600;font-size:12px;
text-transform:uppercase;letter-spacing:.04em;color:var(--m)}
.bar{display:flex;gap:2px;height:26px;border-radius:5px;overflow:hidden;margin:10px 0 6px}
.bar div{display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;
color:#fff;min-width:2px}
.lg{display:flex;flex-wrap:wrap;gap:13px;font-size:12px;color:var(--i2)}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block;margin-right:5px}
nav{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--m);margin-bottom:18px;
flex-wrap:wrap}
nav .sp{margin-left:auto}
.brand{display:inline-flex;align-items:center;gap:7px;color:var(--i);font-weight:600}
.brand:hover{text-decoration:none}
.mk{width:19px;height:19px;flex:none}
.vchip{display:inline-block;padding:1px 7px;border:1px solid var(--ax);border-radius:20px;
font-size:11.5px;font-weight:600;color:var(--i2);font-variant-numeric:tabular-nums}
.lead{font-size:16px;color:var(--i2);margin:0 0 6px;max-width:70ch}
.sec{max-width:74ch}.sec h2{font-size:15px;margin:22px 0 4px}
.sec p{margin:0;color:var(--i2);font-size:14px}
.ind{color:var(--m);font-size:12px;margin-top:10px}
@media(max-width:700px){.tiles{grid-template-columns:1fr 1fr}}
"""


def loc(lang, path):
    """Публічний URL сторінки. path завжди починається з '/'."""
    return path if lang == DEFAULT_LANG else "/uk" + path


def out_path(lang, rel):
    return (SITE if lang == DEFAULT_LANG else SITE / "uk") / rel


def st_label(status, lang):
    if status in ("pending", "not_installable", "not_verifiable",
                  "out_of_scope", "absent"):
        return state_label(status, lang)
    key = {"ok": "st_ok", "warn": "st_warn", "dep": "st_dep", "env": "st_env",
           "fail": "st_fail", "timeout": "st_timeout"}.get(status, "st_none")
    return T[lang][key]


def cell_state(row):
    """Що показати в клітинці серії: код для чипа + похідний стан для фільтра.

    Верифікований модуль показує РЕЗУЛЬТАТ прогону, решта — причину, чому
    прогону немає. Обидва коди їдуть у modules.json, бо фільтр «стан» і
    фільтр «статус» — це різні питання, і зводити їх в одне поле означало б
    повторити помилку, від якої трьохвісна модель і захищає.
    """
    row = dict(row, in_scope=row["series"] in TESTED_SERIES)
    st, status = derive_state(row)
    return {"k": status if st == "verified" else st, "e": st}


def chip(status, lang):
    ic, cls = STATUS_CLS.get(status, STATUS_CLS[None])
    return f'<span class="c {cls}"><span>{ic}</span>{st_label(status, lang)}</span>'


def issue_url(repo, mod, series, status, cause, run_at):
    """Готове посилання на створення issue з підставленими даними прогону.

    Нуль бекенду: форма означала б динамічний endpoint, спам і код, а GitHub уже
    вміє приймати `title` і `body` у query.

    Навіщо саме заповнене, а не «напишіть нам»: девʼять із перших пʼятнадцяти
    `fail` виявилися НАШИМИ помилками, і знайшлися вони лише тому, що хтось
    відкрив лог. Коли індекс побачать сотні людей, цей канал знайде наступні
    девʼять швидше за нас — але тільки якщо повідомити коштує один клік і не
    вимагає згадувати, яка була серія й дата.

    Текст тіла — англійською незалежно від мови сторінки: issue читають і
    відповідають на нього в одному місці, і мова тіла не мусить залежати від
    того, з якого мовного дерева людина прийшла.
    """
    lines = [f"Module: {repo}/{mod}", f"Series: {series}"]
    if run_at:
        lines.append(f"Run date: {run_at:%Y-%m-%d}")
    lines += [f"Verdict: {status or '-'}/{cause or '-'}",
              f"Page: {BASE}/m/{repo}/{mod}/", "", "What I expected:", ""]
    q = urllib.parse.urlencode({
        "title": f"Wrong result: {mod} on {series}",
        "body": "\n".join(lines),
        "labels": "wrong-result",
    })
    return f"{REPO_URL}/issues/new?{q}"


def page(lang, url, title, body, desc="", jsonld=None, noindex=False, feed="/feed.xml"):
    """Сторінка з canonical на себе і hreflang на обидві мови.

    noindex=True ставить <meta name="robots" content="noindex,follow"> — для
    сторінок, у яких ще немає жодного install-статусу. Свідомо НЕ через robots.txt
    і НЕ через заголовок у Caddy: Disallow і noindex взаємно скасовуються (закритий
    краулер не прочитає noindex), а Disallow заблокував би GPTBot, тобто основний
    канал проєкту. Правило однакове в обох мовних деревах.

    `follow` обов'язковий і доданий 21.08.2026. Без нього краулер, дійшовши до
    сторінки без результату, зупинявся: `noindex` за замовчуванням не забороняє
    переходи, але й не гарантує їх, а саме тонкі сторінки — це вузли, через які
    видно решту (залежності модуля, сусіди по репозиторію). Виключати сторінку з
    індексу і водночас обрізати маршрут — дві різні дії, і друга нам не потрібна.
    """
    t = T[lang]
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>' if jsonld else ""
    ni = '<meta name="robots" content="noindex,follow">' if noindex else ""
    other = "uk" if lang == "en" else "en"
    alts = "".join(
        f'<link rel="alternate" hreflang="{lg}" href="{BASE}{loc(lg, url)}">' for lg in LANGS
    ) + f'<link rel="alternate" hreflang="x-default" href="{BASE}{loc(DEFAULT_LANG, url)}">'
    # Автовиявлення фіда: читалка підхоплює правильний фід сама, і це половина
    # справи. На сторінці вендора — його власний, скрізь інде — загальний.
    alts += (f'<link rel="alternate" type="application/atom+xml" '
             f'title="{html.escape(title)}" href="{BASE}{feed}">')
    return f"""<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">{ni}
<link rel="canonical" href="{BASE}{loc(lang, url)}">{alts}
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>{CSS}</style>{ld}</head><body><div class="w">
<nav><a class="brand" href="{loc(lang, '/')}">{MARK}{TITLE}</a>
<a href="{loc(lang, '/methodology.html')}">{t['nav_methodology']}</a>
<a href="{loc(lang, '/data/')}">{t['nav_dataset']}</a>
<span class="sp"></span><a href="{loc(other, url)}" hreflang="{other}">{T[other]['lang_name']}</a></nav>
{body}
<p class="mut" style="margin-top:40px;border-top:1px solid var(--l);padding-top:14px">
{t['footer_updated'].format(d=f'{NOW:%Y-%m-%d %H:%M}')} · {t['footer_fact']} ·
<a href="{loc(lang, '/data/')}">{t['footer_open']}</a></p>
<p class="mut">{t['footer_contact']}: <a href="{REPO_URL}/issues">{t['footer_issues']}</a>
{t['footer_or']} <a href="mailto:{CONTACT}">{CONTACT}</a></p>
<p class="ind">{t['independent']}</p></div></body></html>"""


FEED_MAX = 200          # більше читалці не потрібно, а віддавати дешевше
FEED_NS = "2026"        # рік у tag: URI. Ніколи не міняти — id мусить бути вічним.


def slug(s):
    """Ім'я вендора → стабільний шматок URL."""
    out = "".join(c.lower() if c.isalnum() else "-" for c in s)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "x"


def atom(entries, title, self_path, feed_id, lang="en", fallback=None):
    """Atom 1.0. Не RSS 2.0: там немає нормальних `id` і `updated`, а саме на
    них тримається вся коректність фіда.

    Три речі, на яких саморобні фіди ламаються, і всі три тут закриті:

    * `<id>` запису — `tag:...:change/<run_id>`, вічний і унікальний на подію.
      Не URL модуля: URL повторюється, і читалка склеїла б різні події в одну.
    * `<updated>` — час ПРОГОНУ, не час генерації. Інакше кожна регенерація
      сайту помічає весь фід як непрочитаний.
    * `<updated>` самого фіда — час найсвіжішої події, а не `now()`, з тієї ж
      причини. Це стосується й ПОРОЖНЬОГО фіда: `now()` там означав би, що
      читалка щогодини бачить зміну й приходить по нічого. `fallback` — час
      останньої зміни стану взагалі, включно з тими, що у стрічки не пішли.
    """
    upd = max((e["at"] for e in entries), default=(fallback or NOW))
    items = []
    for e in entries:
        items.append(
            f"<entry><title>{html.escape(e['title'])}</title>"
            f"<id>tag:allservices.one,{FEED_NS}:change/{e['key']}</id>"
            f"<updated>{e['at'].astimezone(datetime.timezone.utc).isoformat()}</updated>"
            f'<link rel="alternate" href="{BASE}{e["url"]}"/>'
            f'<content type="text">{html.escape(e["text"])}</content></entry>')
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="%s">'
        "<title>%s</title>"
        '<id>tag:allservices.one,%s:%s</id>'
        "<updated>%s</updated>"
        '<link rel="self" href="%s%s"/>'
        '<link rel="alternate" href="%s/"/>'
        "%s</feed>\n" % (
            lang, html.escape(title), FEED_NS, feed_id,
            upd.astimezone(datetime.timezone.utc).isoformat(),
            BASE, self_path, BASE, "".join(items)))


def feed_entries(conn):
    """Події для фідів: найсвіжіші зверху, без сівби і без змін стенду.

    `bench` відсіює зміни, які зробили ми, а не автор модуля: перезбірка образу
    або правка правил класифікатора (ops/inbox/0019 A, indexer/changes.py).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT c.run_id, c.series, c.state_old, c.state_new,
               c.status_old, c.status_new, c.at,
               m.repo, m.module, m.vendors
        FROM state_changes c JOIN modules m ON m.id = c.module_id
        WHERE NOT c.seeded AND NOT c.bench
        ORDER BY c.at DESC, c.id DESC
        LIMIT %s
    """, (FEED_MAX * 4,))
    out = []
    for r in cur.fetchall():
        was = r["status_old"] or r["state_old"] or "—"
        now_ = r["status_new"] or r["state_new"]
        out.append({
            "key": r["run_id"],
            "at": r["at"],
            "series": r["series"],
            "vendors": list(r["vendors"] or []),
            "url": f"/m/{r['repo']}/{r['module']}/",
            "title": f"{r['module']} ({r['series']}): {was} → {now_}",
            "text": f"{r['module']} in {r['repo']}, Odoo {r['series']}: "
                    f"{was} → {now_}.",
        })
    return out


def _cmd(args, default=""):
    """Тихо: status.json не має падати через відсутній git чи docker."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else default
    except Exception:
        return default


def status_json(conn):
    """Машинний зріз стану сервера → var/site/status.json.

    Це канал, яким сесія без SSH бачить, що тут відбувається: харвест, черга,
    прогони, версії образів. Публічний і без секретів — ні паролів, ні
    внутрішніх шляхів, ні IP. Caddy віддає його з Cache-Control: no-store,
    інакше читач бачив би вчорашній стан і робив із нього хибні висновки.
    """
    cur = conn.cursor()
    cur.execute("SELECT series, count(*) c FROM modules GROUP BY 1 ORDER BY 1")
    by_series = {r["series"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT taken_at, method FROM series_snapshots "
                "ORDER BY taken_at DESC LIMIT 1")
    row = cur.fetchone() or {}
    last_harvest, method = row.get("taken_at"), row.get("method")
    cur.execute("SELECT status, count(*) c FROM latest_runs GROUP BY 1 ORDER BY 1")
    by_status = {r["status"]: r["c"] for r in cur.fetchall()}
    # Розклад станів по серіях — щоб сесія без SSH бачила не лише «скільки
    # прогонів», а й знаменник, від якого рахується публічний відсоток.
    cur.execute("""
        SELECT m.series, m.availability, m.installable, r.status
        FROM modules m LEFT JOIN latest_runs r ON r.module_id = m.id
    """)
    per_series = {}
    for row in cur.fetchall():
        s = row["series"]
        per_series.setdefault(s, []).append(
            dict(row, in_scope=s in TESTED_SERIES))
    states = {s: breakdown(rows) for s, rows in sorted(per_series.items())}
    cur.execute("SELECT state, count(*) c FROM jobs GROUP BY 1 ORDER BY 1")
    queue = {r["state"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT count(*) c FROM modules")
    total_modules = cur.fetchone()["c"]

    # Образи. Джерело — `series_image`, а не літерал `odoo:{s}`: літерал
    # називав базові образи від 18.08, тоді як прогони йшли проти похідних із
    # доставленими залежностями, а 17.0 у розділі не було взагалі, хоча проти
    # нього прогнано 1 915 модулів (ops/inbox/0019 B). Це єдина машинна заявка
    # про відтворюваність — вона не має права називати не той образ.
    cur.execute("SELECT series, image, set_at FROM series_image")
    tags = {r["series"]: (r["image"], r["set_at"]) for r in cur.fetchall()}
    # Одного тега на серію все одно не досить: у 18.0 частина ПУБЛІКОВАНИХ
    # результатів отримана на базовому образі, а частина — на похідному, і
    # відтворити зріз можна лише знаючи обидва. Рахуємо по latest_runs, тобто
    # рівно по тих прогонах, які видно на сайті.
    cur.execute("SELECT series, odoo_image, count(*) c FROM latest_runs "
                "GROUP BY 1,2 ORDER BY 1,2")
    used = {}
    for r in cur.fetchall():
        used.setdefault(r["series"], {})[r["odoo_image"]] = r["c"]

    images = {}
    for s in sorted(set(tags) | set(TESTED_SERIES) | set(used) | {"20.0"}):
        tag, set_at = tags.get(s, (f"odoo:{s}", None))
        img = _cmd(["docker", "image", "inspect", tag,
                    "--format", "{{.Id}} {{.Created}}"])
        if not img and s not in tags and s not in used:
            continue            # серії ще немає — не заповнювати розділ порожнім
        e = {"tag": tag, "set_at": set_at.isoformat() if set_at else None}
        if img:
            iid, _, created = img.partition(" ")
            e["id"], e["created"] = iid[:19], created.strip()[:19]
        else:
            e["present"] = False
        if used.get(s):
            e["latest_runs_by_image"] = used[s]
        images[s] = e

    # Вартовий наступної серії (indexer/watch20.py). Тут він потрібен тому,
    # що сесія без SSH інакше не дізнається ні що вартовий живий, ні що T-0
    # настав: журнал systemd вона не бачить, а це подія, після якої в проєкті
    # міняється все (ops/inbox/0022).
    cur.execute("SELECT key, at, note FROM watch_state")
    ws = {r["key"]: r for r in cur.fetchall()}
    cur.execute("SELECT kind, repo, at FROM eco_events WHERE series = %s",
                (NEXT_SERIES,))
    ev = {(r["kind"], r["repo"]): r["at"] for r in cur.fetchall()}

    def _at(x):
        return x["at"].isoformat() if isinstance(x, dict) else (x.isoformat() if x else None)

    sweep = ws.get(f"oca_sweep_{NEXT_SERIES}")
    watch = {
        "series": NEXT_SERIES,
        "keynote": V20_KEYNOTE.isoformat(),
        "last_check": _at(ws.get(f"check_{NEXT_SERIES}")),
        # Дата виявлення, а не дата релізу: саме вона згодом і буде доказом.
        "platform_branch_seen": _at(ev.get(("branch_first", "odoo/odoo"))),
        "dockerhub_tag_seen": _at(ev.get(("dockerhub_tag", "library/odoo"))),
        "oca_last_sweep": _at(sweep),
        "oca_note": sweep["note"] if sweep else None,
        "oca_repos_with_branch": sum(
            1 for (k, r) in ev if k == "branch_first" and r.startswith("OCA/")),
    }

    du = shutil.disk_usage("/")
    mem_available_mb = None
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem_available_mb = int(line.split()[1]) // 1024
                break
    except Exception:
        pass

    data = {
        "generated_at": NOW.isoformat(),
        "commit": _cmd(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], "unknown"),
        "harvest": {
            "last_run": last_harvest.isoformat() if last_harvest else None,
            # Версія методики підрахунку. Цифри різних методик не можна класти
            # в один ряд — саме тому вона їде разом із ними, а не десь у доках.
            "method": method,
            "modules_by_series": by_series,
        },
        "runs": {
            "by_status": by_status,
            "tested": sum(by_status.values()),
            "total_modules": total_modules,
        },
        # Три вісі не згортаємо в одну цифру: not_installable і not_verifiable
        # не входять у runnable, тобто у знаменник відсотка встановлюваності.
        "states": states,
        "queue": {k: queue.get(k, 0) for k in ("queued", "running", "error")},
        "images": images,
        "next_series": watch,
        "disk_free_gb": round(du.free / 1024**3, 1),
        "mem_available_mb": mem_available_mb,
    }
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "status.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return data


def norm_pkg(name):
    """PEP 503: `python_slugify`, `Python-Slugify`, `python.slugify` — одне й те саме."""
    base = re.split(r"[<>=!~;\[]", str(name), 1)[0].strip()
    return re.sub(r"[-_.]+", "-", base).lower()


def env_facts(conn, series_list):
    """Довідники для секції залежностей. Будуються раз, а не на кожен модуль.

    Без списку ядра `base` і `account` потрапили б у «невідоме», і сторінка
    виглядала б так, ніби половина залежностей загубилась (ops/inbox/0016 A).
    """
    cur = conn.cursor()
    core = {s: set() for s in series_list}
    cur.execute("SELECT series, name FROM core_addons")
    for r in cur.fetchall():
        core.setdefault(r["series"], set()).add(r["name"])

    cur.execute("SELECT series, image FROM series_image")
    img_of = {r["series"]: r["image"] for r in cur.fetchall()}
    for s in series_list:
        img_of.setdefault(s, f"odoo:{s}")

    cur.execute("SELECT image_tag, kind, name FROM image_packages")
    pkgs = {}
    for r in cur.fetchall():
        pkgs.setdefault((r["image_tag"], r["kind"]), set()).add(norm_pkg(r["name"]))
    in_image = {s: {k: pkgs.get((img_of[s], k), set()) for k in ("python", "bin")}
                for s in series_list}

    # Зворотні залежності: скільки модулів цієї ж серії залежать від цього.
    # Для мейнтейнера це відповідь на «чи варто це портувати».
    cur.execute("""SELECT series, d AS name, count(*) c
                   FROM modules, unnest(depends) d
                   WHERE depends IS NOT NULL GROUP BY 1,2""")
    rev = {(r["series"], r["name"]): r["c"] for r in cur.fetchall()}
    return {"core": core, "in_image": in_image, "img_of": img_of, "rev": rev}


def deps_section(lang, series, row, mods_by_name, facts):
    """«Depends on» зі станом кожної залежності НА ЦІЙ САМІЙ серії.

    Саме стан на тій серії, яку читач зараз дивиться, робить секцію
    діагностичною, а не декоративною: модуль не може працювати на 19.0, якщо
    його залежності на 19.0 немає. Наш власний статус `dep` перетворюється з
    вироку на пояснення.
    """
    t = T[lang]
    deps = row.get("depends")
    if deps is None:
        return (f'<h2>{t["dep_h"]}</h2>'
                f'<p class="mut">{t["dep_unknown_manifest"]}</p>')
    if not deps:
        return f'<h2>{t["dep_h"]}</h2><p class="mut">{t["dep_none"]}</p>'

    core = facts["core"].get(series, set())
    rows, blocking = [], []
    for d in sorted(deps):
        if d in core:
            rows.append(f'<tr><td>{html.escape(d)}</td><td class="mut">{t["dep_core"]}</td>'
                        f'<td class="mut">{t["dep_core_note"]}</td></tr>')
            continue
        other = mods_by_name.get((series, d))
        if not other:
            rows.append(f'<tr><td>{html.escape(d)}</td>'
                        f'<td class="mut">{chip("not_verifiable", lang)}</td>'
                        f'<td class="mut">{t["dep_absent"].format(s=series)}</td></tr>')
            # auto_install-модулі не блокують: вони ставляться самі за наявності
            # інших, і їхня відсутність означає інше (ops/inbox/0016 E4).
            blocking.append(d)
            continue
        st, status = derive_state(dict(other, in_scope=series in TESTED_SERIES))
        when = other["run_at"].strftime("%Y-%m-%d") if other.get("run_at") else ""
        rows.append(
            f'<tr><td><a href="{loc(lang, f"/m/{other["repo"]}/{d}/")}">{html.escape(d)}</a></td>'
            f'<td>{chip(status if st == "verified" else st, lang)}</td>'
            f'<td class="mut">{when}</td></tr>')

    head = ""
    if blocking:
        head = (f'<p class="lead">'
                f'{t["dep_blocked"].format(n=len(blocking), m=len(deps), s=series)} '
                f'<b>{html.escape(", ".join(blocking[:4]))}</b>.</p>')
    return (f'<h2>{t["dep_h"]}</h2>{head}'
            f'<table><tr><th>{t["dep_col"]}</th><th>{t["m_status"]}</th>'
            f'<th>{t["dep_when"]}</th></tr>{"".join(rows)}</table>')


def ext_deps_section(lang, series, row, facts):
    """Зовнішні залежності з позначкою, чи є вони в НАШОМУ образі.

    Це те, що робить статус `env` зрозумілим замість загадкового: читач бачить
    не «щось не так із середовищем», а конкретний пакет, якого бракує.
    """
    t = T[lang]
    ext = row.get("ext_deps") or {}
    if not isinstance(ext, dict) or not any(ext.get(k) for k in ("python", "bin")):
        return ""
    have = facts["in_image"].get(series, {})
    out = []
    for kind in ("python", "bin"):
        for name in (ext.get(kind) or []):
            ok = norm_pkg(name) in have.get(kind, set())
            mark = t["dep_in_image"] if ok else t["dep_not_in_image"]
            cls = "muted" if ok else "serious"
            out.append(f'<tr><td>{kind}</td><td>{html.escape(str(name))}</td>'
                       f'<td class="c {cls}">{mark}</td></tr>')
    if not out:
        return ""
    return (f'<h2>{t["ext_h"]}</h2>'
            f'<p class="mut">{t["ext_note"].format(img=html.escape(facts["img_of"].get(series, "")))}</p>'
            f'<table><tr><th>{t["ext_kind"]}</th><th>{t["ext_pkg"]}</th>'
            f'<th>{t["ext_state"]}</th></tr>{"".join(out)}</table>')


def meta_of(v, series):
    """Метадані модуля з найновішої серії, де манифест реально розібрано.

    Для 16.0/17.0 чекаутів немає, тому брати «першу-ліпшу серію» означало б
    показати порожню картку тому, хто є на 16.0 і на 19.0.
    """
    return (next((v[s] for s in reversed(series)
                  if s in v and v[s].get("manifest_version")), None)
            or next((v[s] for s in reversed(series) if s in v), {}))


def fetch(conn):
    cur = conn.cursor()
    cur.execute("""
      SELECT m.repo, m.module, m.series, m.head_sha, m.last_commit,
             m.availability, m.installable, m.category, m.vendors, m.is_oca,
             m.license, m.summary, m.manifest_version, m.auto_install, m.application,
             m.depends, m.ext_deps, m.last_module_commit, m.commits_12m,
             m.top_authors, m.files_count,
             r.status, r.cause, r.detail, r.log_tail, r.created_at AS run_at,
             r.duration_ms, r.latest_version
      FROM modules m
      LEFT JOIN latest_runs r ON r.module_id = m.id
      ORDER BY m.repo, m.module, m.series
    """)
    rows = cur.fetchall()
    cur.execute("SELECT DISTINCT series FROM modules ORDER BY series")
    series = [r["series"] for r in cur.fetchall()]
    return rows, series


def browse_js(lang, shown_series):
    """Клієнтський браузер модулів: фільтри, сортування, групування, вікно.

    Три рішення, кожне не косметичне:

    * **Фільтри в URL.** Будь-який відбір стає посиланням, яке можна кинути в
      тред OCA: «ось усі ваші зламані модулі в account на 19.0». Для плану
      публікації це цінніше за сам фільтр.
    * **Віконна відмальовка.** 4,5 тис. рядків у DOM помітно гальмують на
      телефоні; малюємо ~200 і додаємо при скролі.
    * **Стан не кольором.** Статус лишається іконкою з підписом, напрямок
      сортування — стрілкою і `aria-sort`, а не відтінком. Інакше `env` і
      `fail` для дальтоніка нерозрізненні.
    """
    pfx = "" if lang == DEFAULT_LANG else "/uk"
    t = T[lang]
    return """
const PFX=%(pfx)s, SER=%(ser)s, CH=%(ch)s, SL=%(sl)s;
const T=%(tt)s;
const $=s=>document.querySelector(s), tb=$('#tbl tbody');
let DATA=null, view=[], sortKey='m', sortDir=1, drawn=0, group=false;

const P=new URLSearchParams(location.search);
const F={cat:P.get('cat')||'', vendor:P.get('vendor')||'', state:P.get('state')||'',
         series:P.get('series')||'', q:P.get('q')||''};
group = P.get('group')==='1';
if(P.get('sort')){ const [k,d]=P.get('sort').split(':'); sortKey=k; sortDir=d==='desc'?-1:1; }

function syncURL(){
  const u=new URLSearchParams();
  for(const k of ['cat','vendor','state','series','q']) if(F[k]) u.set(k,F[k]);
  if(group) u.set('group','1');
  if(sortKey!=='m'||sortDir!==1) u.set('sort',sortKey+':'+(sortDir<0?'desc':'asc'));
  const qs=u.toString();
  history.replaceState(null,'',qs?location.pathname+'?'+qs:location.pathname);
}

function cell(r,i){ const st=r.s[i]; if(!st) return '<td></td>';
  return '<td>'+(CH[st.k||'']||'')+'</td>'; }

function rowHTML(r){
  return '<tr><td><a href="'+PFX+'/m/'+r.r+'/'+r.m+'/">'+r.m+'</a> '
    +'<span class="mut">'+r.r+'</span></td>'
    +'<td class="mut">'+(r.v||[]).join(', ')+'</td>'
    +'<td class="mut">'+(r.c||'')+'</td>'
    +SER.map((_,i)=>cell(r,i)).join('')+'</tr>';
}

function apply(){
  const q=F.q.trim().toLowerCase();
  view=DATA.filter(r=>{
    if(q && !(r.m.includes(q)||r.r.includes(q))) return false;
    if(F.cat && (r.c||'')!==F.cat) return false;
    if(F.vendor && !(r.v||[]).includes(F.vendor)) return false;
    if(F.series){ const i=SER.indexOf(F.series); if(i<0||!r.s[i]) return false; }
    if(F.state){
      const idx=F.series?[SER.indexOf(F.series)]:SER.map((_,i)=>i);
      if(!idx.some(i=>r.s[i]&&(r.s[i].k===F.state||r.s[i].e===F.state))) return false;
    }
    return true;
  });
  const key=r=>sortKey==='m'?r.m:sortKey==='v'?((r.v||[])[0]||'\uffff')
    :sortKey==='c'?(r.c||'\uffff')
    :((r.s[+sortKey.slice(1)]||{}).k||'\uffff');
  view.sort((a,b)=>{const x=key(a),y=key(b);return x<y?-sortDir:x>y?sortDir:0;});
  $('#shown').textContent=T.shown.replace('{n}',view.length).replace('{m}',DATA.length);
  tb.innerHTML=''; drawn=0;
  if(!view.length){ tb.innerHTML='<tr><td colspan="'+(3+SER.length)+'" class="mut">'+T.none+'</td></tr>'; }
  else draw();
  syncURL();
}

function draw(){
  if(drawn>=view.length) return;
  const slice=view.slice(drawn,drawn+200);
  let html='';
  if(group){
    let last=drawn>0?(view[drawn-1].c||''):null;
    for(const r of slice){
      const c=r.c||'';
      if(c!==last){ html+='<tr class="grp"><td colspan="'+(3+SER.length)+'">'+(c||'—')+'</td></tr>'; last=c; }
      html+=rowHTML(r);
    }
  } else html=slice.map(rowHTML).join('');
  tb.insertAdjacentHTML('beforeend',html);
  drawn+=slice.length;
}

addEventListener('scroll',()=>{
  if(innerHeight+scrollY>document.body.offsetHeight-600) draw();
},{passive:true});

function fill(sel,vals,cur){
  const el=$(sel);
  for(const v of vals){ const o=document.createElement('option');
    o.value=v; o.textContent=v; if(v===cur) o.selected=true; el.appendChild(o); }
}

(async()=>{
  DATA=await (await fetch(PFX?'/modules.json':'modules.json')).json();
  fill('#f-cat',[...new Set(DATA.map(r=>r.c).filter(Boolean))].sort(),F.cat);
  fill('#f-vendor',[...new Set(DATA.flatMap(r=>r.v||[]))].sort(),F.vendor);
  fill('#f-series',SER,F.series);
  const states=new Set();
  DATA.forEach(r=>r.s.forEach(x=>x&&states.add(x.k)));
  const el=$('#f-state');
  [...states].sort().forEach(k=>{const o=document.createElement('option');
    o.value=k; o.textContent=SL[k]||k; if(k===F.state)o.selected=true; el.appendChild(o);});
  $('#q').value=F.q; $('#f-group').checked=group;
  markSort();
  apply();
})();

$('#q').addEventListener('input',e=>{F.q=e.target.value;apply();});
for(const [id,k] of [['#f-cat','cat'],['#f-vendor','vendor'],['#f-state','state'],['#f-series','series']])
  $(id).addEventListener('change',e=>{F[k]=e.target.value;apply();});
$('#f-group').addEventListener('change',e=>{group=e.target.checked;apply();});
$('#f-clear').addEventListener('click',()=>{
  for(const k of ['cat','vendor','state','series','q']) F[k]='';
  group=false; $('#q').value=''; $('#f-group').checked=false;
  document.querySelectorAll('.filters select').forEach(s=>s.value='');
  apply();
});

function markSort(){
  document.querySelectorAll('#tbl th[data-sort]').forEach(th=>{
    const on=th.dataset.sort===sortKey;
    th.setAttribute('aria-sort',on?(sortDir<0?'descending':'ascending'):'none');
    th.dataset.arrow=on?(sortDir<0?'\u2193':'\u2191'):'';
  });
}
document.querySelectorAll('#tbl th[data-sort]').forEach(th=>{
  const go=()=>{ const k=th.dataset.sort;
    if(k===sortKey) sortDir=-sortDir; else {sortKey=k;sortDir=1;}
    markSort(); apply(); };
  th.addEventListener('click',go);
  th.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}});
});
""" % {
        "pfx": json.dumps(pfx),
        "ser": json.dumps(shown_series),
        "ch": json.dumps({k or "": chip(k, lang) for k in STATUS_CLS}, ensure_ascii=False),
        "sl": json.dumps({k: state_label(k, lang) for k in
                          ("verified", "pending", "not_installable",
                           "not_verifiable", "absent")}, ensure_ascii=False),
        "tt": json.dumps({"shown": t["f_shown"], "none": t["f_none"]}, ensure_ascii=False),
    }


def home(lang, series, mods, present, ported, gap, counts, tested, repos_next, bd, bd_all):
    t = T[lang]
    newest = series[-1]
    prev = series[-2] if len(series) > 1 else None

    # Кожен опублікований відсоток НАЗИВАЄ свій знаменник. Не «71%
    # встановлюється», а «71% з 1 043 прогонабельних (з 1 192 усього: 149 не
    # встановлювані за манифестом, 800 чекають прогону)». Без цього показник
    # поїде разом із кількістю метапакетів, які до сумісності стосунку не мають,
    # і перший уважний читач зловить нас на арифметиці.
    ok = counts.get("ok", 0)
    verified = sum(c for s, c in counts.items() if s)
    denom_line = (
        t["denom"].format(
            ok=ok, ver=verified,
            pct=f"{ok / verified * 100:.0f}" if verified else "—",
            run=bd.get("runnable", 0), s=newest, total=bd.get("total", 0),
            noninst=bd.get("not_installable", 0), pending=bd.get("pending", 0))
        if verified else
        t["tested"].format(n=tested, m=len(mods)))

    tiles = "".join(
        f'<div class="t"><div class="k">{t["tile_modules_on"].format(s=s)}</div>'
        f'<div class="v">{present[s]}</div>'
        f'<div class="n">{t["tile_latest"] if s == newest else ""}</div></div>'
        for s in series[-3:])
    if prev:
        pct = (len(ported) / max(1, present[prev])) * 100
        tiles += (f'<div class="t"><div class="k">{t["tile_ported"].format(a=prev, b=newest)}</div>'
                  f'<div class="v">{pct:.1f}%</div>'
                  f'<div class="n">{t["tile_still"].format(n=len(gap))}</div></div>')

    # Одна смуга на серію, під однією шкалою. Серія, якої ми не проганяли,
    # отримує ЯВНУ смугу «не охоплено», а не порожнє місце: відсутність даних
    # це теж стан, і показати його чесніше, ніж приховати (ops/inbox/0017 C).
    # Кожна смуга підписана своїм знаменником у тому ж рядку.
    bars = []
    for s in series:
        b = bd_all.get(s, {})
        total = b.get("total", 0) or 1
        segs, parts = [], []
        for key, n, var in bar_parts(b, s):
            segs.append(f'<div style="flex:{n};background:var(--{var})" '
                        f'title="{st_label(key, lang)}: {n}">{n if n / total > .04 else ""}</div>')
            parts.append(f'<span><i class="sw" style="background:var(--{var})"></i>'
                         f'{STATUS_CLS.get(key, STATUS_CLS[None])[0]} '
                         f'{st_label(key, lang)} — {n}</span>')
        run = b.get("runnable", 0)
        ver = b.get("verified", 0)
        note = (t["bar_note"].format(v=ver, r=run, tot=b.get("total", 0))
                if run else t["bar_none"].format(tot=b.get("total", 0)))
        bars.append(f'<h3 class="bh">{s} <span class="mut">· {note}</span></h3>'
                    f'<div class="bar">{"".join(segs)}</div>'
                    f'<div class="lg">{"".join(parts)}</div>')
    seg = ""
    leg = ""
    bars_html = "".join(bars)
    bar_h = t["status_all_h"]

    months = (NOW.year - V19_RELEASED.year) * 12 + (NOW.month - V19_RELEASED.month)
    zero = ("Zero" if lang == "en" else "жодний") if not repos_next else str(repos_next)
    why_now = t["why_now_p"].format(
        next=NEXT_SERIES, keynote=(f"{V20_KEYNOTE:%d %B %Y}" if lang == "en"
                                   else f"{V20_KEYNOTE.day} вересня {V20_KEYNOTE.year}"),
        gap=f"{len(gap):,}".replace(",", " "), base=f"{present[prev]:,}".replace(",", " "),
        prev=prev, new=newest, months=months, zero=zero)

    chips = " ".join(f'<span class="vchip">{s}</span>' for s in series)

    # Таблиця модулів рендериться клієнтом з одного JSON: 4,5 тис. рядків це
    # ~350 КБ, які Caddy віддає стисненими, і браузер жує їх не помічаючи.
    # Серверного пошуку тут не буде ніколи — це рівно та частина, що робить
    # проєкт безкоштовним в обслуговуванні.
    shown_series = series[-4:]
    head = "".join(
        f'<th data-sort="s{i}" tabindex="0" aria-sort="none">{s}</th>'
        for i, s in enumerate(shown_series))
    body = f"""<h1>{t['h1']}</h1>
<p class="lead">{t['sub']}</p>
<p>{chips}</p>
<div class="tiles">{tiles}</div>
<div class="sec">
<h2>{t['why_exists_h']}</h2><p>{t['why_exists_p']}</p>
<h2>{t['why_now_h']}</h2><p>{why_now}</p>
<h2>{t['who_h']}</h2><p>{t['who_p']}</p>
<h2>{t['not_h']}</h2><p>{t['not_p']}</p>
</div>
<h2>{bar_h}</h2>
{bars_html}
<p class="mut">{denom_line}</p>

<h2>{t['modules_h']}</h2>
<div class="filters">
  <input id="q" placeholder="{t['search']}" autocomplete="off" aria-label="{t['search']}">
  <label>{t['f_cat']} <select id="f-cat"><option value="">{t['f_any']}</option></select></label>
  <label>{t['f_vendor']} <select id="f-vendor"><option value="">{t['f_any']}</option></select></label>
  <label>{t['f_state']} <select id="f-state"><option value="">{t['f_any']}</option></select></label>
  <label>{t['f_series']} <select id="f-series"><option value="">{t['f_any']}</option></select></label>
  <label class="cb"><input type="checkbox" id="f-group"> {t['f_group']}</label>
  <button type="button" id="f-clear">{t['f_clear']}</button>
</div>
<p class="mut" id="shown" aria-live="polite"></p>
<table id="tbl">
  <thead><tr>
    <th data-sort="m" tabindex="0" aria-sort="none">{t['col_module']}</th>
    <th data-sort="v" tabindex="0" aria-sort="none">{t['col_vendor']}</th>
    <th data-sort="c" tabindex="0" aria-sort="none">{t['col_cat']}</th>
    {head}
  </tr></thead>
  <tbody></tbody>
</table>
<p class="mut">{t['showing'].format(dataset=loc(lang, '/data/'))} {t['f_link']}</p>
<script>
{browse_js(lang, shown_series)}
</script>"""
    return page(lang, "/", f"{TITLE} — {t['h1']}", body,
                t["sub"][:180])


def check_css_vars():
    """Кожна `var(--x)` у згенерованому має бути визначена в `:root`.

    Смуги на головній були безбарвні тиждень, бо `var(--muted)` не існує:
    ім'я класу підставлялося туди, де потрібне ім'я змінної. Браузер такий
    промах не повідомляє, він просто не малює (ops/inbox/0021 B1). Перевірка
    коштує один прохід по вихідних файлах, тому робиться завжди, а не за
    прапорцем.
    """
    defined = set(re.findall(r"--([\w-]+)\s*:", CSS))
    bad, files = {}, 0
    for f in SITE.rglob("*.html"):
        files += 1
        for name in set(re.findall(r"var\(--([\w-]+)\)", f.read_text())):
            if name not in defined:
                bad.setdefault(name, str(f.relative_to(SITE)))
    if bad:
        raise RuntimeError(
            "невизначені змінні CSS: "
            + ", ".join(f"--{k} (напр. {v})" for k, v in sorted(bad.items()))
            + f"; у :root є {sorted(defined)}")
    return files


def check_bars(conn, bd_all):
    """У серії є прогони — у смузі мусять бути сегменти статусів.

    Незалежна перевірка: сторінка рахується з `mods`, а це — з БД. Рівність тут
    вимагати не можна, і це не недогляд: модуль з `installable=false` має
    прогін, але в смузі стоїть як `not_installable` (indexer/state.py, правило
    3), тому статуси з БД законно можуть не мати пари на сторінці. Незаконне —
    коли прогони є, а сегментів статусів нуль: рівно та регресія з
    ops/inbox/0021 B2.
    """
    cur = conn.cursor()
    cur.execute("SELECT series, status, count(*) c FROM latest_runs GROUP BY 1,2")
    db = {}
    for r in cur.fetchall():
        db.setdefault(r["series"], {})[r["status"]] = r["c"]
    for s in sorted(bd_all):
        page = set(bd_all[s].get("by_status") or {})
        have = set(db.get(s) or {})
        if have and not page:
            raise RuntimeError(
                f"смуга {s}: у БД {len(have)} різних статусів "
                f"({sorted(have)}), у смузі — жодного сегмента статусу")
        miss = sorted(have - page)
        if miss:
            print(f"  увага: у смузі {s} немає статусів {miss} — "
                  f"перевір, чи всі вони з'їдені not_installable",
                  file=sys.stderr)


def build():
    conn = connect()
    # Пишемо ПЕРШИМ і до перевірки на порожні дані: якщо даних нема, саме це
    # й треба показати назовні, а не мовчати.
    status_json(conn)
    rows, series = fetch(conn)
    if not rows:
        print("немає даних: спершу harvest.py"); return

    mods = {}
    for r in rows:
        mods.setdefault((r["repo"], r["module"]), {})[r["series"]] = r

    # Маршрут для краулера. Збираємо (шлях, дата останнього прогону) під час
    # генерації, а не вгадуємо після: критерій входження в карту мусить бути
    # ТИМ САМИМ, що й критерій `noindex` на сторінці, інакше карта обіцяє те, що
    # сторінка забороняє. Тому append стоїть рівно там, де рахується has_status.
    sm = []

    newest = series[-1]
    prev = series[-2] if len(series) > 1 else None
    present = {s: sum(1 for v in mods.values() if s in v) for s in series}
    gap = [k for k, v in mods.items() if prev in v and newest not in v] if prev else []
    ported = [k for k, v in mods.items() if prev in v and newest in v] if prev else []
    counts = {}
    for v in mods.values():
        st = (v.get(newest) or {}).get("status")
        counts[st] = counts.get(st, 0) + 1
    tested = sum(c for s, c in counts.items() if s)
    repos_next = len({r for (r, _), v in mods.items() if NEXT_SERIES in v})
    # Знаменник рахуємо ТІЛЬКИ по модулях, які взагалі є в найновішій серії:
    # «не перенесено» і «не встановлюється» — різні твердження.
    bd = breakdown([dict(v[newest], in_scope=newest in TESTED_SERIES)
                    for v in mods.values() if newest in v])
    bd_all = {s: breakdown([dict(v[s], in_scope=s in TESTED_SERIES)
                            for v in mods.values() if s in v]) for s in series}
    env = env_facts(conn, series)
    mods_by_name = {}
    for (repo, mod), v in mods.items():
        for s in v:
            mods_by_name[(s, mod)] = v[s]
    vendor_slugs = {}
    for v in mods.values():
        for ven in (meta_of(v, series).get("vendors") or []):
            vendor_slugs.setdefault(ven, slug(ven))

    for lang in LANGS:
        out_path(lang, "data").mkdir(parents=True, exist_ok=True)

    # ---------- головна ----------
    for lang in LANGS:
        out_path(lang, "index.html").write_text(
            home(lang, series, mods, present, ported, gap, counts, tested, repos_next, bd, bd_all))

    # ---------- сторінки модулів ----------
    search = []
    for (repo, mod), v in mods.items():
        has_status = any((v.get(s) or {}).get("status") for s in series)
        # lastmod = дата останнього прогону цього модуля. Саме вона, а не дата
        # генерації: інакше кожен щогодинний export кричав би краулеру «усі
        # 7 000 сторінок змінились», і повторний обхід приходив би тоді, коли
        # нічого не сталося, замість того дня, коли вердикт справді змінився.
        if has_status:
            runs_at = [ (v[s] or {}).get("run_at") for s in series if v.get(s) ]
            runs_at = [x for x in runs_at if x]
            sm.append((f"/m/{repo}/{mod}/", max(runs_at) if runs_at else None))
        for lang in LANGS:
            t = T[lang]
            cells = []
            for s in series:
                r = v.get(s)
                if not r:
                    cells.append(f'<tr><td><span class="vchip">{s}</span></td>'
                                 f"<td>{chip(None, lang)}</td>"
                                 f"<td class='mut'>{t['m_nobranch']}</td></tr>")
                    continue
                # Показуємо ПОХІДНИЙ стан, а не сирий статус: інакше модуль,
                # який ми свідомо не запускаємо, виглядав би «не протестованим»,
                # хоча причина відома й записана в його ж манифесті.
                st, status = derive_state(dict(r, in_scope=s in TESTED_SERIES))
                if st == "verified":
                    when = r["run_at"].strftime("%Y-%m-%d") if r.get("run_at") else "—"
                    det = html.escape(r.get("detail") or "")
                    ver = r.get("latest_version")
                    extra = f" <span class='mut'>· {html.escape(ver)}</span>" if ver else ""
                    note = f"{det} <span class='mut'>· {t['m_run'].format(d=when)}</span>{extra}"
                else:
                    note = f"<span class='mut'>{state_label(st, lang)}</span>"
                cells.append(f'<tr><td><span class="vchip">{s}</span></td>'
                             f"<td>{chip(status if st == 'verified' else st, lang)}</td>"
                             f"<td>{note}</td></tr>")
            logs = ""
            for s in reversed(series):
                r = v.get(s) or {}
                if r.get("log_tail"):
                    logs = (f"<h2>{t['m_log'].format(s=s)}</h2>"
                            f"<pre>{html.escape(r['log_tail'])}</pre>")
                    break
            meta = meta_of(v, series)
            meta_facts = ""
            if meta.get("summary"):
                meta_facts += f'<p class="lead">{html.escape(meta["summary"])}</p>'
            bits = []
            if meta.get("category"):
                bits.append(f'{t["f_cat"]}: <a href="{loc(lang, "/")}?cat='
                            f'{urllib.parse.quote(meta["category"])}">'
                            f'{html.escape(meta["category"])}</a>')
            for ven in (meta.get("vendors") or [])[:4]:
                bits.append(f'<a href="{loc(lang, "/")}?vendor='
                            f'{urllib.parse.quote(ven)}">{html.escape(ven)}</a>')
            if meta.get("license"):
                bits.append(html.escape(meta["license"]))
            if bits:
                meta_facts += f'<p class="mut">{" · ".join(bits)}</p>'
            # Секції йдуть ПІСЛЯ таблиці серій: спершу відповідь (статус),
            # потім пояснення (залежності, оточення, історія) — ops/inbox/0016 E6.
            newest_here = next((s for s in reversed(series) if s in v), None)
            cur_row = v.get(newest_here) or {}
            extra = ""
            if newest_here:
                extra += deps_section(lang, newest_here, cur_row, mods_by_name, env)
                extra += ext_deps_section(lang, newest_here, cur_row, env)
                rc = env["rev"].get((newest_here, mod), 0)
                if rc:
                    extra += (f'<h2>{t["rev_h"]}</h2><p>'
                              f'{t["rev_p"].format(n=rc, s=newest_here)}</p>')
                if cur_row.get("last_module_commit"):
                    au = ", ".join(cur_row.get("top_authors") or [])
                    extra += (
                        f'<h2>{t["git_h"]}</h2><table>'
                        f'<tr><td>{t["git_last"]}</td><td>'
                        f'{cur_row["last_module_commit"]:%Y-%m-%d}</td></tr>'
                        f'<tr><td>{t["git_work"]}</td><td>{cur_row.get("commits_12m") or 0}</td></tr>'
                        + (f'<tr><td>{t["git_authors"]}</td><td>{html.escape(au)}</td></tr>' if au else "")
                        + (f'<tr><td>{t["git_files"]}</td><td>{cur_row.get("files_count") or 0}</td></tr>'
                           if cur_row.get("files_count") else "")
                        + f'</table><p class="mut">{t["git_note"]}</p>')
            # Однорядкова матриця модуль × серія: це і є відповідь на питання,
            # з яким людина прийшла — «на чому це працює». Деталі нижче, у
            # таблиці; тут — за секунду.
            mx = ""
            for s in series:
                if s in v:
                    st_, status_ = derive_state(dict(v[s], in_scope=s in TESTED_SERIES))
                    c = chip(status_ if st_ == "verified" else st_, lang)
                else:
                    c = chip("absent", lang)
                mx += f'<div class="mx"><span class="vchip">{s}</span>{c}</div>'
            # Заповнене посилання будуємо по НАЙНОВІШІЙ серії з вердиктом:
            # саме її людина бачить першою і саме про неї сперечатиметься.
            rep_s = next((s for s in reversed(series)
                          if (v.get(s) or {}).get("status")), series[-1])
            rep_r = v.get(rep_s) or {}
            wrong = (f'<p class="mut"><strong>{t["m_wrong"]}</strong> {t["m_wrong_cta"]} '
                     f'<a href="{issue_url(repo, mod, rep_s, rep_r.get("status"), rep_r.get("cause"), rep_r.get("run_at"))}"'
                     f' rel="nofollow">{t["m_wrong_link"]}</a></p>') if has_status else ""
            b = (f'<h1>{mod}</h1><p class="mut">{t["m_in"]} '
                 f'<a href="{loc(lang, f"/r/{repo}/")}">{repo}</a> · '
                 f'<a href="https://github.com/OCA/{repo}">{t["m_source"]}</a></p>'
                 f'{meta_facts}'
                 f'<div class="mxr">{mx}</div>'
                 f'<table><tr><th>{t["m_series"]}</th><th>{t["m_status"]}</th>'
                 f'<th>{t["m_details"]}</th></tr>{"".join(cells)}</table>{extra}{logs}'
                 f'{wrong}')
            ld = {"@context": "https://schema.org", "@type": "SoftwareSourceCode",
                  "name": mod, "codeRepository": f"https://github.com/OCA/{repo}",
                  "applicationCategory": "Odoo module", "inLanguage": lang,
                  "url": f"{BASE}{loc(lang, f'/m/{repo}/{mod}/')}",
                  "dateModified": NOW.isoformat()}
            d = out_path(lang, f"m/{repo}/{mod}")
            d.mkdir(parents=True, exist_ok=True)
            title = (f"{mod} — Odoo version compatibility" if lang == "en"
                     else f"{mod} — сумісність з версіями Odoo")
            desc = (f"Does {mod} from {repo} install on each Odoo series." if lang == "en"
                    else f"Чи встановлюється модуль {mod} з {repo} на версії Odoo.")
            (d / "index.html").write_text(
                page(lang, f"/m/{repo}/{mod}/", title, b, desc, ld,
                     noindex=not has_status,
                     feed=f"/feed/{newest_here}.xml" if newest_here else "/feed.xml"))
        # Метадані є лише там, де є чекаут (18.0/19.0). Беремо найновішу
        # серію, у якій манифест реально розібрано, інакше картка модуля
        # виглядала б порожньою через те, що першою трапилась 16.0.
        any_row = next((v[s] for s in reversed(series)
                        if s in v and v[s].get("manifest_version")), {})
        if not any_row:
            any_row = next((v[s] for s in reversed(series) if s in v), {})
        search.append({
            "r": repo, "m": mod,
            "c": any_row.get("category") or "",
            "v": list(any_row.get("vendors") or []),
            "s": [cell_state(v[s]) if s in v else None for s in series[-4:]],
        })

    # мовно-нейтральний індекс пошуку: коди статусів, підписи рендерить сторінка
    (SITE / "modules.json").write_text(
        json.dumps(search, ensure_ascii=False, separators=(",", ":")))

    # ---------- сторінки репозиторіїв ----------
    byrepo = {}
    for (repo, mod), v in mods.items():
        byrepo.setdefault(repo, []).append((mod, v))
    for repo, items in byrepo.items():
        repo_has_status = any((v.get(s) or {}).get("status") for _, v in items for s in series)
        # Сторінка репозиторію — ДРУГИЙ маршрут, незалежний від sitemap і від
        # скриптів: вона перелічує всі модулі репозиторію звичайними <a href>.
        # Тому шлях sitemap → /r/… → /m/… працює навіть для краулера, який не
        # виконує JS, а таблиця на головній малюється саме скриптом і віконно
        # (у DOM ~200 рядків із 4 500).
        if repo_has_status:
            ra = [ (v.get(s) or {}).get("run_at") for _, v in items for s in series ]
            ra = [x for x in ra if x]
            sm.append((f"/r/{repo}/", max(ra) if ra else None))
        for lang in LANGS:
            t = T[lang]
            head = "".join(f"<th>{s}</th>" for s in series[-4:])
            rws = "".join(
                f'<tr><td><a href="{loc(lang, f"/m/{repo}/{m}/")}">{m}</a></td>' +
                "".join(f"<td>{chip((v.get(s) or {}).get('status'), lang)}</td>"
                        for s in series[-4:]) + "</tr>"
                for m, v in sorted(items))
            d = out_path(lang, f"r/{repo}")
            d.mkdir(parents=True, exist_ok=True)
            title = (f"{repo} — module compatibility" if lang == "en"
                     else f"{repo} — сумісність модулів")
            (d / "index.html").write_text(page(
                lang, f"/r/{repo}/", title,
                f'<h1>{repo}</h1><p class="mut">{t["r_modules"].format(n=len(items))} · '
                f'<a href="https://github.com/OCA/{repo}">GitHub</a></p>'
                f'<table><tr><th>{t["col_module"]}</th>{head}</tr>{rws}</table>',
                title, noindex=not repo_has_status))

    # ---------- датасет ----------
    with open(SITE / "data" / "modules.csv", "w", newline="") as f:
        w = csv.writer(f)
        # У датасеті поруч і сирі вісі, і похідний стан: сирі — щоб можна було
        # перерахувати по-своєму, похідний — щоб цифри збігалися з сайтом.
        w.writerow(["repo", "module", "category", "vendors", "license", "is_oca"]
                   + [f"{s}_present" for s in series]
                   + [f"{s}_state" for s in series]
                   + [f"{s}_status" for s in series]
                   + [f"{s}_cause" for s in series]
                   + [f"{s}_installable" for s in series]
                   + [f"{s}_version" for s in series])
        for (repo, mod), v in sorted(mods.items()):
            meta = meta_of(v, series)

            def st_of(s):
                if s not in v:
                    return ""
                return derive_state(dict(v[s], in_scope=s in TESTED_SERIES))[0]

            w.writerow([repo, mod, meta.get("category") or "",
                        ";".join(meta.get("vendors") or []),
                        meta.get("license") or "",
                        "" if meta.get("is_oca") is None else int(meta["is_oca"])]
                       + [1 if s in v else 0 for s in series]
                       + [st_of(s) for s in series]
                       + [(v.get(s) or {}).get("status") or "" for s in series]
                       + [(v.get(s) or {}).get("cause") or "" for s in series]
                       + ["" if (v.get(s) or {}).get("installable") is None
                          else int(v[s]["installable"]) for s in series]
                       + [(v.get(s) or {}).get("latest_version") or "" for s in series])
    # schema.org/Dataset — те, на що дивляться і Google Dataset Search, і
    # LLM-краулери. Береться з фактичних даних, а не вписується: temporalCoverage
    # рахується з реальних дат прогонів, тому не старіє й не бреше.
    #
    # `distribution` називає ОБИДВА формати, бо це два різні способи вжитку:
    # CSV відкривають у таблиці й цитують, JSON читає пошук у браузері.
    def dataset_ld(lang):
        first = min((r["run_at"] for r in rows if r.get("run_at")), default=None)
        last = max((r["run_at"] for r in rows if r.get("run_at")), default=None)
        cov = (f"{first:%Y-%m-%d}/{last:%Y-%m-%d}" if first and last else None)
        ld = {
            "@context": "https://schema.org", "@type": "Dataset",
            "name": TITLE,
            "description": ("Which OCA modules actually install on which Odoo series, "
                            "from real `odoo -i` runs in a clean database."
                            if lang == "en" else
                            "Які модулі OCA справді встановлюються на які серії Odoo — "
                            "за результатами реальних прогонів `odoo -i` у чистій базі."),
            "url": f"{BASE}{loc(lang, '/data/')}",
            "license": "https://creativecommons.org/licenses/by/4.0/",
            "isAccessibleForFree": True,
            "inLanguage": lang,
            "creator": {"@type": "Person", "name": "Maintainer, " + TITLE,
                        "email": CONTACT},
            "keywords": ["Odoo", "OCA", "module compatibility", "migration",
                         "install verification"],
            "variableMeasured": ["module", "series", "status", "cause"],
            "distribution": [
                {"@type": "DataDownload", "encodingFormat": "text/csv",
                 "contentUrl": f"{BASE}/data/modules.csv"},
                {"@type": "DataDownload", "encodingFormat": "application/json",
                 "contentUrl": f"{BASE}/modules.json"},
            ],
            "dateModified": NOW.date().isoformat(),
        }
        if cov:
            ld["temporalCoverage"] = cov
        return ld

    for lang in LANGS:
        t = T[lang]
        out_path(lang, "data").mkdir(parents=True, exist_ok=True)
        out_path(lang, "data/index.html").write_text(page(
            lang, "/data/", f"{t['d_h1']} — {TITLE}",
            f'<h1>{t["d_h1"]}</h1><p class="lead">{t["d_intro"]}</p><ul>'
            f'<li><a href="/data/modules.csv">{t["d_csv"]}</a></li>'
            f'<li><a href="/modules.json">{t["d_json"]}</a></li></ul>',
            t["d_intro"], jsonld=dataset_ld(lang)))

    # ---------- методологія ----------
    for lang in LANGS:
        t = T[lang]
        tbl = ('<table><tr><th>' + t["m_status"] + "</th><th>"
               + ("Meaning" if lang == "en" else "Значення") + "</th></tr>"
               + "".join(f"<tr><td>{chip(k, lang)}</td><td>{st_label(k, lang)}</td></tr>"
                         for k in ("ok", "warn", "dep", "env", "fail", "timeout"))
               + "</table>")
        out_path(lang, "methodology.html").write_text(page(
            lang, "/methodology.html", f"{t['meth_h1']} — {TITLE}",
            f"<h1>{t['meth_h1']}</h1>" + METHODOLOGY[lang].format(
                table=tbl, issues=f"{REPO_URL}/issues", contact=CONTACT),
            "How module compatibility is verified." if lang == "en"
            else "Як саме перевіряється сумісність модулів Odoo.",
            jsonld=dataset_ld(lang)))

    # ---------- знак ----------
    (SITE / "favicon.svg").write_text(FAVICON)

    # ---------- robots.txt ----------
    # Нічого не забороняємо: GPTBot та інші LLM-краулери — основний канал проєкту.
    # Тонкі сторінки тримаються поза індексом посторінковим noindex (див. page()).
    # sitemap пишеться в кінці build(): у нього входять сторінки модулів,
    # репозиторіїв і вендорів, а вони генеруються нижче.
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")

    # ---------- Atom-фіди ----------
    # Фід по вендору — головний з трьох: він потрапляє в чужий робочий процес
    # без листа й без реєстрації. У Tecnativa 328 модулів на 19.0; вони
    # підписуються раз і бачать свої поломки раніше за користувачів.
    events = feed_entries(conn)
    cur = conn.cursor()
    cur.execute("SELECT max(at) a FROM state_changes")
    last_any = cur.fetchone()["a"]
    (SITE / "feed").mkdir(parents=True, exist_ok=True)
    (SITE / "feed" / "vendor").mkdir(parents=True, exist_ok=True)

    (SITE / "feed.xml").write_text(atom(
        events[:FEED_MAX], f"{TITLE} — module state changes", "/feed.xml", "feed",
        fallback=last_any))

    for s in series:
        sub = [e for e in events if e["series"] == s][:FEED_MAX]
        (SITE / "feed" / f"{s}.xml").write_text(atom(
            sub, f"{TITLE} — Odoo {s}", f"/feed/{s}.xml", f"feed/{s}",
            fallback=last_any))

    for ven, sl in sorted(vendor_slugs.items(), key=lambda kv: kv[1]):
        sub = [e for e in events if ven in e["vendors"]][:FEED_MAX]
        (SITE / "feed" / "vendor" / f"{sl}.xml").write_text(atom(
            sub, f"{TITLE} — {ven}", f"/feed/vendor/{sl}.xml", f"feed/vendor/{sl}",
            fallback=last_any))

    # ---------- сторінки вендорів ----------
    # Потрібні не заради самих сторінок, а щоб фід вендора взагалі можна було
    # знайти: читалка підхоплює його з <link rel="alternate"> у <head>.
    for ven, sl in vendor_slugs.items():
        items = sorted((k, v) for k, v in mods.items()
                       if ven in (meta_of(v, series).get("vendors") or []))
        if items:
            va = [ (v.get(s) or {}).get("run_at") for _, v in items for s in series ]
            va = [x for x in va if x]
            sm.append((f"/v/{sl}/", max(va) if va else None))
        for lang in LANGS:
            tt = T[lang]
            head_v = "".join(f"<th>{s}</th>" for s in series[-4:])
            rws = "".join(
                f'<tr><td><a href="{loc(lang, f"/m/{repo}/{m}/")}">{m}</a> '
                f'<span class="mut">{repo}</span></td>' +
                "".join(f"<td>{chip((v.get(s) or {}).get('status'), lang)}</td>"
                        for s in series[-4:]) + "</tr>"
                for (repo, m), v in items)
            d = out_path(lang, f"v/{sl}")
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text(page(
                lang, f"/v/{sl}/", f"{ven} — {TITLE}",
                f'<h1>{html.escape(ven)}</h1>'
                f'<p class="mut">{tt["r_modules"].format(n=len(items))} · '
                f'<a href="{loc(lang, "/")}?vendor={urllib.parse.quote(ven)}">'
                f'{tt["f_vendor"]}</a> · '
                f'<a href="/feed/vendor/{sl}.xml">Atom</a></p>'
                f'<table><tr><th>{tt["col_module"]}</th>{head_v}</tr>{rws}</table>',
                f"{ven}: Odoo module compatibility.",
                noindex=not items, feed=f"/feed/vendor/{sl}.xml"))

    # ---------- llms.txt (один, англійською, у корені) ----------
    # Формулювання залежить від наявності прогонів: заявляти «install runs»
    # при tested=0 — неправда, і саме цей файл цитують LLM.
    if tested:
        what = ("Which OCA modules actually install on which Odoo version, "
                "from real install runs.")
    else:
        what = (f"Index of which Odoo version branches each OCA module has, collected from "
                f"git. Actual install verification is in progress: tested {tested} "
                f"of {len(mods)}.")
    (SITE / "llms.txt").write_text(f"""# {TITLE}
{what}
English: {BASE}/ · Ukrainian: {BASE}/uk/
Data: {BASE}/data/modules.csv (CSV, CC BY 4.0)
Module page: {BASE}/m/<repo>/<module>/  (Ukrainian: {BASE}/uk/m/<repo>/<module>/)
Methodology: {BASE}/methodology.html
Last updated: {NOW.isoformat()}
Modules indexed: {len(mods)}. Tested: {tested}. Series: {', '.join(series)}.
Independent project. Not affiliated with Odoo S.A. or the Odoo Community Association.
""")

    # ---------- прибирання сторінок, яких більше немає в даних ----------
    #
    # export ЛИШЕ писав файли й ніколи не видаляв, тому сайт накопичував
    # сторінки модулів і репозиторіїв, вилучених з індексу. 21.08.2026 таких
    # знайшлося 16 — і серед них рівно ті три фантоми, які CLAUDE.md забороняє
    # публікувати (`server-auth/readme`, `stock-logistics-transport/lessons`,
    # `delivery-carrier/delivery_carrier_label_gls`), плюс два репозиторії,
    # скошені жнецем із OCA (`interface-github`, `infrastructure-dns`).
    # Каддi віддавав їх із кодом 200. Виявились вони випадково: у них лишився
    # старий `content="noindex"` без `follow`, тобто мовчали б і далі.
    #
    # Це та сама асиметрія, що й у harvest: додавати без видалення означає, що
    # помилка живе назавжди. Тому очікуваний набір рахуємо З ДАНИХ, а не з того,
    # що писали цього разу.
    #
    # Запобіжник як у жнеця (ops/inbox/0004 R1): якщо під видалення підпадає
    # більше 2% сторінок або більше 100, НІЧОГО не видаляємо і кричимо. Порожній
    # чи поламаний зріз не має права знести опублікований сайт.
    keep = {
        "m": {f"{r}/{m}" for (r, m) in mods},
        "r": set(byrepo),
        "v": set(vendor_slugs.values()),
    }
    stale, total_pages = [], 0
    for lang in LANGS:
        for kind, names in keep.items():
            root = out_path(lang, kind)
            if not root.exists():
                continue
            for idx in root.rglob("index.html"):
                total_pages += 1
                rel = idx.parent.relative_to(root).as_posix()
                if rel not in names:
                    stale.append(idx.parent)
    cap = max(100, int(total_pages * 0.02))
    if not stale:
        print("прибирання: зайвих сторінок немає")
    elif len(stale) > cap:
        print(f"УВАГА: під видалення підпадає {len(stale)} сторінок із {total_pages} "
              f"(межа {cap}) — НЕ видаляю. Схоже на поламаний зріз, не на прибирання.")
        for d in stale[:10]:
            print(f"   {d}")
    else:
        for d in stale:
            shutil.rmtree(d, ignore_errors=True)
        print(f"прибрано зайвих сторінок: {len(stale)} із {total_pages} "
              f"({', '.join(str(d.relative_to(SITE)) for d in stale[:6])}"
              f"{' …' if len(stale) > 6 else ''})")

    # ---------- sitemap: індекс + файли по типах сторінок ----------
    #
    # Досі в карті було 6 записів: головна, методологія, датасет — по дві мови.
    # Сторінок модулів у ній не було НІ ОДНОЇ, і це найдорожча діра з можливих:
    # питання, з яким приходить партнер, звучить «does module X work on Odoo 19»,
    # і сторінка модуля — єдина в світі відповідь на нього. Дійти до неї краулер
    # не міг: таблиця на головній малюється скриптом і віконно (у DOM ~200 рядків
    # із 4 500), тобто посилань на решту в розмітці просто немає.
    #
    # Один <url> на мову з повним набором hreflang-альтернатив у кожному: інакше
    # англійська й українська версії конкурують між собою як дублікати.
    #
    # Ліміт стандарту — 50 000 URL і 50 МБ на файл. Ріжемо по 40 000 із запасом:
    # у вересні додається пʼята серія, і перевищити ліміт саме тоді, коли карта
    # найпотрібніша, було б у стилі решти дефектів цього тижня.
    SM_CHUNK = 40_000

    def _urlset(entries):
        out = []
        for pth, lastmod in entries:
            alt = "".join(
                f'<xhtml:link rel="alternate" hreflang="{lg}" href="{BASE}{loc(lg, pth)}"/>'
                for lg in LANGS
            ) + (f'<xhtml:link rel="alternate" hreflang="x-default" '
                 f'href="{BASE}{loc(DEFAULT_LANG, pth)}"/>')
            lm = f"<lastmod>{lastmod:%Y-%m-%d}</lastmod>" if lastmod else ""
            for lg in LANGS:
                out.append(f"<url><loc>{BASE}{loc(lg, pth)}</loc>{alt}{lm}</url>")
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
                ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
                + "\n".join(out) + "\n</urlset>\n")

    static_pages = [(pth, NOW.date()) for pth in ("/", "/methodology.html", "/data/")]
    groups = {
        "pages": static_pages,
        "modules": [e for e in sm if e[0].startswith("/m/")],
        "repos": [e for e in sm if e[0].startswith("/r/")],
        "vendors": [e for e in sm if e[0].startswith("/v/")],
    }
    children = []
    for name, entries in groups.items():
        if not entries:
            continue
        chunks = [entries[i:i + SM_CHUNK] for i in range(0, len(entries), SM_CHUNK)]
        for n, chunk in enumerate(chunks, 1):
            fn = f"sitemap-{name}.xml" if len(chunks) == 1 else f"sitemap-{name}-{n}.xml"
            (SITE / fn).write_text(_urlset(chunk))
            newest = max((lm for _, lm in chunk if lm), default=NOW.date())
            children.append((fn, newest, len(chunk) * len(LANGS)))
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(f"<sitemap><loc>{BASE}/{fn}</loc>"
                     f"<lastmod>{lm:%Y-%m-%d}</lastmod></sitemap>"
                     for fn, lm, _ in children)
        + "\n</sitemapindex>\n")
    print("sitemap: " + " · ".join(f"{fn} — {n} URL" for fn, _, n in children)
          + f" · разом {sum(n for _, _, n in children)}")

    check_bars(conn, bd_all)
    files = check_css_vars()
    print(f"згенеровано: {len(mods)} модулів × {len(LANGS)} мови, "
          f"{len(byrepo)} репозиторіїв → {SITE}")
    print(f"перевірено: сегменти смуг сходяться з total, "
          f"змінні CSS визначені ({files} файлів)")
    conn.close()


if __name__ == "__main__":
    build()
