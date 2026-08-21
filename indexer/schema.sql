-- Схема індексу. Черга задач тут же, у Postgres: жодного Redis/RabbitMQ.
CREATE TABLE IF NOT EXISTS modules (
  id          bigserial PRIMARY KEY,
  repo        text NOT NULL,
  module      text NOT NULL,
  series      text NOT NULL,
  head_sha    text,              -- sha теки модуля: змінився → треба перепрогнати
  manifest    jsonb,
  last_commit timestamptz,
  seen_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (repo, module, series)
);
CREATE INDEX IF NOT EXISTS modules_series_idx ON modules (series);

-- ── Три незалежні вісі (ops/inbox/0010) ──────────────────────────────────────
-- Кожна відповідає на СВОЄ питання, і змішувати їх в одну колонку не можна:
--   availability — чи можемо ми модуль дістати       (наша спроможність)
--   installable  — чи заявляє сам модуль, що ставиться (факт із манифеста)
--   runs.status  — що сталося, коли ми запустили      (результат прогону)
-- Модуль з installable=false цілком ДОСТУПНИЙ (код у git), просто ми свідомо
-- його не запускаємо. Записати йому env або «не протестовано» було б брехнею
-- в обидві сторони: у OCA це метапакети, залишки _unported і оболонки для
-- депрекації, тобто навмисна властивість, а не поломка.
ALTER TABLE modules ADD COLUMN IF NOT EXISTS availability text NOT NULL DEFAULT 'open_source';
ALTER TABLE modules ADD COLUMN IF NOT EXISTS installable boolean;   -- NULL = чекауту немає, не знаємо

-- ── Метадані з __manifest__.py (indexer/manifests.py) ────────────────────────
-- Для 16.0/17.0 чекаутів немає, тому там усе лишається NULL. Це чесно й нічого
-- не коштує: краще порожньо, ніж вигадано.
ALTER TABLE modules ADD COLUMN IF NOT EXISTS category         text;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS author_raw       text;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS vendors          text[];  -- без OCA-парасольки
ALTER TABLE modules ADD COLUMN IF NOT EXISTS is_oca           boolean;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS license          text;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS summary          text;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS manifest_version text;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS depends          text[];
ALTER TABLE modules ADD COLUMN IF NOT EXISTS ext_deps         jsonb;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS website          text;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS maintainers      text[];
ALTER TABLE modules ADD COLUMN IF NOT EXISTS auto_install     boolean;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS application      boolean;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS manifest_error   text;   -- чому не розібрали
ALTER TABLE modules ADD COLUMN IF NOT EXISTS manifest_at      timestamptz;

-- Історія модуля з чекауту. Дата останнього коміту — найсильніший сигнал
-- покинутості: «остання зміна 2023-04-11» поруч із «немає гілки 19.0» це вже
-- висновок, а не факт. Коштує один `git log` по теці, без жодного зовнішнього
-- запиту.
ALTER TABLE modules ADD COLUMN IF NOT EXISTS last_module_commit timestamptz;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS commits_12m        int;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS top_authors        text[];
ALTER TABLE modules ADD COLUMN IF NOT EXISTS files_count        int;
ALTER TABLE modules ADD COLUMN IF NOT EXISTS git_at             timestamptz;
CREATE INDEX IF NOT EXISTS modules_lastcommit_idx ON modules (series, last_module_commit DESC);

CREATE INDEX IF NOT EXISTS modules_category_idx ON modules (series, category);
CREATE INDEX IF NOT EXISTS modules_avail_idx    ON modules (availability, installable);
CREATE INDEX IF NOT EXISTS modules_vendors_idx  ON modules USING gin (vendors);

