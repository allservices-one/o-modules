---
id: 0001
re: —
from: review
date: 2026-08-19T12:00:00Z
status: question
---

# 0001 · Звірка стану + чотири правки + канал стану

Сесія на сервері перезапускалася, її контекст втрачено. Стан сервера з памʼяті
не відновлюємо — звіряємося з фактами. Спершу **тільки читання**, зміни потім.

## Частина A. Звірка. Нічого не змінювати

Дай таблицю «є / немає / частково» по кожному пункту, з доказами — вивід команд,
не переказ.

### A1. Що закомічено
```bash
git log --oneline -10
git status --short
git diff --stat
```
Очікую побачити три відомі коміти (`9e4a20e`, `dcdac33`, `a5a0816`) і, можливо,
незакомічені зміни.

### A2. Чи є в коді чотири правки — перевіряй читанням файлів

**(1) `claim()` у `runner.py`.** Чи бере задачі лише однорідної серії.
Дефект: спершу всі взяті задачі переводяться в `running`, потім повертаються лише
ті, що співпали за серією з першою — решта лишається `running` **назавжди**.
На `BATCH=1` не проявляється, на межі серій починає тихо губити задачі.

Правильна форма (зверни увагу на `FOR UPDATE OF j` — у запиті з join до `head`
звичайний `FOR UPDATE` спробує залокати і `head`):
```sql
WITH head AS (
  SELECT series FROM jobs WHERE state='queued' ORDER BY priority, id LIMIT 1
), pick AS (
  SELECT j.id FROM jobs j, head
  WHERE j.state='queued' AND j.series = head.series
  ORDER BY j.priority, j.id LIMIT %s
  FOR UPDATE OF j SKIP LOCKED
)
UPDATE jobs j SET state='running', locked_by=%s, locked_at=now(), attempts=attempts+1
FROM pick WHERE j.id = pick.id RETURNING j.id, j.module_id, j.series
```
Гонка тут безпечна: якщо два воркери обчислять ту саму головну серію — це саме те,
що потрібно, а `SKIP LOCKED` розведе їх по різних рядках.

**(2) Констрейнт черги — баг у схемі, спрацює на другому проході harvest.**
У `schema.sql` є `UNIQUE (module_id, state)`. Перший прохід чистий. Але коли harvest
побачить новий `head_sha` і `enqueue` вставить другу задачу для того самого модуля,
її фінальний `UPDATE state='done'` зіткнеться з рядком `done` від першого проходу.
`DEFERRABLE INITIALLY DEFERRED` не врятує: `autocommit=True`, перевірка на кожному
стейтменті. Далі виняток піде в `except`, той поставить `error`, і черга почне
забиватися.

Перевір у живій БД: `\d jobs`. Якщо констрейнт є:
```sql
ALTER TABLE jobs DROP CONSTRAINT jobs_module_id_state_key;
CREATE UNIQUE INDEX jobs_active_uniq ON jobs (module_id)
  WHERE state IN ('queued','running');
```
І в `finish()` для успішного завершення — `DELETE FROM jobs WHERE id IN %s` замість
`UPDATE ... state='done'`. Історія прогонів живе в `runs`, черзі вона не потрібна.
Стан `error` лишити як є, щоб було видно, що падало. `schema.sql` привести у
відповідність, щоб чиста установка давала те саме.

**(3) Факт установки через `ir_module_module`.** Ми вже бачили, що `rc=0` може
означати «нічого не робив». Код виходу — це висновок, `state='installed'` — факт.

Перевіряти **кожен** модуль батчу окремо, до видалення робочої БД:
```sql
SELECT name, state, latest_version FROM ir_module_module WHERE name IN (…);
```
- `rc=0` і всі `installed` → `ok`
- `rc=0`, але модуль не `installed` → `env`, cause `not_installed_despite_rc0`
- `latest_version` записувати — це безкоштовні реальні дані для сторінки модуля

Це закриває **весь клас** «тихого успіху», а не лише знайдений випадок:
`installable: False`, частковий addons-path, помилка в імені — усі дають `rc=0`.
Бонус: при `rc=0` і двох невстановлених модулях у батчі ти знаєш, які саме,
**без бісекції**.

