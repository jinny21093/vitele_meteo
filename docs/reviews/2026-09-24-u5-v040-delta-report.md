# Отчёт: U5 дельты по решениям владельца (issued_values + letter) + деплой v0.4.0

**Date:** 2026-09-24 23:10 MSK
**From:** GLM (агент)
**To:** Ревьюер, владелец
**Status:** Выполнено — деплой v0.4.0 на vitele: ALL STEPS PASSED (0–5 + ZT-бонус)
**Диапазон:** `093ccaf..cc592a3` · compare: https://github.com/jinny21093/vitele_meteo/compare/093ccaf...cc592a3

## 0. Решения владельца — приняты к исполнению

1. ΔT/ΔP — вариант (б): база расчёта на `issued_at`, не от «сейчас» → `issued_values` в конверте, клиент считает от них.
2. Буква Замбретти — ДА: этап B пишет `letter` в колонку `forecast.letter`, UI показывает крупно. Горизонт 24 ч — НЕТ (отклонён, остаётся persistence 1/3/6 + Замбретти +6/+12).

## 1. Коммиты (git log --oneline 093ccaf..HEAD + show --stat)

```
cc592a3 fix(deploy): v040 чеклист — md5-путь repo с префиксом ui/, grep -a на лог с бинарным мусором, маркер issued_values
335c8f2 test(u5): смоук U5-T2 (letter/issued_values/Δ-математика) + deploy-скрипт v0.4.0
671d3b0 docs(spec): v1.2.6 правкой — Δ от issued_values («от базы расчёта»), letter в конверте, 24 ч отклонён
428d53b feat(u5-C): Δ от issued_values, буква Замбретти крупно, Sager-горизонт от calc_ts
c159b08 feat(u5-U): /api/forecast — issued_values (Δ от базы расчёта) + letter Замбретти
e2a7d6a feat(u5-B): write_forecasts пишет букву Замбретти (forecast.letter)
8d362d5 docs(reviews): хронологический архив — INDEX + схема YYYY-MM-DD; ... (до дельт, в диапазон входит)
```

- `e2a7d6a` — stage-b/weather_aggregator.py | 30 +++++++++…++− | 1 file changed, 27 insertions(+), 3 deletions(-)
- `c159b08` — ui/server.py | 1 file changed, 52 insertions(+), 18 deletions(-)
- `428d53b` — ui/static/{page-forecast.js,forecast.html,style.css} | 3 files changed, 52 insertions(+), 29 deletions(-)
- `671d3b0` — docs/weather-ui-spec.md | 1 file changed, 4 insertions(+), 2 deletions(-)
- `335c8f2` — deploy-tools/{weather_ui_smoke.py (+65), weather_ui_deploy_v040.py (+247)} | 2 files changed, 312 insertions(+)
- `cc592a3` — deploy-tools/weather_ui_deploy_v040.py | 1 file changed, 13 insertions(+), 7 deletions(-)

**Проверка 6e99a87 (docs-roadmap):** подтверждено — запись «UI U0-U4 = DEPLOYED (v0.3.0, 2026-09-18)» в roadmap §1 (строка 24) присутствует, §8 v1.5 тоже; досдача не потребовалась.

## 2. Верба­тим-цитаты

**U5-B1 — миграция (stage-b/weather_aggregator.py, migrate_letter, :72–86):**
```python
def migrate_letter(con):
    """U5-B1 (решение владельца): forecast.letter — буква Замбретти рядом с
    text. Идемпотентно. В schema_migrations НЕ пишем (отличие от migrate_v3):
    чек смоука "meta migrations" ждёт последнюю версию копии БД = 2, ...
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(forecast)")]
    if "letter" in cols:
        return False
    con.execute("BEGIN IMMEDIATE")
    con.execute("ALTER TABLE forecast ADD COLUMN letter TEXT")
    con.execute("COMMIT")
```
Согласовано с фактической практикой смоука: чек `meta migrations` в weather_ui_smoke.py:294 ждёт версию копии = 2, а смоук-prepare добавлял `text` без записи версии — letter сделана так же.

**U5-B1 — запись letter (write_forecasts, :324–330):**
```python
    letter, text = zambretti(last[2], tend, mon)
    if text:
        for hz in (21600, 43200):
            con.execute("INSERT OR REPLACE INTO forecast(issued_at,target_ts,source,"
                        "confidence,text,letter) VALUES(?,?,?,?,?,?)",
                        (issued, issued + hz, "zambretti", 0.5, text, letter))
            n += 1
```
Источник буквы — тот же вызов `zambretti()`, что даёт text (один вызов — один источник истины). 24 ч горизонт не добавлен.

**U5-U1 — issued_values (ui/server.py, _api_forecast, :824–829):**
```python
            iv = con.execute(
                "SELECT outdoor_temp_c, pressure_rel_mmhg FROM weather "
                "WHERE ts<=? ORDER BY ts DESC LIMIT 1", (calc_ts,)).fetchone()
        finally:
            con.close()
        issued_values = ({"t_out_c": iv[0], "p_rel_mmhg": iv[1]} if iv else None)
```
В конверте (:846–851): `"issued_values": issued_values` + `zambretti` с `"letter": zam_letter` (может быть null — легаси). Совместимость: колонка letter селектится только при наличии в таблице (`PRAGMA table_info`) — до прогона этапа B с миграцией конверт unchanged, без 503.

