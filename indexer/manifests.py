#!/usr/bin/env python3
"""Метадані з `__manifest__.py`: категорія, вендор, ліцензія, залежності.

Запускати ПІСЛЯ `sync_repos.sh` — потрібні файли на диску. Для серій без
чекаутів (16.0, 17.0) поля лишаються NULL, і це чесно: краще порожньо, ніж
вигадано.

Ніякого `eval` і ніякого `import`: манифест — це чужий код, який ми не
виконуємо. Тільки `ast`.
"""
import ast, json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT, SERIES

# Парасолька OCA стоїть в `author` майже кожного модуля, тому як «вендор» вона
# нічого не розрізняє. Цікаве саме друге ім'я — компанія-контриб'ютор: воно
# показує, хто реально тягне екосистему. Зберігаємо і сирий рядок, і розібране.

# Сміття, яке трапляється в author замість назви компанії.
NOT_A_VENDOR = {"", "-", "n/a", "none", "unknown", "odoo sa", "odoo s.a.",
                "odoo", "various", "others", "community"}


def parse_manifest(path):
    """→ (dict, error). Розбираємо ПОКЛЮЧОВО, а не цілим літералом.

    `ast.literal_eval` на всьому файлі падає, якщо десь у манифесті є виклик
    чи конкатенація — а таких у OCA чимало (`'description': open(...).read()`,
    склеєні рядки, звернення до змінних). Цілісний розбір втратив би всі поля
    такого модуля. Тому беремо словник як AST і обчислюємо кожне значення
    окремо: те, що не є літералом, просто пропускаємо.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {}, f"read: {e}"
    try:
        node = ast.parse(src, mode="eval").body
    except SyntaxError as e:
        return {}, f"syntax: line {e.lineno}"
    if not isinstance(node, ast.Dict):
        return {}, "не словник"
    out, skipped = {}, []
    for k, v in zip(node.keys, node.values):
        try:
            key = ast.literal_eval(k)
        except Exception:
            continue
        if not isinstance(key, str):
            continue
        try:
            out[key] = ast.literal_eval(v)
        except Exception:
            skipped.append(key)          # виклик, змінна, f-рядок — не літерал
    return out, (f"не літерали: {','.join(skipped[:6])}" if skipped else None)


def split_authors(raw):
    """`author` → (is_oca, [компанії]).

    В OCA це майже завжди «Odoo Community Association (OCA), Camptocamp, ...».
    Парасольку виносимо в окремий прапорець, решту — у вендорів.
    """
    if not isinstance(raw, (str, list, tuple)):
        return None, []
    parts = raw if isinstance(raw, (list, tuple)) else raw.split(",")
    is_oca, vendors = False, []
    for p in parts:
        name = str(p).strip()
        low = name.lower()
        # Парасолька, а не вендор. Перевірка навмисно вузька: просто підрядок
        # "oca" зловив би купу звичайних назв, тому або повна назва асоціації,
        # або рівно абревіатура, або її дужкова форма.
        if ("odoo community association" in low or low == "oca"
                or "(oca)" in low):
            is_oca = True
            continue
        if low.replace(".", "") in NOT_A_VENDOR:
            continue
        if name:
            vendors.append(name)
    return is_oca, vendors


def as_bool(v, default=None):
    return v if isinstance(v, bool) else default


def as_text(v, limit=None):
    if v is None or isinstance(v, (dict, list, tuple, bool)):
        return None
    s = str(v).strip()
    if not s:
        return None
    return s[:limit] if limit else s


def as_list(v):
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return None


def _compact(m, limit=100_000):
    """Манифест у jsonb без `description` (це цілий README, він нам не потрібен).

    НЕ обрізаємо рядок JSON по довжині: обрізаний JSON — невалідний JSON, і
    колонка jsonb просто відмовиться його прийняти. Якщо не влазить, викидаємо
    найдовші значення, поки не влізе.
    """
    d = {k: v for k, v in m.items()
         if k != "description"
         and isinstance(v, (str, int, float, bool, list, dict, set, tuple, type(None)))}
    s = _dumps(d)
    while len(s) > limit and d:
        big = max(d, key=lambda k: len(_dumps(d[k])))
        d.pop(big)
        s = _dumps(d)
    return s


def _fallback(o):
    """Фільтр по типу на верхньому рівні не рятує: множина чи кортеж можуть
    лежати ВКЛАДЕНО (`'external_dependencies': {'python': {...}}`), і json
    падає вже на серіалізації. Приводимо до списку, решту — до рядка."""
    if isinstance(o, (set, frozenset, tuple)):
        return sorted(str(x) for x in o)
    return str(o)


def _dumps(v):
    return json.dumps(v, ensure_ascii=False, default=_fallback)


def main():
    t0 = time.time()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, repo, module, series FROM modules WHERE series = ANY(%s)",
                (SERIES,))
    rows = cur.fetchall()
    print(f"модулів до розбору: {len(rows)} (серії {', '.join(SERIES)})", file=sys.stderr)

    stats = {"ok": 0, "partial": 0, "no_file": 0, "failed": 0, "not_installable": 0}
    for r in rows:
        path = ROOT / "var" / "repos" / r["series"] / r["repo"] / r["module"] / "__manifest__.py"
        if not path.exists():
            stats["no_file"] += 1
            continue
        m, err = parse_manifest(path)
        if not m:
            stats["failed"] += 1
            cur.execute("UPDATE modules SET manifest_error=%s, manifest_at=now() WHERE id=%s",
                        (err, r["id"]))
            continue
        stats["partial" if err else "ok"] += 1

        # Ключове рішення: відсутній 'installable' у Odoo означає True.
        # Тому None тут — це «манифест не розібрався», а не «не встановлюваний»;
        # плутати ці два стани не можна, бо друге виключає модуль із черги.
        installable = as_bool(m.get("installable"), True)
        if not installable:
            stats["not_installable"] += 1
        is_oca, vendors = split_authors(m.get("author"))
        ext = m.get("external_dependencies")

        cur.execute("""
            UPDATE modules SET
              installable=%s, category=%s, author_raw=%s, vendors=%s, is_oca=%s,
              license=%s, summary=%s, manifest_version=%s, depends=%s, ext_deps=%s,
              website=%s, maintainers=%s, auto_install=%s, application=%s,
              manifest=%s, manifest_error=%s, manifest_at=now()
            WHERE id=%s
        """, (
            installable,
            as_text(m.get("category"), 120),
            as_text(m.get("author"), 500),
            vendors or None,
            is_oca,
            as_text(m.get("license"), 60),
            as_text(m.get("summary"), 400),
            as_text(m.get("version"), 40),
            as_list(m.get("depends")),
            json.dumps(ext, ensure_ascii=False) if isinstance(ext, dict) else None,
            as_text(m.get("website"), 300),
            as_list(m.get("maintainers")),
            as_bool(m.get("auto_install"), False),
            as_bool(m.get("application"), False),
            _compact(m),
            err, r["id"],
        ))

    print(f"\nготово за {time.time()-t0:.0f}s", file=sys.stderr)
    for k, v in stats.items():
        print(f"  {k:16} {v}", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
