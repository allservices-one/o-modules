"""Похідний стан модуля — ЄДИНЕ місце, де він обчислюється.

Три вісі в БД відповідають на різні питання (ops/inbox/0010):

    availability — чи можемо ми модуль дістати       (наша спроможність)
    installable  — чи заявляє сам модуль, що ставиться (факт із манифеста)
    runs.status  — що сталося, коли ми запустили      (результат прогону)

Показувати три колонки користувачеві не треба, тому є похідний `state`. Але
рахуватися він мусить в одному місці: щойно сайт, датасет і `/status.json`
почнуть виводити його кожен по-своєму, вони розійдуться — і першим це помітить
не хтось із нас, а читач, який зіставить сторінку з CSV.

Головне правило звідси ж: `not_installable` і `not_verifiable` **не входять у
знаменник** відсотка встановлюваності й не входять у чисельник «зламаних».
Інакше показник поїде разом із кількістю метапакетів, а це не має жодного
стосунку до сумісності з версією.
"""

# Стани, для яких прогін має сенс. Усе інше — не «ще не перевірили», а
# «перевіряти нічого»: причина відома наперед і не є результатом прогону.
RUNNABLE = ("pending", "verified")

LABELS = {
    "verified":        ("Перевірено прогоном",        "Verified by install run"),
    "pending":         ("Прогін заплановано",         "Run pending"),
    "not_installable": ("Не встановлюваний за манифестом", "Not installable by manifest"),
    "not_verifiable":  ("Перевірити неможливо",       "Cannot be verified"),
    "absent":          ("Немає в цій серії",          "Not in this series"),
}


def derive_state(row):
    """→ (state, status)

    `row` — рядок модуля з приєднаним останнім прогоном: очікуються ключі
    `availability`, `installable`, `status` (може бути None).

    Порядок перевірок не довільний:
      1. немає гілки — питання не стоїть узагалі;
      2. платний — прогнати не можемо і ніколи не зможемо, ліцензії немає;
      3. манифест каже «не встановлюваний» — це факт про модуль, а не наш
         висновок, і він сильніший за відсутність прогону;
      4. є прогін — показуємо його результат;
      5. решта — чекає на прогін.
    Якби (3) стояло після (4), модуль з installable=false, який колись
    прогнали до введення цієї вісі, показувався б як звичайний результат.
    """
    if row.get("absent"):
        return "absent", None
    if row.get("availability") == "store_paid":
        return "not_verifiable", None
    if row.get("installable") is False:
        return "not_installable", None
    status = row.get("status")
    if status:
        return "verified", status
    return "pending", None


def label(state, lang="uk"):
    pair = LABELS.get(state)
    return pair[0 if lang == "uk" else 1] if pair else state


def denominator(rows):
    """Скільки модулів реально можна перевірити — знаменник для відсотків.

    Кожен опублікований відсоток мусить називати свій знаменник: не
    «71% встановлюється», а «71% з 1 043 прогонабельних (з 1 192 усього:
    149 не встановлювані за манифестом)». Ця функція дає перше число,
    `breakdown()` — решту підпису.
    """
    return sum(1 for r in rows if derive_state(r)[0] in RUNNABLE)


def breakdown(rows):
    """Повний розклад для підпису під відсотком: {state: n} плюс total."""
    out = {}
    for r in rows:
        st = derive_state(r)[0]
        out[st] = out.get(st, 0) + 1
    out["total"] = len(rows)
    out["runnable"] = sum(out.get(s, 0) for s in RUNNABLE)
    return out