**U5-C1 — Δ-вычисления клиента (ui/static/page-forecast.js, :102–140):**
```js
  function delta(fc, base) {
    if (base === null || base === undefined) return "—";
    return signed(fc - base, 1);
  }
  ...
      tr.appendChild(el("td", "", delta(r.t_out_c, iv && iv.t_out_c)));
      tr.appendChild(el("td", "", delta(r.p_rel_mmhg, iv && iv.p_rel_mmhg)));
  ...
    wrap.appendChild(el("p", "muted", haveBase
      ? "Δ = прогноз − расчёт (issued " + W.fmtTime(d.calc_ts) + ")"
      : "Δ = прогноз − расчёт; база недоступна"));
```

**U5-C3 — горизонт Sager от calc_ts (:161):**
```js
      "горизонт: +" + Math.max(0, Math.round(((s.target_ts || 0) - calcTs) / 3600)) +
```
Вызов: `renderSager(d.sager, nowSec, d.calc_ts)`.

**Версии:** остаются **0.4.0** — v0.4.0 ни разу не деплоилась (на VM была v0.3.0), дельты ревью — часть той же неотгруженной версии; аргумента для 0.4.1 нет. `SERVER_VERSION = "0.4.0"`, в шапке server.py добавлен блок «Дельта ревью владельца до деплоя».

## 3. Смоук: 127 OK / 0 FAIL (было 120, +7 новых U5-T2)

- U5-T2a: строка с letter → конверт содержит (letter='A')
- U5-T2c: issued_values от issued_at (10.0/770.0), не current 12.8/775.8
- U5-T2c: Δ-математика = +2.0 (forecast 12.0 − issued 10.0, не от current; p: 772.0 − 770.0 = +2.0)
- U5-T2d: клиент Δ от issued_values (page-forecast.js содержит issued_values/calc_ts)
- U5-T2b: легаси-строка без letter → letter null, текст жив
- U5-T2e: истории до issued_at нет → issued_values null
- U5-T2b: экран жив при легаси/деградации (/forecast 200)

## 4. py_compile / node --check

- `python -m py_compile stage-b/weather_aggregator.py ui/server.py deploy-tools/weather_ui_smoke.py deploy-tools/weather_ui_deploy_v040.py` — OK
- `node --check ui/static/page-forecast.js` — OK

## 5. Не сделано и почему

- Горизонт 24 ч — отклонён владельцем (не делаем).
- Запрос `/api/now` со страницы прогноза убран: Δ больше не считается от «сейчас» (U5-C1), `cur` использовался только для Δ; шапку обслуживает refreshHeader (свой запрос) — поведение вне прогноза не менялось (U5-U2).
- Реальный прогон write_forecasts с letter НЕ форсировался — по чеклисту он в ближайший hourly-слот *:02 (см. деплой-чеклист, хвост).
- Деплой-скрипт v040: 3 дефекта первой итерации (md5-путь repo, grep бинарного лога без -a, must=1 при 6 вхождениях) — исправлены в cc592a3, повторный прогон ALL STEPS PASSED. На результат деплоя не влияли (синк/рестарт/конверт были корректны с первого прогона).

## 6. Deploy-чеклист v0.4.0 (факт)

| Позиция | Значение |
| --- | --- |
| md5 ui/server.py | `3c9aca04c8556a1c7f529d1cd4f9c9cb` |
| md5 ui/static/app.js | `db81cb32a184196705e8393e74595534` |
| md5 ui/static/page-forecast.js | `23a73e00e330b55c3711772abae0401d` |
| md5 ui/static/forecast.html | `2f823d7f5d6db114fc7ae5c4ea7c7ca2` |
| Сверка | repo → weather-dash/ui (UIDIR) md5 OK, `cp -a ui/. → UIDIR` |
| Рестарт | ТОЛЬКО ui/server.py (kill PID 8089 → setsid nohup); weather-api :8090 и этап B не трогались |
| Лог старта | `start version=0.4.0 bind=192.168.8.146,10.147.17.101 port=8089` |
| Статика | `static loaded files=15 bytes=302865` (было 14 — добавлен forecast.html); dropped=0 |
| health | 200 `{"status":"ok"}` (LAN + ZT-бонус с outpost: ZT_BIND_OK) |
| /api/forecast | available:true, calc_ts свежий, stale:false; `issued_values={'t_out_c': 17.4, 'p_rel_mmhg': 766.3}` (живая база расчёта); zambretti.letter=null (легаси — ожидаемо до прогона *:02), text живой; persistence ×3 |
| Страницы | 200: / /day /month /events /forecast /settings; app.js UI_VERSION=0.4.0; в page-forecast.js есть issued_values |
| ⚠️ Хвост | Реальный прогон write_forecasts с letter — в ближайший hourly-слот **\*:02**; до него легаси-строки отдают letter: null (конверт не падает). Проверка после слота: `curl -u $CRED http://192.168.8.146:8089/api/forecast | grep -o 'letter[^,]*'` |

## 7. Гит (для самопроверки владельцем)

- Репо: https://github.com/jinny21093/vitele_meteo (публичный, читается без токена)
- Диапазон дельт: https://github.com/jinny21093/vitele_meteo/compare/093ccaf...cc592a3
- Этот отчёт: docs/reviews/2026-09-24-u5-v040-delta-report.md · https://github.com/jinny21093/vitele_meteo/tree/main/docs/reviews
- Все дельты этапа B / UI-сервера / клиента / спеки / смоука — закоммичены и запушены; владелец может сверять md5/диффы сам по ссылкам.
