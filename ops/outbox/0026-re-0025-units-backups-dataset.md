---
id: 0026
re: 0025
from: server
date: 2026-08-21T12:25:00Z
status: done
---

# 0026 · Бекапи існують. Датасет у git. І дефект, який тихо різав зріз до однієї серії

Порядок твій: maint → датасет → watch20 → postreboot. По дорозі знайшлось те, що
пояснює твою знахідку B точніше, ніж «юнітів немає», і те, що дорожче за неї.

## 0. Поправка до B: файли були встановлені. Не був увімкнений таймер

`systemctl list-units` показує лише **завантажені** юніти, а oneshot, який ще ні
разу не запускався, systemd у памʼять не тягне. Тому `maint`, `watch20` і
`postreboot` у твоєму виводі відсутні, хоча файли лежали в `/etc/systemd/system`
із 19.08 і були байт-у-байт як у git:

```
=== diff repo vs /etc (до будь-яких моїх дій) ===
OK   modidx-export.service     OK   modidx-export.timer
OK   modidx-harvest.service    OK   modidx-harvest.timer
OK   modidx-maint.service      OK   modidx-maint.timer
OK   modidx-postreboot.service OK   modidx-watch20.timer
OK   modidx-runner@.service    OK   modidx-watch20.service
```

Правильна команда — `list-unit-files`, вона показує встановлене незалежно від
того, чи воно колись запускалось. Це записано в `systemd/README.md`.

Висновок B від цього не слабшає, а стає точнішим: **скопіювати файл — не значить
увімкнути.** `maint.timer` був `disabled`, тобто твоє C справедливе повністю.

## 1. maint: увімкнено і прогнано вручну

```
# systemctl enable --now modidx-maint.timer
Created symlink /etc/systemd/system/timers.target.wants/modidx-maint.timer → /etc/systemd/system/modidx-maint.timer.

# systemctl list-timers 'modidx-*' --all
NEXT                        LEFT   LAST                        PASSED    UNIT                 ACTIVATES
Fri 2026-08-21 13:00:00 UTC 47min  Fri 2026-08-21 12:00:03 UTC 12min ago modidx-export.timer  modidx-export.service
Sat 2026-08-22 03:21:57 UTC 15h    Fri 2026-08-21 03:30:03 UTC -         modidx-harvest.timer modidx-harvest.service
Sat 2026-08-22 04:40:00 UTC 16h    -                           -         modidx-maint.timer   modidx-maint.service

# systemctl list-unit-files 'modidx-*'
modidx-export.service     static   -
modidx-harvest.service    static   -
modidx-maint.service      static   -
modidx-postreboot.service disabled enabled
modidx-runner@.service    indirect enabled
modidx-watch20.service    static   -
modidx-export.timer       enabled  enabled
modidx-harvest.timer      enabled  enabled
modidx-maint.timer        enabled  enabled
modidx-watch20.timer      disabled enabled
```

`*.service` під таймером — `static`, і це не «не встановлено»: у них немає
`[Install]`, бо вмикається таймер. Раніше це саме так і читалося неправильно.

Ручний прогін — **через юніт, не через скрипт**, щоб перевірити ще й оточення:

```
# systemctl start modidx-maint.service        (8,6 с)
# systemctl status modidx-maint.service
     Loaded: loaded (/etc/systemd/system/modidx-maint.service; static)
     Active: inactive (dead) since Fri 2026-08-21 12:08:40 UTC
TriggeredBy: ● modidx-maint.timer
    Process: 9729 ExecStart=/bin/bash /srv/modidx/bin/maint.sh (code=exited, status=0/SUCCESS)
        CPU: 1.958s

# cat var/logs/maint.log
=== 2026-08-21T12:08:31+00:00 ===
series_snapshots: 7 рядків
датасет запушено: eea95ad
stats.html оновлено
диск 42% · у черзі 0 · зависло 0 · прогонів за добу 1921
готово
```

Справжній `pg_dump`, не «крок виконано»:

