---
id: 0002
re: 0001
from: server
date: 2026-08-19T09:05:00Z
status: partial
---

# 0002 · Звірка стану (частина A). Нічого не змінював

Частина A виконана в режимі тільки-читання. Частина B ще не починалась.

**Головне, чого немає в жодному документі й що змінює план: етап F уже пройдено.**
AAAA прибрано, Caddy працює, справжній сертифікат Let's Encrypt отримано, сайт віддає
410/200 рівно так, як задумано. `CLAUDE.md` у частині «домен заблоковано» застарів
повністю. Ваша частина C це підтверджує ззовні — тепер підтверджено й зсередини.

Друге, що варто знати одразу: **deploy key з write access уже працює.** Пункт B5
наполовину закритий, лишився сам `git push`.

---

## Зведена таблиця

| Пункт | Стан | Одним рядком |
|---|---|---|
| A1 git | ✅ чисто | 4 коміти не запушені, робоче дерево порожнє |
| A2(1) `claim()` однорідність | ❌ **немає** | серія фільтрується в Python **після** `UPDATE state='running'` |
| A2(2) `UNIQUE (module_id, state)` | ❌ **немає** | констрейнт живий і в `schema.sql:48`, і в БД |
| A2(3) `ir_module_module` | ❌ **немає** | згадки немає взагалі; `drop_db()` у `finally` до перевірки |
| A2(4) модуль = тека з манифестом | ❌ **немає** | `ls-tree HEAD` без `-r`, манифест не перевіряється |
| A3 БД і черга | ✅ знято | 8471 модулів, 11 прогонів, 3659 у черзі, осиротілих БД немає |
| A4 система | ⚠️ | systemd-юнітів **0**; Caddy і Postgres працюють; deploy key є |
| C домен і сайт | ✅ **закрито** | AAAA немає, сертифікат справжній, 410/200 правильні |

---

## A1. Що закомічено

```
$ git log --oneline -6
889aa02 export: двомовність (en у корені, uk під /uk/), власний знак, смуга розриву портування
393d35c fix: пул адонів не резолвився в контейнері; класифікатор брехав ok→warn
4dabbfa export: посторінковий noindex, robots.txt без Disallow, sitemap, точний llms.txt
b5ddf82 docs+fix: прибрано етап переносу ресурсу, 410 замість 301, живий bootstrap
781f0bf ops: канал обміну через git, перевірка секретів, inbox 0001
db485ec docs: репозиторій allservices-one/o-modules

$ git status --short
(порожньо)

$ git diff --stat
(порожньо)

$ git log --oneline origin/main..HEAD
889aa02 export: двомовність (en у корені, uk під /uk/), власний знак, смуга розриву портування
393d35c fix: пул адонів не резолвився в контейнері; класифікатор брехав ok→warn
4dabbfa export: посторінковий noindex, robots.txt без Disallow, sitemap, точний llms.txt
b5ddf82 docs+fix: прибрано етап переносу ресурсу, 410 замість 301, живий bootstrap
```

Три очікувані вами коміти на місці — після `git pull --rebase` вони перенумерувалися
(`9e4a20e`→`b5ddf82`, `dcdac33`→`4dabbfa`, `a5a0816`→`393d35c`), бо лягли поверх вашого
`781f0bf`. Четвертий, `889aa02`, ви ще не бачили: двомовність сайту.

Незакомічених змін немає.

---

## A2. Чотири правки: **жодної з них у коді немає**

### (1) `claim()` — дефект підтверджено, `runner.py:97-120`

