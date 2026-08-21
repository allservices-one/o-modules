#!/usr/bin/env python3
"""requirements-declared.txt із манифестів — скриптом, а не руками.

Список пакетів для похідного образу мусить приходити **з `external_dependencies`
модулів**, а не з нашого розсуду: інакше ми перевіряємо власне середовище замість
того, яке декларує вендор, і результат перестає щось означати
(див. `docker/deps/Dockerfile`).

Досі файл робився ad hoc запитом до БД. Це та сама пастка, яку закрив
`bin/mkconstraints.sh`: «руками» означає «по-різному для 16.0 і для 20.0 у
вересні, під тиском». 21.08.2026 розбір манифестів 16.0 додав 35 назв, яких не
оголошувала жодна інша серія — без перегенерації похідний образ 16.0 не покривав
би саме те, під що будується. Заодно виявилось, що зроблений руками файл мав 133
назви замість 157 навіть для вже пройдених серій.

    python3 indexer/declared.py                 # усі серії з БД
    python3 indexer/declared.py 16.0 17.0       # лише названі

## Як обирається один рядок на пакет

Dockerfile ставить кожен рядок окремо, тому два рядки з тією самою назвою — це
дві спроби поставити те саме з різними межами, і друга перебиває першу. Тому на
пару (назва, маркер оточення) лишається один рядок.

**Маркер оточення (`; python_version < '3.12'`) частиною ключа є навмисно.**
`endesive<=2.18.5 ; python_version < '3.12'` і `endesive ; python_version >= '3.12'`
взаємно виключні: pip сам пропускає той, чия умова не виконується. Злити їх в
один рядок означало б лишити пакет невстановленим на половині серій.

Побічний ефект, який ми приймаємо свідомо: маркери різних модулів не обов'язково
взаємно виключні. `pymssql` приходить у чотирьох варіантах, і на py3.11 умови
`< '3.12'` та `> '3.10'` виконуються обидві — кожен рядок ставиться окремо, тому
перемагає останній. Вирівнювати це нам нема з чого: суперечність у самих
оголошеннях OCA, а наша робота — поставити те, що вендори оголосили, а не
вирішувати за них, яка межа правильна.

Серед варіантів з тим самим ключем беремо той, що задовольняє **найбільше**
оголошень:

* якщо є хоч один без верхньої межі (гола назва або тільки `>=`/`>`) — беремо
  голу назву, інакше найчастіший з них. Найновіша версія задовольняє і голі
  оголошення, і будь-яку нижню межу;
* якщо ВСІ варіанти з верхньою межею (`==`, `<`, `<=`, `~=`) — задовольнити всіх
  неможливо. Беремо найчастіший (далі: найдовший запис, далі алфавіт) і
  **друкуємо розбіжність**: це факт про екосистему, не шум.
"""
import os, re, sys, collections, datetime
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT

OUT = ROOT / "docker" / "deps" / "requirements-declared.txt"
NAME = re.compile(r"^[^<>=!~;\[\s]+")
CAP = re.compile(r"(==|<=|<|~=)")


def split(spec):
    """→ (назва, маркер, чи є верхня межа). Назва нормалізована для звірки."""
    spec = spec.strip()
    req, _, marker = spec.partition(";")
    m = NAME.match(req)
    if not m:
        return None, None, False
    name = m.group(0).lower().replace("_", "-")
    return name, marker.strip(), bool(CAP.search(req))


def _ver(spec):
    """Найбільше число-версія в записі, як кортеж. Потрібне лише для розв'язання
    нічиї між пінами: при рівній частоті новіший пін — краща ставка, бо модуль,
    що тримає новішу версію, з більшою ймовірністю досі підтримується."""
    best = ()
    for m in re.finditer(r"\d+(?:\.\d+)+", spec):
        v = tuple(int(x) for x in m.group(0).split("."))
        best = max(best, v)
    return best


def pick(variants):
    """variants: Counter{сирий рядок: скільки модулів}. → обраний рядок."""
    uncapped = {s: n for s, n in variants.items() if not split(s)[2]}
    pool = uncapped or variants
    if uncapped:
        bare = {s: n for s, n in uncapped.items() if not re.search(r"[<>=!~]", s.partition(";")[0])}
        pool = bare or uncapped
    return sorted(pool.items(),
                  key=lambda kv: (-kv[1], tuple(-x for x in _ver(kv[0])), -len(kv[0]), kv[0])
                  )[0][0]


def main():
    series = sys.argv[1:]
    conn = connect()
    cur = conn.cursor()
    q = """SELECT series, jsonb_array_elements_text(ext_deps->'python') AS pkg
           FROM modules WHERE ext_deps ? 'python'"""
    if series:
        cur.execute(q + " AND series = ANY(%s)", (series,))
    else:
        cur.execute(q)
    rows = cur.fetchall()
    conn.close()

    groups = collections.defaultdict(collections.Counter)
    where = collections.defaultdict(set)
    for r in rows:
        spec = (r["pkg"] or "").strip()
        name, marker, _ = split(spec)
        if not name:
            continue
        groups[(name, marker)][spec] += 1
        where[name].add(r["series"])

    chosen, hard = {}, []
    for key, variants in groups.items():
        chosen[key] = pick(variants)
        if len(variants) > 1 and all(split(s)[2] for s in variants):
            hard.append((key, dict(variants), chosen[key]))

    src = ", ".join(sorted(series)) if series else "усі серії"
    head = [
        f"# Згенеровано indexer/declared.py {datetime.date.today().isoformat()}"
        f" з external_dependencies манифестів OCA ({src}).",
        "# НЕ правити руками: файл описує те, що оголосили вендори, а не те,",
        "# що нам зручно мати в образі. Перегенерувати після кожного manifests.py",
        "# на новій серії — інакше похідний образ не покриває те, під що будується.",
    ]
    body = [chosen[k] for k in sorted(chosen)]
    OUT.write_text("\n".join(head + body) + "\n")

    print(f"declared: {OUT} — {len(body)} рядків із {len(rows)} оголошень "
          f"({len(where)} унікальних назв)", file=sys.stderr)
    for (name, marker), variants, got in sorted(hard):
        print(f"  несумісні межі у {name}: {variants} → {got!r}", file=sys.stderr)
    solo = collections.Counter(next(iter(where[n])) for n in where if len(where[n]) == 1)
    if solo:
        print("  назв лише в одній серії: "
              + ", ".join(f"{s} — {c}" for s, c in sorted(solo.items())), file=sys.stderr)


if __name__ == "__main__":
    main()