```
# ls -la var/backups/
-rw-r--r-- 1 root root 1889551 Aug 21 12:08 modidx-2026-08-21.sql.gz

# gzip -t var/backups/*.sql.gz && echo цілий
цілий
# zcat var/backups/*.sql.gz | wc -l
25948
# zcat var/backups/*.sql.gz | grep '^COPY public' | awk '{print $2}'
public.core_addons      public.eco_events        public.feed_cursor
public.image_packages   public.jobs              public.modules
public.runs             public.series_image      public.series_snapshots
public.state_changes    public.unbuildable_deps  public.watch_state
```

12 таблиць, `series_snapshots` серед них. Ротація — 7 останніх, як і було.

### Дефект, який знайшовся тільки на живому запуску

`git push` у нічному юніті **не працював би взагалі**, і не через ключ.
systemd не виставляє `$HOME` сервісам без `User=`, а пуш іде по SSH через аліас
`github-modidx` із `/root/.ssh/config`. Без `HOME` ssh не бачить ні config, ні
known_hosts — і крок падає тихо, при зеленому юніті й живому таймері. Тобто
offsite-копії знову не було б, а виглядало б усе так само добре, як досі.
Додано `Environment=HOME=/root` у `modidx-maint.service`.

Це та сама пастка, що й твоє B, тільки на рівень нижче: юніт існує, увімкнений,
відпрацював з кодом 0 — і не зробив головного.

## 2. Датасет у `data/` цього репозиторію

Зроблено як ти пропонував, окремий репозиторій не заводив.

```
# git log --oneline -2
eea95ad dataset 2026-08-21: series_snapshots + modules.csv
4c2124b inbox 0025: юніти maint/watch20/postreboot не встановлені; бекапів немає

# git show --stat eea95ad
 data/README.md            |   32 +
 data/modules.csv          | 4449 +++++++
 data/series_snapshots.csv |    8 +

# git log --oneline origin/main -1
eea95ad dataset 2026-08-21: series_snapshots + modules.csv
```

`series_snapshots` вигружається **цілком**, як ти й вимагав — усі колонки, усі
дати, `ORDER BY taken_at, series`. Не поточний зріз.

Три деталі, на яких старий код або тихо не робив нічого, або зробив би шкоду:

- Старий крок був `if [ -d var/dataset/.git ]`. Теки `var/dataset` **на диску
  ніколи не існувало**, тому навіть при увімкненому таймері крок був no-op без
  жодного повідомлення. Тепер шлях один і він у робочому дереві.
- Вигрузка йде у `var/series_snapshots.csv.new` і лише потім `mv`. Пряме
  `> data/series_snapshots.csv` при падінні psql обнулило б файл — бекап
  перетворився б на видалення.
- Коміт **тільки по pathspec `-- data`** і з попереднім `git add -- data`. У
  робочому дереві сервера цілком може лежати незакінчена правка сесії, і нічне
  обслуговування не має права затягнути її в публічний репозиторій. Перевірено:
  після прогону мої правки в `bin/` і `systemd/` лишились незакоміченими, у
  коміт пішли рівно три файли `data/`. Без `git add` новий файл не потрапив би
  в коміт узагалі — `commit -- path` бере лише те, що git уже відслідковує.

`data/README.md` розділяє щоденні файли й **еталон 18.08**: `oca_modules.csv` і
`oca_snapshot.csv` я не чіпав, бо з ними звіряються всі наступні заміри.

## 3. Головне: нічний зріз писав ЛИШЕ 16.0, і це коштувало даних

Твоє C про відсутність копії — правда, але поки я туди дивився, знайшлось гірше.
`series_snapshots` до моїх дій:

```
     d      | series | repos | modules
 2026-08-19 | 16.0   |   214 |    3099
 2026-08-19 | 17.0   |   217 |    1930
 2026-08-19 | 18.0   |   228 |    2877
 2026-08-19 | 19.0   |   230 |    1192
 2026-08-19 | 20.0   |     0 |       0
 2026-08-20 | 16.0   |   214 |    3099     ← і все за добу
 2026-08-21 | 16.0   |   214 |    3101     ← і все за добу
```

Рядки за 19.08 — від ручних запусків. Нічний таймер за 20 і 21.08 записав **одну
серію з пʼяти**. Причина не в скрипті й не в юніт-файлі, а в тому, як systemd
читає `Environment=`: він розбиває рядок по пробілах.

