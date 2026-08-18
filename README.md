# Module Health Index — стартовий комплект

**Спершу прочитайте `CLAUDE.md`** — це контекст для сесії Claude Code на сервері.
**Як поставити Claude Code на сервер:** `SETUP-CLAUDE-CODE.md`.
**Порядок дій по пунктах:** `STEPS.md`.

Індекс фактичної сумісності модулів Odoo. Один VPS, усі служби локальні, нуль платних сервісів.

**Розрахований на:** 4 vCPU / 8 GB RAM / 80 GB диск.
План і обґрунтування — у `PLAN.md`. Уже зібрані дані — у `data/`.

## Швидкий старт

### Крок 1. Preflight — ПЕРЕД усім іншим
```bash
# розпакувати архів у /srv/modidx, далі:
cd /srv/modidx && chmod +x bin/*.sh
bash bin/preflight.sh > pf.txt && cat pf.txt
```
Показує: чи зайняті 80/443 і ким, cgroup v2, вільне місце, зовнішній IP, DNS,
чи працюють `docker pull` і `git`. **Розгортати наосліп не треба** — спершу подивитися вивід.

### Крок 2. DNS
Апекс `allservices.one` → IPv4 з preflight (рядок «Зовнішній IPv4»); наявний ресурс — на субдомен.
Окремо перевірити **AAAA**: зараз він веде на інший хост, і через це Let's Encrypt не видасть сертификат.
Без цього Let's Encrypt не видасть сертификат.

### Крок 3. Розгортання
```bash
ROOT=/srv/modidx bash bin/bootstrap.sh      # docker, swap, теки, postgres, схема, шаблонні БД

bash bin/sync_repos.sh                      # чекаути OCA 18.0/19.0 + пул адонів (~6 GB, 10-20 хв)
python3 indexer/harvest.py                  # зріз гілок і модулів (~2 хв)
python3 indexer/enqueue.py                  # наповнити чергу
BATCH=8 python3 indexer/runner.py           # перший воркер, перевірити що прогони йдуть

# коли переконались — під systemd
cp systemd/*.service systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now modidx-runner@1 modidx-runner@2
systemctl enable --now modidx-harvest.timer modidx-export.timer modidx-maint.timer

python3 indexer/export.py                   # згенерувати сайт у var/site
# перевірити домени й AAAA у Caddyfile, потім:
docker compose up -d caddy
```

## Що де

```
bootstrap.sh        одноразове розгортання (ідемпотентне)
mktemplate.sh       шаблонна БД з установленим base на серію Odoo
sync_repos.sh       чекаути OCA + плоский пул адонів симлінками
maint.sh            нічне: прибирання БД, бекап, goaccess, healthcheck
notify.py           пошта через особисту скриньку (SMTP-relay, без свого MTA)

indexer/schema.sql  таблиці + черга задач у Postgres (без Redis)
indexer/harvest.py  зріз OCA лише через git, без GitHub API
indexer/enqueue.py  ставить у чергу нові й змінені модули
indexer/runner.py   воркер: БД з шаблону → docker run odoo -i → класифікація
indexer/classify.py класифікатор причин падіння — найважливіший файл
indexer/export.py   генератор статики + датасету, без Node і Hugo

docker-compose.yml  Postgres (tuned) + Caddy
Caddyfile           статика, HTTPS автоматом, JSON-логи для goaccess
systemd/            юніти й таймери
data/               уже зібрані реальні дані станом на 18.08.2026
```

## Ключові рішення й чому саме так

| Рішення | Причина |
|---|---|
| Черга в Postgres (`FOR UPDATE SKIP LOCKED`) | нуль додаткових служб; Redis тут не потрібен |
| Шаблонні БД (`CREATE DATABASE … TEMPLATE`) | 1–2 с замість 30–60 с на установку `base` |
| `fsync=off` у Postgres | БД прогонів одноразові, дані не шкода — швидкість важливіша |
| Батчі по 8 із бісекцією | виграш 5–8× на першому масовому проході |
| Рівно 2 воркери | 8 GB: 2×2 GB контейнери + 1.5 GB Postgres + система |
| Плоский пул адонів симлінками | один `--addons-path`, залежності між репо резолвяться самі |
| Статика на Python, без Hugo/Node | менше залежностей і нуль ресурсу на слабкій машині |
| Пошук у браузері по одному JSON | нуль серверних ресурсів на пошук |
| GoAccess по логах раз на годину | нуль постійної памʼяті замість Umami/Plausible |
| SMTP особистої скриньки | власний MTA на VPS = спам-папка і зіпсована репутація домену |
| Датасет у git як бекап | offsite безкоштовно, і водночас це те, що цитують LLM |

## Обмеження, які треба тримати в голові

- **Не запускати третій воркер** — OOM. Спершу підняти RAM.
- **Диск:** `var/repos` росте з кожною серією (~3 GB). У вересні додасться 20.0 — прибрати 16.0/17.0.
- **Класифікація помилок важливіша за самі прогони.** Якщо не відділяти «немає python-пакета»
  від «несумісний з версією», цифрам не повірять і проєкт нічого не вартий.
- **Платні модулі** не встановлюються без ліцензії — тільки метадані, і це позначено явно.
- **Публікуємо факт прогону з логом і датою**, а не оцінку якості вендора.