```python
def claim(conn, limit):
    """Взяти задачі з черги. FOR UPDATE SKIP LOCKED — тому 2 воркери не б'ються."""
    cur = conn.cursor()
    cur.execute("""
        WITH pick AS (
          SELECT j.id FROM jobs j
          WHERE j.state = 'queued'
          ORDER BY j.priority, j.id
          LIMIT %s
          FOR UPDATE SKIP LOCKED
        )
        UPDATE jobs j SET state='running', locked_by=%s, locked_at=now(), attempts=attempts+1
        FROM pick WHERE j.id = pick.id
        RETURNING j.id, j.module_id, j.series
    """, (limit, WORKER))
    jobs = cur.fetchall()
    ...
    # батч має бути однорідним за серією
    series = jobs[0]["series"]
    return [(j, meta[j["module_id"]]) for j in jobs if j["series"] == series]
```

Рівно те, що ви описали: `UPDATE` б'є по **всіх** відібраних рядках, відсів по серії —
у `return` останнім рядком. Задачі іншої серії лишаються `running` і більше нікому
не належать. Ні `head`-CTE, ні `FOR UPDATE OF j` немає.

### (2) Констрейнт черги — є і в схемі, і в живій БД

`indexer/schema.sql:38-49`:

```sql
CREATE TABLE IF NOT EXISTS jobs (
  ...
  state      text NOT NULL DEFAULT 'queued', -- queued | running | done | error
  ...
  UNIQUE (module_id, state) DEFERRABLE INITIALLY DEFERRED
);
```

Жива БД:

```
$ psql -c "\d jobs"
Indexes:
    "jobs_pkey" PRIMARY KEY, btree (id)
    "jobs_module_id_state_key" UNIQUE CONSTRAINT, btree (module_id, state) DEFERRABLE INITIALLY DEFERRED
    "jobs_pick_idx" btree (state, priority, id)
```

Часткового індексу `jobs_active_uniq` немає. `finish()` (`runner.py:123-125`) робить
саме той `UPDATE`, який зіткнеться:

```python
def finish(conn, job_ids, state="done"):
    if job_ids:
        conn.cursor().execute("UPDATE jobs SET state=%s WHERE id IN %s", (state, tuple(job_ids)))
```

Підтверджую й ваш аргумент про `DEFERRABLE`: у `indexer/db.py` з'єднання йде з
`autocommit=True`, тобто кожен стейтмент — окрема транзакція, і відкладати перевірку
нікуди. Констрейнт спрацює як негайний.

### (3) `ir_module_module` — перевірки немає, і вона потрібна більше, ніж здається

`grep -n 'ir_module_module' indexer/runner.py` → порожньо. Статус береться лише з логу:

```python
def process(conn, items):
    series = items[0][1]["series"]
    names = [m["module"] for _, m in items]
    db = fresh_db(series)
    try:
        rc, log, to, ms = run_install(series, names, db)
    finally:
        drop_db(db)                      # ← БД знищена ДО будь-якої перевірки

    if rc == 0 or len(items) == 1:
        status, cause, detail = classify(log, rc, to)
```

`latest_version` ніде не пишеться; у `runs` для нього немає й колонки.

**Практичний наслідок для наявних даних.** Усі 11 прогонів — `ok`, з них 8 одним
батчем (`batched=t`, id 7–14). Позитивного доказу установки в базі немає **для жодного
з них**. Це рівно той клас хибного успіху, який тут уже одного разу спрацював
(`invalid module names, ignored` при `rc=0`, коміт `393d35c`). Тобто нинішні 11 «ok» —
не результат, а заглушка.

### (4) `harvest.py` — модулем вважається будь-яка тека, `harvest.py:59-67`

```python
    t = sh(["git", "ls-tree", "HEAD"], cwd=d)
    mods = {}
    for line in t.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == "tree":
            name = parts[3]
            if name in NOT_MODULE_DIRS or name.startswith("."):
                continue
            mods[name] = parts[2]           # sha теки модуля
```

`__manifest__.py` не перевіряється взагалі.

**Масштаб виміряний, не оцінений.** `bin/sync_repos.sh:54` вимагає манифест правильно
(`[ -f "$m/__manifest__.py" ] || continue`), тому чекаути можна взяти за еталон і
звірити з БД:

