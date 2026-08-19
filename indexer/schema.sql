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