```
# journalctl -b | grep 'Invalid environment assignment'
/etc/systemd/system/modidx-harvest.service:9: Invalid environment assignment, ignoring: 17.0
/etc/systemd/system/modidx-harvest.service:9: Invalid environment assignment, ignoring: 18.0
/etc/systemd/system/modidx-harvest.service:9: Invalid environment assignment, ignoring: 19.0
/etc/systemd/system/modidx-harvest.service:9: Invalid environment assignment, ignoring: 20.0
/etc/systemd/system/modidx-runner@.service:10: Invalid environment assignment, ignoring: 18.0
/etc/systemd/system/modidx-runner@.service:10: Invalid environment assignment, ignoring: 19.0

# systemctl show modidx-harvest.service -p Environment      (до правки)
Environment=ROOT=/srv/modidx HARVEST_SERIES=16.0
```

У файлі написано `HARVEST_SERIES=16.0 17.0 18.0 19.0 20.0`. `cat` показує
правильний рядок, юніт зелений, у лозі harvest — чесний вивід про одну серію.
Потрібні лапки: `Environment="HARVEST_SERIES=..."`.

Після правки й `daemon-reload`:

```
# systemctl show modidx-harvest.service -p Environment
Environment=ROOT=/srv/modidx "HARVEST_SERIES=16.0 17.0 18.0 19.0 20.0"
# systemctl show modidx-runner@1.service -p Environment
Environment=ROOT=/srv/modidx "SERIES=17.0 18.0 19.0" BATCH=8 RUN_MEM=2g RUN_TIMEOUT=420
```

**Чому це важливіше за все інше в цьому файлі.** Твоя теза «щоденна історія
зрізів не відтворюється ніколи» тут спрацювала буквально: зріз за **20.08 по
серіях 17.0, 18.0, 19.0 і 20.0 втрачений назавжди**. Дописати його нічим — це
був стан гілок на той день. Тобто діра була не лише в копії активу, а в самому
активі, і без бекапу її б ніхто не побачив ще довго.

За 21.08 добу вдалося врятувати — вона ще не закінчилась, тому я прогнав harvest
з усіма серіями, і `ON CONFLICT (день, серія, метод)` дописав відсутні рядки:

```
 2026-08-20 | 16.0   |   214 |    3099     ← 17/18/19/20 за цю добу втрачено
 2026-08-21 | 16.0   |   214 |    3102
 2026-08-21 | 17.0   |   217 |    1931
 2026-08-21 | 18.0   |   229 |    2879
 2026-08-21 | 19.0   |   230 |    1209
 2026-08-21 | 20.0   |     0 |       0     ← вартовий і зріз погоджуються
```

Це справжній обхід 232 репозиторіїв (103 с), а не заповнення дірки копіюванням
сусіднього дня.

`modidx-runner@.service` мав ту саму помилку (`SERIES=17.0` замість трьох серій),
але наслідків не дала: `claim()` у `runner.py` бере серію з голови черги й змінну
взагалі не читає. Лапки все одно поставив — пастку лишати не варто.

Дві помічені суміжні речі, які **не чіпав**, бо це вже про 16.0 і твій пункт 5:
`bin/sync_repos.sh` за замовчуванням синхронізує `18.0 19.0`, а `enqueue.py` і
`manifests.py` беруть `SERIES` за замовчуванням `17.0 18.0 19.0`. Тобто зріз
тепер бачить 16.0, а в чергу 16.0 не потрапить, поки ми цього не скажемо явно.
Це радше добре: масовий прохід не має початися сам собою.

## 4. watch20: встановлено, перевірено, таймер вимкнений

```
# systemctl start modidx-watch20.service ; echo exit=$?
exit=0
# cat var/logs/watch20.log
вартовий 20.0: гілки платформи немає (0.6s)
# systemctl is-enabled modidx-watch20.timer modidx-watch20.service
disabled
static
```

Відповідь правильна. І не лише в лозі — вартовий дійшов до БД:

```
# psql -c 'SELECT * FROM watch_state;'
    key     |              at               | note
 check_20.0 | 2026-08-21 12:09:05.705894+00 |
```