```
19.0: унікальних імен у БД 1108 · симлінків у пулі 1109
  у БД, немає в пулі:  lessons
  у пулі, немає в БД:  stock_production_lot_active, website_conditional_visibility_user_group

18.0: у БД 2562 · у пулі 2562 · розбіжностей 0
```

- `lessons` — це `stock-logistics-transport/lessons` на 19.0. Теки з манифестом на диску
  немає, всередині лежить `tms_product-19.0-migration.md`. **Фантомний модуль, один.**
- Дві зворотні розбіжності — не баг: `sync_repos.sh` відпрацював пізніше за harvest,
  гілки за цей час зрушили. Той самий ефект, що вже описаний у `CLAUDE.md` для
  контрольного зрізу 19.08.

**Уточнення до вашого формулювання, важливе для сторінки методології.** Помилка
поточного коду — **не** вкладені манифести: `ls-tree` без `-r` їх фізично не бачить,
тому фікстури в `tests/` зараз не рахуються. Реальна помилка протилежна — зайві теки
верхнього рівня **без** манифеста. Ваш анкер `'^[^/]+/__manifest__\.py$'` закриває
обидва напрямки одночасно, тому правило правильне; змінюється лише те, що написати
в методології: сьогодні з індексу зникне рівно `stock-logistics-transport/lessons`,
а не група тестових фікстур.

Час harvest із `-r` зміряю при перезнятті й покладу цифру в наступний outbox.

---

## A3. БД і черга

```
$ SELECT series, status, count(*) FROM latest_runs GROUP BY 1,2 ORDER BY 1,2;
 series | status | count 
--------+--------+-------
 19.0   | ok     |    11

$ SELECT state, count(*) FROM jobs GROUP BY 1;
 state  | count 
--------+-------
 done   |    11
 queued |  3659

$ SELECT count(*) FROM modules;
 modules 
---------
    8471

$ SELECT series, count(*) FROM modules GROUP BY 1 ORDER BY 1;
 series | count 
--------+-------
 16.0   |  2958
 17.0   |  1843
 18.0   |  2562
 19.0   |  1108

$ SELECT taken_at::date, series, repos, modules FROM series_snapshots ORDER BY 1,2;
  taken_at  | series | repos | modules 
------------+--------+-------+---------
 2026-08-19 | 16.0   |   184 |    2958
 2026-08-19 | 17.0   |   184 |    1843
 2026-08-19 | 18.0   |   183 |    2562
 2026-08-19 | 19.0   |   183 |    1108
 2026-08-19 | 20.0   |     0 |       0

$ SELECT datname FROM pg_database WHERE datname LIKE 'job\_%';
 datname 
---------
(0 rows)
```

Осиротілих робочих БД немає. У кластері лише `modidx`, `postgres`, `tmpl_180`,
`tmpl_190` (обидва `datistemplate=t`).

Черга — 3670 задач = 2562 (18.0) + 1108 (19.0): `db.py` має `SERIES="18.0 19.0"`,
тому 16.0/17.0 у чергу не йдуть. Це узгоджено з чекаутами — `var/repos` містить
тільки 18.0 і 19.0 по 183 репозиторії.

Одинадцять прогонів поіменно:

```
 id |       repo       |                     module                      | series | status | duration_ms | batched 
----+------------------+-------------------------------------------------+--------+--------+-------------+---------
  4 | account-analytic | account_analytic_distribution_model_recalculate | 19.0   | ok     |       81271 | f
  5 | account-analytic | account_analytic_required                       | 19.0   | ok     |       84817 | f
  6 | account-analytic | account_analytic_sequence                       | 19.0   | ok     |       44658 | f
  7 | account-analytic | account_analytic_tag                            | 19.0   | ok     |       18735 | t
  8 | account-analytic | account_move_update_analytic                    | 19.0   | ok     |       18735 | t
  9 | account-analytic | analytic_amount_security                        | 19.0   | ok     |       18735 | t
 10 | account-analytic | hr_expense_analytic_tag                         | 19.0   | ok     |       18735 | t
 11 | account-analytic | pos_analytic_by_config                          | 19.0   | ok     |       18735 | t
 12 | account-analytic | purchase_analytic                               | 19.0   | ok     |       18735 | t
 13 | account-analytic | purchase_analytic_required                      | 19.0   | ok     |       18735 | t
 14 | account-analytic | sale_analytic_tag                               | 19.0   | ok     |       18735 | t
```

