"use strict";
/* weather-ui page-now.js — экран «Сейчас» (ТЗ §4.1, polling 30 с).
   Карточки, последнее событие за сутки, sparkline T и P за 6 ч (Chart.js),
   кнопка «Обновить». Данные — только /api/now + /api/history + /api/events. */

(function () {
  const W = window.WEATHER;
  const el = W.el;                 // m-17: общий хелпер app.js
  const $ = (id) => document.getElementById(id);
  let chartT = null;
  let chartP = null;
  let poller = null;

  function kv(label, value, valueCls) {
    const row = el("div", "kv");
    row.appendChild(el("span", "k", label));
    const v = el("span", "v" + (valueCls ? " " + valueCls : ""), value);
    row.appendChild(v);
    return row;
  }

  function big(value, color) {
    const d = el("div", "big main-val", value);
    if (color) d.style.color = color;      // CSSOM — CSP это разрешает
    return d;
  }

  /* --- шапка §4.0: свежесть + батарея --- */
  function header(nowSec, status, batteryRaw, events) {
    const gap = status.gap_s;
    // m-2: now — верхний уровень контракта /api/now, передаётся параметром
    // (в status поля now нет); fallback Date.now() оставлен
    let kind = "ok", text = "обновлено " + W.timeAgo(status.last_poll_ts, nowSec || Math.floor(Date.now() / 1000));
    if (gap > 600) { kind = "bad"; }
    else if (gap > 120) { kind = "warn"; }
    W.setFreshness(kind, text);
    // батарея: общий whitelist OK-строк коллектора W.batteryOk (M-1, app.js);
    // иначе fail-safe 🟡, 🔴 — активное BATTERY_LOW за сутки
    const ok = W.batteryOk(batteryRaw);
    const lowOpen = (events || []).some(
      (r) => r.event_type === "BATTERY_LOW" && r.ts_end === null);
    if (lowOpen) W.setBattery("bad", "батарея: LOW");
    else if (ok) W.setBattery("ok", "батарея: ok");
    else W.setBattery("warn", "батарея: ?");   // нераспознано — fail-safe
  }

  /* --- карточки --- */
  function renderCards(d) {
    const c = d.current || {};
    const stale = d.status && d.status.gap_s > 600;   // §8: > 10 мин — серым
    const staleCls = stale ? " stale" : "";

    // Улица
    const out = $("outdoor-body");
    out.className = "card-body" + staleCls;
    out.textContent = "";
    out.appendChild(big(W.num(c.outdoor_temp_c, 1) + " °C",
      W.colorForTemp(c.outdoor_temp_c)));
    out.appendChild(kv("Влажность", W.num(c.outdoor_hum_pct, 0) + " %"));
    out.appendChild(kv("Точка росы", W.num(c.dew_point_c, 1) + " °C"));
    out.appendChild(kv("Wind chill", W.num(c.wind_chill_c, 1) + " °C"));

    // Дом
    const ind = $("indoor-body");
    ind.className = "card-body" + staleCls;
    ind.textContent = "";
    ind.appendChild(big(W.num(c.indoor_temp_c, 1) + " °C",
      W.colorForTemp(c.indoor_temp_c)));
    ind.appendChild(kv("Влажность", W.num(c.indoor_hum_pct, 0) + " %"));

    // Давление: rel крупно, abs мелко, тенденция 3 ч стрелкой + класс
    const pr = $("pressure-body");
    pr.className = "card-body" + staleCls;
    pr.textContent = "";
    pr.appendChild(big(W.num(c.pressure_rel_mmhg, 1), null));
    const tr = W.pressureTrend(d.p_tendency_3h);
    const trow = el("div", "kv");
    trow.appendChild(el("span", "k", "3 ч: " + tr.word));
    const tv = el("span", "trend " + tr.cls,
      (d.p_tendency_3h === null || d.p_tendency_3h === undefined ? "—" :
        (d.p_tendency_3h > 0 ? "+" : "") + W.num(d.p_tendency_3h, 2)) + " " + tr.arrow);
    trow.appendChild(tv);
    pr.appendChild(trow);
    pr.appendChild(kv("Абсолютное", W.num(c.pressure_abs_mmhg, 1) + " мм рт. ст."));

    // Ветер: скорость, порыв, стрелка + румб, avg2/avg10
    const wd = $("wind-body");
    wd.className = "card-body" + staleCls;
    wd.textContent = "";
    const wr = el("div", "wind-row");
    const arrow = c.wind_dir_card && W.RUMB_ARROW[c.wind_dir_card] ? W.RUMB_ARROW[c.wind_dir_card] : "·";
    wr.appendChild(el("span", "wind-arrow", arrow));
    wr.appendChild(big(W.num(c.wind_ms, 1) + " м/с", W.colorForWind(c.wind_ms)));
    wd.appendChild(wr);
    wd.appendChild(kv("Порыв", W.num(c.gust_ms, 1) + " м/с"));
    wd.appendChild(kv("Направление", (c.wind_dir_card || "—") + " (" + W.num(c.wind_dir_deg, 0) + "°)"));
    wd.appendChild(kv("avg2/avg10", W.num(c.wind_avg2_ms, 1) + " / " + W.num(c.wind_avg10_ms, 1) + " м/с"));

    // Дождь
    const rn = $("rain-body");
    rn.className = "card-body" + staleCls;
    rn.textContent = "";
    rn.appendChild(kv("Интенсивность", W.num(c.rain_rate_calc_mmh !== undefined ? c.rain_rate_calc_mmh : c.rain_rate_mmh, 2) + " мм/ч"));
    rn.appendChild(kv("Час", W.num(c.rain_hour_mm, 1) + " мм"));
    rn.appendChild(kv("Сутки", W.num(c.rain_day_mm, 1) + " мм"));
    rn.appendChild(kv("Месяц", W.num(c.rain_month_mm, 1) + " мм"));
    rn.appendChild(kv("Год", W.num(c.rain_year_mm, 1) + " мм"));

    // Солнце
    const sun = $("sun-body");
    sun.className = "card-body" + staleCls;
    sun.textContent = "";
    sun.appendChild(big(W.num(c.light_wm2, 0) + " Вт/м²", null));
    sun.appendChild(kv("UVI", W.num(c.uvi, 1)));
  }

  /* --- последнее событие за сутки (§4.1) --- */
  async function renderLastEvent(nowSec) {
    const box = $("last-event");
    try {
      const q = "from=" + (nowSec - 86400) + "&to=" + nowSec;
      const ev = await W.apiFetch("/api/events?" + q);
      const rows = ev.rows || [];
      headerCache = rows;
      box.textContent = "";
      if (!rows.length) {
        box.appendChild(el("span", "muted", "Событий за сутки нет"));
        return;
      }
      const r = rows[0];                    // свежайшее (ORDER BY ts_start DESC)
      const head = el("div", "big", r.event_type);
      head.style.fontSize = "1.15em";
      const sevCls = r.severity === "high" ? "bad" : (r.severity === "mid" ? "warn" : "ok");
      box.appendChild(head);
      box.appendChild(kv("Время", W.fmtTs(r.ts_start), sevCls));
      box.appendChild(kv("Severity", r.severity || "—", sevCls));
      if (r.value !== null && r.value !== undefined) {
        box.appendChild(kv("Значение", W.num(r.value, 2)));
      }
      if (r.duration_s !== null && r.duration_s !== undefined) {
        box.appendChild(kv("Длительность", Math.round(r.duration_s / 60) + " мин"));
      }
      if (r.context && r.context.t_out !== undefined) {
        box.appendChild(kv("T при событии", W.num(r.context.t_out, 1) + " °C"));
      }
    } catch (e) {
      headerCache = [];
      box.textContent = "";
      box.appendChild(el("span", "muted", "События недоступны"));
    }
  }

  /* --- sparkline T и P за 6 ч (§4.1) --- */
  async function renderSparks(nowSec) {
    if (typeof Chart === "undefined") return;   // chart.min.js не загрузился
    /* m-11: в try/catch (образец — renderLastEvent, §8): сбой спарклайнов
       не гасит карточки, шапку и «последнее событие». */
    try {
      const from = nowSec - 6 * 3600;
      const q = "from=" + from + "&to=" + nowSec +
        "&fields=ts,outdoor_temp_c,pressure_rel_mmhg";
      const h = await W.apiFetch("/api/history?" + q);
      const fi = {};
      (h.fields || []).forEach((f, i) => { fi[f] = i; });
      const labels = [], tData = [], pData = [];
      (h.rows || []).forEach((row) => {
        labels.push(W.fmtTime(row[fi.ts]));
        const t = row[fi.outdoor_temp_c];
        const p = row[fi.pressure_rel_mmhg];
        tData.push(t === null ? null : Number(t));
        pData.push(p === null ? null : Number(p));
      });
      const th = W.chartTheme();   // m-7: как в page-day/month, без ручного CSS
      const muted = th.muted;
      const grid = th.grid;
      const accent = th.accent;
      const baseOpts = {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { ticks: { maxTicksLimit: 7, color: muted, font: { size: 10 } },
               grid: { display: false } },
          y: { ticks: { color: muted, font: { size: 10 } },
               grid: { color: grid } }
        }
      };
      if (chartT) chartT.destroy();
      chartT = new Chart($("spark-t"), {
        type: "line",
        data: { labels, datasets: [{ data: tData, borderColor: accent,
          borderWidth: 2, pointRadius: 0, tension: .25, spanGaps: true }] },
        options: JSON.parse(JSON.stringify(baseOpts))
      });
      if (chartP) chartP.destroy();
      chartP = new Chart($("spark-p"), {
        type: "line",
        data: { labels, datasets: [{ data: pData, borderColor: "#dfa53f",
          borderWidth: 2, pointRadius: 0, tension: .25, spanGaps: true }] },
        options: JSON.parse(JSON.stringify(baseOpts))
      });
    } catch (e) { /* §8: спарклайны недоступны — остальной экран живёт */ }
  }

  let headerCache = [];

  /* --- один цикл обновления --- */
  async function refresh() {
    let d;
    try {
      d = await W.apiFetch("/api/now");
    } catch (e) {
      // m-13: «Инициализация…» живёт в initMode (пустая БД, retry 10 с)
      if (e && e.initMode) {
        const note = $("init-note");
        if (note) note.classList.remove("hidden");
      }
      throw e;                       // retry 10 с сохраняется
    }
    $("init-note").classList.add("hidden");
    renderCards(d);
    const nowSec = d.now || Math.floor(Date.now() / 1000);
    await Promise.all([renderLastEvent(nowSec), renderSparks(nowSec)]);
    header(d.now, d.status, (d.current || {}).battery_raw, headerCache);
    const lu = $("last-update");
    if (lu) lu.textContent = "обновлено в " + W.fmtTime(nowSec);
    W.banner(null);
  }

  W.ready.then(() => {
    poller = W.poll(refresh, 30000);            // §4.1: polling 30 с
    const btn = $("refresh");
    if (btn) btn.addEventListener("click", () => poller.refreshNow());
  });
})();