Таймер лишається `disabled` до 20.09, як ти й просив; увімкнення —
`systemctl enable --now modidx-watch20.timer`, записано в `systemd/README.md`.

## 5. postreboot: він **відпрацював**. Просто доказ не виїхав із сервера

Твоє «не встановлений, тобто не відпрацював» — єдине місце, де висновок хибний, і
винні в цьому ми: юніт вимикає себе сам, а звіт лягає в `var/`, який у git не йде.
Тому назовні він виглядає точно як невстановлений.

```
# journalctl -u modidx-postreboot.service
Aug 21 11:18:38 allservices systemd[1]: Starting modidx-postreboot.service...
Aug 21 11:19:53 allservices systemd[1]: modidx-postreboot.service: Deactivated successfully.
Aug 21 11:19:53 allservices systemd[1]: Finished modidx-postreboot.service.
Aug 21 11:19:53 allservices systemd[1]: Consumed 13.283s CPU time, 1.1G memory peak
```

**Призначення.** Одноразовий чек-лист після перезавантаження: збирає докази, а не
висновок «усе піднялося». `docker ps`, шаблонні БД, `list-units` + `list-unit-files`,
swap, `curl` сайту й `status.json`, черга (в `running` не має бути нічого), диск,
образи, чекаути, залишкові `job_*`. `ExecStartPre=sleep 40` — два цикли
healthcheck Postgres із запасом. `ExecStartPost=systemctl disable` — самознезброєння:
потрібен рівно один раз, інакше збирав би файл після кожного ребуту й одного дня
свіжий переплутали б зі старим. Саме тому він зараз `disabled` — це успіх, не збій.

Витяг із `var/logs/postreboot-20260821T111918Z.txt` (11:19:18Z, uptime 0 хвилин):

```
docker: modidx-caddy Up 41 seconds · modidx-pg Up 41 seconds (healthy)
tmpl_170|t  tmpl_180|t  tmpl_190|t
runner@1 active running · runner@2 active running
export.timer enabled/waiting · harvest.timer enabled/waiting
maint.timer disabled · watch20.timer disabled          ← ось де твоє B видно чорним по білому
swap: /swapfile 4G, used 0B
https://allservices.one → HTTP/2 200
status.json: generated_at 2026-08-21T11:00:04Z commit 8608e93 queue {queued:0, running:0, error:0}
черга: (0 rows) · у running (0 rows)
/dev/sda1 75G 31G 41G 43% · Mem 7751 total 584 used
job_* залишкових: 0
```

**Ручного доганяння не потрібно** — він відпрацював у своє вікно. Юніт лишається
в репозиторії. Але з нього два висновки:

1. Перед **кожним** плановим ребутом його треба вмикати заново
   (`systemctl enable modidx-postreboot.service`) — записано в `systemd/README.md`.
2. Він сам виявив `maint.timer disabled` о 11:19, за годину до твого 0025. Ніхто
   не подивився, бо файл лежав у `var/`. Наступного разу витяг із нього кладу в
   `outbox` тим же кроком, що й решту звіту.

## Що зроблено файлами

| Файл | Зміна |
|---|---|
| `bin/maint.sh` | крок 5 переписаний: `series_snapshots` цілком + `modules.csv` у `data/`, `.new`+`mv`, `git add`+коміт по pathspec, rebase-фолбек при push |
| `systemd/modidx-maint.service` | `Environment=HOME=/root` |
| `systemd/modidx-harvest.service` | лапки в `HARVEST_SERIES` |
| `systemd/modidx-runner@.service` | лапки в `SERIES` |
| `systemd/README.md` | таблиця «хто чим є», правило лапок, `static ≠ не встановлено`, `list-unit-files` замість `list-units`, коли вмикати watch20 і postreboot |
| `data/README.md` | щоденні файли vs еталон 18.08 |

## Далі

Твій пункт 5 — 16.0, потім `master` до 24.09. Черга порожня, диск 42%, бекап є.
Перед масовим проходом 16.0 треба явно вирішити `SERIES` для `sync_repos.sh`,
`manifests.py` і `enqueue.py` — зараз 16.0 у них не входить.
