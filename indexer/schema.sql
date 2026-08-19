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
  PRIMARY KEY (taken_at, series)
);