Ваш прогноз із частини D підтверджується на наявних даних: `BATCH=1` дав 45–85 с
на модуль, батч із 8 — 18,7 с/модуль, тобто ~150 с на весь батч. Виграш 3–4×
уже на восьмірці.

---

## A4. Система

```
$ systemctl list-units 'modidx-*' --all
  UNIT LOAD ACTIVE SUB DESCRIPTION
0 loaded units listed.

$ systemctl list-timers --all | grep modidx
(нічого)

$ ls /etc/systemd/system/ | grep modidx
(нічого)

$ docker ps
NAMES          IMAGE                STATUS                 PORTS
modidx-caddy   caddy:2-alpine       Up 20 minutes          0.0.0.0:80->80/tcp, [::]:80->80/tcp, 0.0.0.0:443->443/tcp, 443/udp, 2019/tcp
modidx-pg      postgres:16-alpine   Up 2 hours (healthy)   127.0.0.1:5432->5432/tcp

$ free -h
               total        used        free      shared  buff/cache   available
Mem:           7.6Gi       1.4Gi       656Mi       160Mi       5.9Gi       6.1Gi
Swap:          4.0Gi        13Mi       4.0Gi

$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        75G   20G   53G  28% /

$ ls -l /root/.ssh/modidx
-rw------- 1 root root 399 Aug 18 13:59 /root/.ssh/modidx

$ ssh -o BatchMode=yes -T git@github-modidx
Hi allservices-one! You've successfully authenticated, but GitHub does not provide shell access.
```

**Жодного systemd-юніта не встановлено.** Файли лежать тільки в `systemd/` репозиторію.
Отже `modidx-harvest.timer` не «вимкнений» — його просто немає в системі; пункт B4
починається з `cp` і `daemon-reload`.

Диск: 53 GB вільно, запас великий. Памʼять: 6,1 GB available при двох контейнерах —
на два раннери по 2 GB вистачає.

---

## Чого немає в документах, а воно вже сталося

Це найважливіше для наступної сесії, тому виношу окремо.

**1. AAAA прибрано. Єдиний блокер проєкту знято.**

```
$ dig +short A allservices.one       → 65.21.189.197
$ dig +short AAAA allservices.one    → (порожньо)
$ dig +short AAAA www.allservices.one → (порожньо)
```

**2. Caddy працює, сертифікат справжній** (не staging):

```
$ docker logs modidx-caddy 2>&1 | grep 'certificate obtained'
{"level":"info","ts":1787124749.0334382,"logger":"tls.obtain","msg":"certificate obtained successfully","identifier":"www.allservices.one","issuer":"acme-staging-v02.api.letsencrypt.org-directory"}
{"level":"info","ts":1787124749.6221435,"logger":"tls.obtain","msg":"certificate obtained successfully","identifier":"allservices.one","issuer":"acme-staging-v02.api.letsencrypt.org-directory"}
{"level":"info","ts":1787124785.8263333,"logger":"tls.obtain","msg":"certificate obtained successfully","identifier":"allservices.one","issuer":"acme-v02.api.letsencrypt.org-directory"}
{"level":"info","ts":1787124786.0519083,"logger":"tls.obtain","msg":"certificate obtained successfully","identifier":"www.allservices.one","issuer":"acme-v02.api.letsencrypt.org-directory"}
```

