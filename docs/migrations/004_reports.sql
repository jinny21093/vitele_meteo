-- docs/migrations/004_reports.sql — U8 «Суточный ИИ-отчёт»
-- Спека: docs/weather-report-spec.md v1.1.1 §5.1 (DDL verbatim: 14 колонок,
-- llm_error, CHECK 4 статусов, UNIQUE idempotency_key, schema_version=1) + 2 индекса.
--
-- НОМЕР МИГРАЦИИ. Правило спеки §5.1: N = MAX(schema_migrations.version)+1 на
-- момент внедрения («сверить на месте»). Посылка M4 («version=2 → ожидаемо N=3»)
-- написана до фиксации миграции этапа B. Свежий read-only снапшот живой БД
-- (2026-10-01, sha256 1d844c74…, выкачка scripts/u8_db_snapshot.py) показывает:
--   version=2  epoch UTC, surrogate PK+UNIQUE(ts), L1, WAL, …
--   version=3  stage B: forecast.text (zambretti/sager), aggregates, api
--                (applied_at=1789544905 — 2026-09-24, до акцепта спеки 2026-09-25)
-- → MAX(version)=3, N=4: файл 004_reports.sql, строка (version=4, 'U8 reports').
-- Вставка под занятый version=3 (003_reports.sql) нарушила бы trail миграций:
-- гвард «нет версии» не пропустил бы запись, и таблица reports осталась бы
-- без версии в schema_migrations.
--
-- ИДЕМПОТЕНТНОСТЬ (директива U8): CREATE — только при отсутствии таблицы
-- (IF NOT EXISTS); строка версии — только при отсутствии version=4
-- (INSERT…WHERE NOT EXISTS). Повторное применение — no-op.
--
-- ПРИМЕНЕНИЕ (только при деплое, после приёмки кода и команды владельца):
--   sqlite3 /home/auditbot/weather-dash/weather.db < docs/migrations/004_reports.sql
-- Проверка после применения:
--   sqlite3 …/weather.db "SELECT * FROM schema_migrations ORDER BY version;"
--   sqlite3 …/weather.db "PRAGMA table_info(reports);"

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day_epoch INTEGER NOT NULL,
  generated_at INTEGER NOT NULL,
  facts_json TEXT NOT NULL,
  llm_text TEXT,
  llm_model TEXT,
  llm_tokens_in INTEGER,
  llm_tokens_out INTEGER,
  llm_error TEXT,
  delivery_status TEXT NOT NULL
    CHECK (delivery_status IN ('pending','pending_retry','sent','failed')),
  delivery_attempts INTEGER DEFAULT 0,
  delivery_error TEXT,
  idempotency_key TEXT UNIQUE NOT NULL,
  schema_version INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_reports_day ON reports(day_epoch);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(delivery_status, generated_at);

INSERT INTO schema_migrations(version, applied_at, description)
SELECT 4, strftime('%s','now'), 'U8 reports'
WHERE NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 4);