CREATE TABLE IF NOT EXISTS runs (
  id           bigserial PRIMARY KEY,
  module_id    bigint NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  series       text NOT NULL,
  head_sha     text,
  status       text NOT NULL,     -- ok | warn | dep | fail | timeout | skipped
  cause        text,              -- машинна категорія причини
  detail       text,              -- один рядок для людини
  log_tail     text,              -- хвіст логу, стиснений на рівні таблиці
  duration_ms  integer,
  odoo_image   text,
  batched      boolean NOT NULL DEFAULT false,
  latest_version text,            -- версія з ir_module_module: реальний факт, не з манифеста
  created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS latest_version text;
-- Версія правил класифікатора, якою отриманий цей статус (indexer/classify.py).
-- Разом із odoo_image це повний опис стенду прогону. Без них не відрізнити
-- «модуль змінився» від «ми змінили стенд» — і друге їде у фід як перше.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS rules_version text;
CREATE INDEX IF NOT EXISTS runs_module_idx  ON runs (module_id, created_at DESC);
CREATE INDEX IF NOT EXISTS runs_series_idx  ON runs (series, created_at DESC);
CREATE INDEX IF NOT EXISTS runs_status_idx  ON runs (status);

-- Останній результат на кожен (модуль, серія)
CREATE OR REPLACE VIEW latest_runs AS
SELECT DISTINCT ON (module_id) *
FROM runs ORDER BY module_id, created_at DESC;

CREATE TABLE IF NOT EXISTS jobs (
  id         bigserial PRIMARY KEY,
  module_id  bigint NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  series     text NOT NULL,
  priority   int  NOT NULL DEFAULT 100,   -- менше = раніше
  state      text NOT NULL DEFAULT 'queued', -- queued | running | done | error
  attempts   int  NOT NULL DEFAULT 0,
  locked_by  text,
  locked_at  timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_pick_idx ON jobs (state, priority, id);

-- Тут стояв UNIQUE (module_id, state) — і це був баг, який спрацював би на
-- ДРУГОМУ проході harvest: новий head_sha → enqueue вставляє другу задачу на той
-- самий модуль, її фінальний UPDATE state='done' стикається з рядком 'done' від
-- першого проходу. DEFERRABLE не рятує: db.py тримає autocommit=True, тобто кожен
-- стейтмент — окрема транзакція і відкладати перевірку нікуди. Далі виняток пішов
-- би в except, той поставив би 'error', і черга почала б забиватися.
--
-- Натомість унікальність лише серед АКТИВНИХ задач: один модуль не може стояти
-- в черзі двічі, але скільки завгодно разів може бути пройденим. Завершені задачі
-- видаляються (runner.finish), історія живе в runs.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_module_id_state_key;
CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_uniq ON jobs (module_id)
  WHERE state IN ('queued','running');

-- ── Інвентаризація оточення ─────────────────────────────────────────────────
-- Одна й та сама робота потрібна у двох місцях (ops/inbox/0015 і 0016), тому
-- робиться раз: похідний образ проти env і секція залежностей на сторінці
-- модуля мусять спиратися на ті самі факти, інакше вони почнуть суперечити.

-- Що лежить в образі. Знімок НА ТЕГ: склад образу змінюється, і «пакета немає»
-- без мітки тегу одного дня почне брехати саме тому, що ми оновили образ.
CREATE TABLE IF NOT EXISTS image_packages (
  image_tag text NOT NULL,
  kind      text NOT NULL,          -- python | bin
  name      text NOT NULL,
  version   text,
  taken_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (image_tag, kind, name)
);

-- Модулі ядра Odoo. Без цього списку `base` і `account` потрапили б у
-- «невідоме», і сторінка залежностей виглядала б так, ніби половина
-- залежностей загублена. Склад ядра різний між серіями, тому тег обов'язковий.
CREATE TABLE IF NOT EXISTS core_addons (
  series    text NOT NULL,
  name      text NOT NULL,
  image_tag text NOT NULL,
  taken_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (series, name)
);

-- Який образ проганяти для якої серії. Без цієї таблиці перехід на похідний
-- образ (і 24.09 на офіційний odoo:20.0) був би правкою коду замість зміни
-- одного значення.
CREATE TABLE IF NOT EXISTS series_image (
  series    text PRIMARY KEY,
  image     text NOT NULL,
  note      text,
  set_at    timestamptz NOT NULL DEFAULT now()
);

-- Пакет, який оголошений модулем, але не ставиться в стандартному оточенні
-- (немає wheel, конфлікт версій, потрібен компілятор). Це НЕ env: це факт про
-- модуль, і він цінний сам по собі.
CREATE TABLE IF NOT EXISTS unbuildable_deps (
  image_tag text NOT NULL,
  name      text NOT NULL,
  error     text,
  taken_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (image_tag, name)
);

-- Зміни стану модуля — джерело для Atom-фідів.
--
-- Подією є САМЕ ЗМІНА, а не прогін. Щоденний прохід дає ~4 000 прогонів на добу,
-- і якби кожен ішов у фід, читати його було б неможливо. У фід іде лише те, що
-- сталося вперше або відрізняється від попереднього разу.
--
-- `seeded` — записи першого проходу. Вони заповнюють базу мовчки: без цього
-- перший же підписник отримав би 4 447 листів «новий: verified» і відписався.
CREATE TABLE IF NOT EXISTS state_changes (
  id         bigserial PRIMARY KEY,
  module_id  bigint NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  series     text NOT NULL,
  state_old  text,
  state_new  text NOT NULL,
  status_old text,
  status_new text,
  run_id     bigint REFERENCES runs(id) ON DELETE SET NULL,
  at         timestamptz NOT NULL,
  seeded     boolean NOT NULL DEFAULT false,
  UNIQUE (run_id)
);
-- ops/inbox/0019 A: подією фіда є зміна МОДУЛЯ. Якщо між двома послідовними
-- прогонами змінився стенд — образ або версія правил класифікатора, — різниця
-- в результаті належить нам: bench=true, і в стрічки рядок не йде. Сам рядок
-- лишається в таблиці: на ньому тримається порівняння «попередній стан».
ALTER TABLE state_changes ADD COLUMN IF NOT EXISTS bench boolean NOT NULL DEFAULT false;
DROP INDEX IF EXISTS state_changes_feed_idx;      -- предикат був лише WHERE NOT seeded
CREATE INDEX IF NOT EXISTS state_changes_live_idx ON state_changes (at DESC)
  WHERE NOT seeded AND NOT bench;
CREATE INDEX IF NOT EXISTS state_changes_mod_idx ON state_changes (module_id, series, at DESC);

-- Куди дійшли при матеріалізації змін. Одна колонка, один рядок: віконна
-- функція по всій `runs` щогодини — марна робота, коли нових прогонів десяток.
CREATE TABLE IF NOT EXISTS feed_cursor (
  one          boolean PRIMARY KEY DEFAULT true CHECK (one),
  last_run_id  bigint NOT NULL DEFAULT 0,
  seeded_at    timestamptz
);
INSERT INTO feed_cursor (one) VALUES (true) ON CONFLICT DO NOTHING;

-- Стан вартового наступної серії (indexer/watch20.py): коли останній тик, коли
-- останній повний обхід OCA. Потрібен, щоб обхід 232 репозиторіїв не йшов
-- кожні 15 хвилин, і щоб `/status.json` показував, що вартовий живий.
CREATE TABLE IF NOT EXISTS watch_state (
  key  text PRIMARY KEY,
  at   timestamptz NOT NULL DEFAULT now(),
  note text
);

-- Події екосистеми: поява й зникнення репозиторію, перша гілка серії.
-- Інша аудиторія, ніж зміни стану модуля, тому окрема таблиця й окремий фід.
CREATE TABLE IF NOT EXISTS eco_events (
  id     bigserial PRIMARY KEY,
  kind   text NOT NULL,          -- repo_added | repo_gone | branch_first
  repo   text NOT NULL,
  series text,
  at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, repo, series)
);
CREATE INDEX IF NOT EXISTS eco_events_at_idx ON eco_events (at DESC);

-- Знімки для публічного лідерборда: історія цифр по серіях
CREATE TABLE IF NOT EXISTS series_snapshots (
  taken_at    timestamptz NOT NULL DEFAULT now(),
  series      text NOT NULL,
  repos       int NOT NULL,
  modules     int NOT NULL,
  installs_ok int,
  method      text NOT NULL DEFAULT 'v2',   -- версія методики підрахунку модулів
  PRIMARY KEY (taken_at, series)
);
ALTER TABLE series_snapshots ADD COLUMN IF NOT EXISTS method text NOT NULL DEFAULT 'v2';

-- PRIMARY KEY (taken_at, series) при taken_at DEFAULT now() не конфліктує ніколи,
-- тому ON CONFLICT DO NOTHING був мертвим кодом: кожен ручний запуск harvest
-- додавав ще одну точку за той самий день і робив публічний графік темпу
-- зубчастим від наших же перевірок. Конфлікт має бути по (день, серія, метод).
--
-- method: v1 — будь-яка тека верхнього рівня (до 19.08.2026);
--         v2 — тека з __manifest__.py на першому рівні.
-- Нахил і будь-які похідні цифри рахувати ТІЛЬКИ в межах одного методу.
-- Саме AT TIME ZONE 'UTC', а не taken_at::date: приведення timestamptz до date
-- залежить від параметра TimeZone сесії, тому НЕ immutable, і Postgres відмовляє
-- будувати по ньому індекс («functions in index expression must be marked
-- IMMUTABLE»). Фіксація зони робить вираз детермінованим. Заодно доба зрізу
-- скрізь означає добу UTC, незалежно від налаштувань клієнта.
CREATE UNIQUE INDEX IF NOT EXISTS series_snapshots_daily
  ON series_snapshots (((taken_at AT TIME ZONE 'UTC')::date), series, method);
