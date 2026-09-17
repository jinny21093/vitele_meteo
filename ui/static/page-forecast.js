"use strict";
/* weather-ui page-forecast.js — экран «Прогноз» (ТЗ weather-ui-spec.md §4.5;
   §5.6 в фактическом конверте этапа B — v1.2.6). Принцип U5: формулы
   (Zambretti/Sager/persistence) НЕ дублируем — /api/forecast отдаёт последний
   прогон таблицы forecast, который пишет weather_aggregator ежечасно
   (systemd timer *:02:00, issued_at=int(now) прогона).
   - polling 15 мин (§4.5) + ручной refresh; W.poll (backoff, m-6).
   - Замбретти крупно: text этапа B = "en — ru" -> ru крупно, en мелко; буква
     Замбретти в БД НЕ пишется (находка разведки U5-R2) — не показываем.
     Иконки в /static/icons/ нет (только favicon.svg) — экран текстовый.
   - Persistence-таблица (.stat-table): горизонты 1/3/6 ч (12/24 ч этап B не
     пишет — доклад U5-R2); ΔT/ΔP — относительно current из /api/now
     (арифметика отображения, не формула прогноза); если /api/now упал —
     показываем абсолюты без Δ (m-11).
   - Sager: строка в БД есть только днём (формула этапа B write_forecasts:
     10 <= локальный час дачи < 16, TZ дачи = W.tzOffset) -> ночью плашка
     «Не применимо (ночь)»; днём без строки — «ещё не рассчитан».
   - U5-C2: available:false -> карточка «Прогноз ещё не сформирован (нужно
     ≥ 3 ч истории)» (паттерн initMode); ошибка API — та же карточка.
   - U5-C3: «рассчитан HH:MM (возраст)»; stale — жёлтым (fc-stale).
   - m-11: каждый блок — try/catch-деградация; m-15: banner(null) ДО своих
     баннеров; DOM только через W.el/textContent, inline запрещён (CSP §0.6). */

