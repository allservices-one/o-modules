# Встановлення юнітів

```bash
cp /srv/modidx/systemd/*.service /srv/modidx/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now modidx-runner@1 modidx-runner@2
systemctl enable --now modidx-harvest.timer modidx-export.timer modidx-maint.timer
```

**`cp` без `daemon-reload` не робить нічого.** systemd тримає юніт у памʼяті; правка
файла на диску сама собою не застосовується, юніт лишається зеленим і працює за
конфігом, якого на диску вже немає. Той самий клас, що й bind-монтування `Caddyfile`
по іноду (див. `CLAUDE.md`).

**Лапки в `Environment=` для значень із пробілами обовʼязкові.** systemd розбиває
рядок по пробілах, тому `Environment=HARVEST_SERIES=16.0 17.0 18.0 19.0 20.0` дає
`HARVEST_SERIES=16.0` плюс чотири «Invalid environment assignment, ignoring». Файл
при цьому виглядає правильним, юніт зелений, а нічний зріз пише лише одну серію —
спіймано 21.08.2026, коштувало серій 17.0–20.0 у зрізі за 20.08 назавжди. Перевірка —
не `cat` файла, а:
```bash
systemctl show modidx-harvest.service -p Environment
journalctl -b | grep 'Invalid environment assignment'
```

**Рівно два воркери.** Третій на 8 GB RAM викличе OOM: кожен контейнер Odoo обмежений 2 GB,
плюс 1.5 GB Postgres і решта системи.

## Хто чим є

| Юніт | Enable | Що робить |
|---|---|---|
| `modidx-runner@1`, `@2` | `enabled` | воркери прогонів, постійно |
| `modidx-harvest.timer` | `enabled` | зріз OCA щодня 03:20 + sync_repos, manifests, enqueue |
| `modidx-export.timer` | `enabled` | генерація сайту щогодини |
| `modidx-maint.timer` | `enabled` | 04:40: прибирання, `pg_dump` у `var/backups/`, датасет у `data/` з пушем |
| `modidx-watch20.timer` | `enabled` з 21.08.2026 | вартовий гілки 20.0 кожні 15 хв |
| `modidx-postreboot.service` | одноразовий | чек-лист після ребуту, вимикає себе сам |

`*.service` під таймером — `static`, це нормально: у них немає `[Install]`, бо
вмикається таймер, а не сервіс. `systemctl is-enabled modidx-maint.service` → `static`
не означає «не встановлено».

`modidx-watch20.timer` **увімкнений постійно**, а не з 20.09. Плановий термін був
помилкою проєктування: він вимагав, щоб людина згадала про таймер у конкретний день,
і якщо 20 вересня власник зайнятий — вартовий не запуститься, а дізнаємось ми 25-го.
Той самий клас відмови, який ми ловимо весь тиждень, тільки замість тихого коду тут
тиха памʼять.

Постійне опитування дешеве саме тому, що дорога частина умовна: до появи
`refs/heads/20.0` у `odoo/odoo` вартовий робить **один** `git ls-remote` і виходить.
96 запитів на добу до одного репозиторію — кілобайти. Повний обхід 232 репозиторіїв
OCA лишається за умовою «гілка платформи вже є» і не частіше разу на годину
(`WATCH_SWEEP_MIN=60`) — саме ця умова й тримає ціну на нулі.

Поки гілки немає, вартовий **тихий**: жодного листа, жодного рядка в `eco_events` чи
`series_snapshots`. Єдиний запис — heartbeat `watch_state.check_20.0`, один рядок,
що оновлюється на місці. Інакше 96 повідомлень на добу привчили б нас ігнорувати
саме те повідомлення, якого ми чекаємо.

`modidx-postreboot.service` в `ExecStartPost` робить `systemctl disable` сам собі —
потрібен рівно один раз, інакше збирав би чек-лист після кожного ребуту назавжди.
**Тому перед КОЖНИМ плановим перезавантаженням його треба увімкнути заново:**
```bash
systemctl enable modidx-postreboot.service   # перед ребутом
```
Звіт лягає в `var/logs/postreboot-<UTC>.txt`, а `var/` у git не йде — тому для сесії
без SSH його треба вкласти в `ops/outbox/` вручну.

## Перевірка

Не «додав юніт», а вивід із системи:
```bash
systemctl list-unit-files 'modidx-*'          # що встановлено і в якому стані
systemctl list-timers 'modidx-*' --all        # коли спрацює наступний раз
systemctl status 'modidx-*'
journalctl -u modidx-runner@1 -f
```
`systemctl list-units 'modidx-*'` тут недостатньо: він показує лише **завантажені**
юніти, а oneshot, який ще ні разу не запускався, у списку відсутній — і виглядає як
«у системі немає», хоча файл на місці.