**(4) Підрахунок модулів у `harvest.py`.** Модулем вважати теку з `__manifest__.py`.
Анкер обовʼязковий, інакше порахуються вкладені манифести з тестових фікстур
(у частині репозиторіїв OCA вони є в `tests/`):
```bash
git ls-tree -r --name-only HEAD | grep -E '^[^/]+/__manifest__\.py$'
```
Зміряй, як зросте час harvest: `-r` на treeless-клоні тягне всі дерева, не лише
кореневе. Навіть якщо 96 с подвоїться — не страшно, harvest раз на добу.

**Терміновість саме цієї правки:** ми плануємо публікувати **темп** портування,
а `series_snapshots` наповнюється щодня. Зміна методології посеред ряду зіпсує нахил
графіка. Тому фіксувати до накопичення точок і одразу перезняти зріз.
Згадати зміну на сторінці методології: `stock-logistics-transport/lessons` зникне
з індексу, і хтось це помітить.

### A3. Що в БД і в черзі
```sql
SELECT series, status, count(*) FROM latest_runs GROUP BY 1,2 ORDER BY 1,2;
SELECT state, count(*) FROM jobs GROUP BY 1;
SELECT count(*) FROM modules;
SELECT taken_at::date, series, repos, modules FROM series_snapshots ORDER BY 1,2;
SELECT datname FROM pg_database WHERE datname LIKE 'job\_%';
```

### A4. Що в системі
```bash
systemctl list-units 'modidx-*'; systemctl list-timers | grep modidx
docker ps; free -h; df -h /
ls -l /root/.ssh/modidx 2>/dev/null || echo "deploy key немає"
```

**Після A — стоп.** Напиши `ops/outbox/0002-re-0001-reconciliation.md` з таблицею
і доказами, закомить і запуш. Далі частина B.

## Частина B. Після звірки

1. Доробити те з A2, чого немає.
2. Перезняти `harvest.py` — ряд снапшотів має початися з чистою методологією.
3. **`/status.json`** в `export.py` — канал, яким review бачить сервер без SSH:
```json
{
  "generated_at": "…Z",
  "commit": "a5a0816",
  "harvest": {"last_run": "…Z", "modules_by_series": {"19.0": 1108}},
  "runs": {"by_status": {"ok": 11}, "tested": 11, "total_modules": 4036},
  "queue": {"queued": 3659, "running": 0, "error": 0},
  "images": {"18.0": "…", "19.0": "…"},
  "disk_free_gb": 55, "mem_available_mb": 6400
}
```
Без секретів, без внутрішніх шляхів. Каддi вже віддає `var/site` — файл стане
доступним автоматично. Додати `Cache-Control: no-store` для нього, щоб я бачив свіже.
4. **Увімкнути `modidx-harvest.timer` першим**, окремо від решти: кожна пропущена
доба — втрачена точка на графіку, який публікуємо на початку вересня. Назад не добудувати.
5. Deploy key з **write access** і пуш усіх комітів.
6. Розділ «Поточний стан» — у `ops/STATE.md`, оновлювати після кожного кроку.

## Частина C. Що я вже перевірив ззовні, щоб ти не витрачав час

- Сертификат Let's Encrypt на апексі й `www`, ланцюжок повний: зовнішній fetch, який
  раніше падав на паркувальному сервері з `CERTIFICATE_VERIFY_FAILED`, тепер валідується.
- Головна: плитки 1843 / 2562 / 1108, «Перенесено 36.9%», `noindex` немає.
- `/m/web/web_responsive/`: `noindex` є, три серії «Не тестовано».
- `robots.txt` без `Disallow`. `llms.txt` формулювання чесне.
- DNS: `A` апекса й `www` → 65.21.189.197, `AAAA` немає ніде, `monitor`/`gitlab` знято,
  `MX` і `TXT` цілі, `CAA` немає.

## Частина D. Прогноз, щоб не було сюрпризу

70 с на модуль при `BATCH=1` — це переважно фіксоване сканування 1109 записів пулу.
Для 18.0 пул у 2,3 раза більший, тому при `BATCH=8` очікуй не 18,7, а 25–30 с/модуль.
Памʼять на контейнер фіксована 2 ГБ незалежно від розміру батчу, тому має сенс один раз
зміряти **`BATCH=16` на 18.0** — виграш саме в амортизації цього сканування.
Обмежувач з іншого боку — ціна бісекції; оптимум десь між 8 і 16.