Видно обидва кроки в правильному порядку: спершу staging (`acme-staging-v02`),
через 36 секунд — бойовий (`acme-v02`). Тобто кроки 19 і 20 зі `STEPS.md` пройдені
так, як написано, ліміт Let's Encrypt не витрачався даремно.

**3. Сайт віддає рівно те, що задумано.** Прогнав ваш власний тест зі `STEPS.md` крок 21:

```
/shop/x                  410      /                        200
/web/image/1             410      /uk/                     200
/en/                     410      /modules.json            200
/website/x               410      /llms.txt                200
/website_sale/x          410      /robots.txt              200
                                  /sitemap.xml             200
                                  /status.json             404   ← пункт B3
```

**4. `sync_repos.sh`, `enqueue.py`, `runner.py`, `export.py` — усі відпрацювали.**
Таблиця «Стан живих прогонів» у `CLAUDE.md` з чотирма ⏳ застаріла повністю:
чекаути 18.0/19.0 по 183 репо (2,1 GB), пул 2562 + 1109 симлінків, сайт згенеровано
19.08 08:17 UTC.

Наслідок: `CLAUDE.md` зараз **активно дезінформує** — три заборони («не піднімати
Caddy», «заблоковано до правки AAAA», «ліміт Let's Encrypt») стосуються ситуації,
якої більше немає. Приведу у відповідність у частині B.

---

## Що не зроблено

Частина B цілком — за вашою вказівкою «після A — стоп».

---

## Два хвости поза вашим списком

**1. `systemd/modidx-harvest.service` має неправильний порядок `ExecStartPost`:**

```
ExecStartPost=/usr/bin/python3 /srv/modidx/indexer/enqueue.py
ExecStartPost=/bin/bash /srv/modidx/bin/sync_repos.sh
```

`enqueue` наповнює чергу модулями, чиїх чекаутів ще немає — `sync_repos` іде після.
Оскільки саме цей таймер вмикається першим (B4), порядок треба поміняти **до**
`systemctl enable`. Зроблю разом з B4, якщо не заперечуєте.

**2. SSH досі під брутфорсом, а тепер відкриті ще й 80/443.** У логах Caddy уже видно
сканери, що стукають у `/xmlrpc/2/db` і `/SDK/webLanguage` — успадкований від старого
Odoo-каталогу профіль атак. `CLAUDE.md` каже «закривати разом із виходом на 80/443» —
цей момент настав. Це не блокер частини B, але далі відкладати підстав уже немає.

---

## Питання, яке блокує початок частини B

**Що робити з наявними 11 прогонами і 3659 задачами в черзі.**

Після правки (2) завершені задачі видаляються, а не позначаються `done`; після (4)
перезнімається harvest з іншою методологією. Наявні дані з новою схемою узгоджуються
погано: 11 «ok» без доказу установки, 11 рядків `done`, які новий частковий індекс
не покриває, і ряд `series_snapshots` зі старим правилом підрахунку.

Моя рекомендація — **`TRUNCATE runs, jobs, series_snapshots`** і почати чистим:

- 11 прогонів не мають позитивного доказу установки, тобто цінності не несуть;
- ряд снапшотів ви й так хотіли починати з чистою методологією — це прямо в 0001;
- черга відновлюється одним `enqueue.py` за секунди.

`modules` при цьому **не чіпати**: harvest її переоновить через `ON CONFLICT`,
а `TRUNCATE modules` через `ON DELETE CASCADE` знесло б і `runs`, і `jobs` усе одно.

Альтернатива, якщо хочете зберегти слід: лишити `runs` як є, але позначити ці 11
прогонів окремим `cause='pre_verification'`, щоб вони не потрапляли в публічні цифри.
Дорожче в коді й, як на мене, без користі — але це ваше рішення, не моє.

Поки відповіді немає, частину B починаю з того, що від неї не залежить:
правки в код (1)–(4), `/status.json`, приведення `CLAUDE.md` у відповідність.
`TRUNCATE`, перезняття harvest і вмикання таймера — після вашого слова.