(function () {
  const W = window.WEATHER;
  const el = W.el;                 // m-17: общий хелпер app.js
  const $ = (id) => document.getElementById(id);
  const POLL_MS = 900000;          // §4.5: polling 15 мин
  const DAY_LO = 10, DAY_HI = 16;  // окно дня этапа B (10 <= час < 16)
  let poller = null;

  /* локальный час дачи (TZ из /api/meta; фолбэк UTC+3 — app.js) */
  function dachaHour(nowSec) {
    return new Date((nowSec + W.tzOffset) * 1000).getUTCHours();
  }

  /* со знаком: "+1.2" / "0.0" / "-3.0" (минус — от toFixed) */
  function signed(v, digits) {
    if (v === null || v === undefined || isNaN(Number(v))) return "—";
    const n = Number(v);
    return (n > 0 ? "+" : "") + n.toFixed(digits);
  }

  /* переключение init-карточка <-> данные (U5-C2) */
  function showInit(title, note) {
    $("fc-data").classList.add("hidden");
    $("fc-init").classList.remove("hidden");
    const box = $("fc-init-body");
    box.textContent = "";
    box.appendChild(el("div", "big", title));
    box.appendChild(el("div", "muted", note));
  }
  function showData() {
    $("fc-init").classList.add("hidden");
    $("fc-data").classList.remove("hidden");
  }

  /* U5-C3: «рассчитан HH:MM (возраст)»; stale — жёлтым */
  function renderFresh(d, nowSec) {
    const box = $("fc-fresh");
    box.textContent = "";
    const line = el("div", d.stale ? "fc-stale" : "muted",
      "рассчитан " + W.fmtTime(d.calc_ts) +
      " (" + W.timeAgo(d.calc_ts, nowSec) + ")");
    box.appendChild(line);
    if (d.stale) {
      box.appendChild(el("div", "fc-stale",
        "данные устарели (агрегатор этапа B не запускался > 2 ч)"));
    }
  }

  /* Замбретти: ru крупно, en мелко; horizons/confidence — подписью */
  function renderZambretti(z) {
    const box = $("fc-zam");
    box.textContent = "";
    if (!z || !z.text) {
      box.appendChild(el("span", "muted", "нет данных"));
      return;
    }
    const parts = String(z.text).split(" — ");
    const ru = parts.length > 1 ? parts[parts.length - 1] : String(z.text);
    const en = parts.length > 1 ? parts.slice(0, -1).join(" — ") : "";
    box.appendChild(el("div", "fc-zam-ru", ru));
    if (en) box.appendChild(el("div", "fc-zam-en muted", en));
    const hs = (z.targets || []).map((t) => "+" + t.horizon_h + " ч").join(", ");
    box.appendChild(el("div", "muted",
      "горизонты: " + (hs || "—") +
      " · уверенность " + W.num(z.confidence, 2)));
  }

  /* persistence-таблица: горизонты 1/3/6 ч; ΔT/ΔP относительно current */
  function renderTable(d, cur) {
    const wrap = $("fc-table");
    wrap.textContent = "";
    const rows = d.persistence || [];
    if (!rows.length) {
      wrap.appendChild(el("span", "muted", "нет данных"));
      return;
    }
    const tb = el("table", "stat-table");
    const trh = el("tr");
    for (const h of ["Через", "ΔT, °C", "ΔP, мм", "T, °C", "P, мм", "Влажн, %"]) {
      trh.appendChild(el("th", "", h));
    }
    tb.appendChild(trh);
    for (const r of rows) {
      const tr = el("tr");
      tr.appendChild(el("td", "", "+" + r.horizon_h + " ч"));
      tr.appendChild(el("td", "", cur ? signed(r.t_out_c - cur.outdoor_temp_c, 1) : "—"));
      tr.appendChild(el("td", "", cur ? signed(r.p_rel_mmhg - cur.pressure_rel_mmhg, 1) : "—"));
      tr.appendChild(el("td", "", W.num(r.t_out_c, 1)));
      tr.appendChild(el("td", "", W.num(r.p_rel_mmhg, 1)));
      tr.appendChild(el("td", "", W.num(r.rh_out_pct, 0)));
      tb.appendChild(tr);
    }
    wrap.appendChild(tb);
    wrap.appendChild(el("p", "muted", cur
      ? "сейчас: " + W.num(cur.outdoor_temp_c, 1) + " °C, " +
        W.num(cur.pressure_rel_mmhg, 1) + " мм рт. ст. — Δ = прогноз − сейчас"
      : "Δ недоступна: текущие показания не получены (m-11)"));
  }

  /* Sager: ночью «Не применимо (ночь)» (логика этапа B 10<=h<16);
     днём без строки в прогоне — «ещё не рассчитан» */
  function renderSager(s, nowSec) {
    const box = $("fc-sager");
    box.textContent = "";
    const h = dachaHour(nowSec);
    if (h < DAY_LO || h >= DAY_HI) {
      box.appendChild(el("div", "fc-night", "Не применимо (ночь)"));
      return;
    }
    if (!s || !s.text) {
      box.appendChild(el("span", "muted",
        "в последнем прогоне нет — рассчитается в ближайший час"));
      return;
    }
    box.appendChild(el("div", "fc-zam-ru", s.text));
    box.appendChild(el("div", "muted",
      "горизонт: +" + Math.max(0, Math.round(((s.target_ts || 0) - nowSec) / 3600)) +
      " ч · уверенность " + W.num(s.confidence, 2)));
  }

  /* один цикл обновления (§4.5: polling 15 мин + ручной refresh) */
  async function refresh() {
    const nowSec = Math.floor(Date.now() / 1000);
    let d = null;
    try { d = await W.apiFetch("/api/forecast"); }
    catch (e) { /* m-11: баннер уже показан apiFetch */ }
    let cur = null;
    try {
      const n = await W.apiFetch("/api/now");
      cur = n.current || null;
    } catch (e) { cur = null; }      // Δ деградирует до «—», прогноз живёт
    try {
      if (!d) {
        showInit("Прогноз недоступен",
          "ошибка API — повтор через 15 мин или кнопкой «Обновить»");
      } else if (!d.available) {
        showInit("Прогноз ещё не сформирован",
          "нужно ≥ 3 ч истории — этап B посчитает в ближайший часовой прогон");
      } else {
        showData();
        renderFresh(d, nowSec);
        renderZambretti(d.zambretti);
        renderTable(d, cur);
        renderSager(d.sager, nowSec);
      }
    } catch (e) { /* m-11: блок продолжает жить */ }
    await W.refreshHeader();         // §4.0: шапка на всех экранах
    const lu = $("last-update");
    if (lu) lu.textContent = "обновлено в " + W.fmtTime(nowSec);
    W.banner(null);                  // m-15: сброс ДО возможных баннеров
  }

  W.ready.then(() => {
    poller = W.poll(refresh, POLL_MS);          // §4.5: polling 15 мин
    const btn = $("refresh");
    if (btn) btn.addEventListener("click", () => poller.refreshNow());
  });
})();
