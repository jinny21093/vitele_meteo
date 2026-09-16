"use strict";
/* weather-ui page-month.js — экран «Месяц» (ТЗ weather-ui-spec.md §4.3, polling 1 ч).
   Heatmap дни×часы из /api/hourly (t_out_avg), календарь осадков из /api/daily,
   тренд min/max/avg по суткам; селектор 7/30/90 дней (localStorage).
   Heatmap и календарь — HTML-сетки с CSS-классами (CSP §0.6: без inline-стилей),
   Chart.js — только тренд. Все даты/оси — TZ дачи (wmeta.tz_offset_seconds).
   day_epoch/hour_epoch этапа B выровнены по полуночи/началу часа MSK. */

(function () {
  const W = window.WEATHER;
  const $ = (id) => document.getElementById(id);
  const DAYS_KEY = "weather-month-days";
  const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
                  "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
  let days = parseInt(W.lsGet(DAYS_KEY), 10);   // m-16: guard заблокированного storage
  if (![7, 30, 90].includes(days)) days = 30;
  let trendChart = null;
  let poller = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  /* локальные сутки дачи: номер дня и час из epoch */
  const TZ = () => W.tzOffset;
  function dayIdx(ts) { return Math.floor((ts + TZ()) / 86400); }
  function hourOf(ts) { return Math.floor(((ts + TZ()) % 86400) / 3600); }
  function dayStartOf(idx) { return idx * 86400 - TZ(); }
  function dm(epoch) {                       // "ДД.ММ" в TZ дачи
    const s = W.fmtDate(epoch);
    return s.slice(8, 10) + "." + s.slice(5, 7);
  }

  /* --- heatmap: класс по порогам (палитра = colorForTemp app.js) --- */
  function hmClass(t) {
    if (t === undefined || t === null || isNaN(t)) return "hm-na";
    if (t <= -20) return "hm-1";
    if (t <= -10) return "hm-2";
    if (t <= 0) return "hm-3";
    if (t <= 10) return "hm-4";
    if (t <= 20) return "hm-5";
    if (t <= 30) return "hm-6";
    return "hm-7";
  }

  function renderLegend() {
    const lg = $("hm-legend");
    lg.textContent = "";
    const items = [["hm-1", "≤ −20"], ["hm-2", "−20…−10"], ["hm-3", "−10…0"],
                   ["hm-4", "0…+10"], ["hm-5", "+10…+20"], ["hm-6", "+20…+30"],
                   ["hm-7", "> +30"], ["hm-na", "нет данных"]];
    for (const [cls, name] of items) {
      const s = el("span", "hm-legend-item");
      s.appendChild(el("span", "sw " + cls));
      s.appendChild(document.createTextNode(name));
      lg.appendChild(s);
    }
  }

  function renderHeatmap(h) {
    const grid = $("hm-grid");
    grid.textContent = "";
    const rows = (h && h.rows) || [];      // m-11: h может быть null (сбой запроса)
    $("hm-note").classList.toggle("hidden", rows.length > 0);
    if (!rows.length) return;
    const fi = {};
    (h.fields || []).forEach((f, i) => { fi[f] = i; });
    const cells = new Map();               // dayIdx -> Array(24)
    for (const r of rows) {
      const he = r[fi.hour_epoch];
      if (he === null || he === undefined) continue;
      const d = dayIdx(he);
      if (!cells.has(d)) cells.set(d, new Array(24).fill(undefined));
      const t = r[fi.t_out_avg];
      cells.get(d)[hourOf(he)] =
        (t === null || t === undefined) ? undefined : Number(t);
    }
    const idxs = [...cells.keys()].sort((a, b) => a - b);
    grid.appendChild(el("div", "hm-label", ""));        // угловая ячейка
    for (let hh = 0; hh < 24; hh++) {
      grid.appendChild(el("div", "hm-hour",
        hh % 3 === 0 ? String(hh).padStart(2, "0") : ""));
    }
    for (const d of idxs) {
      const start = dayStartOf(d);
      const ud = new Date((start + TZ()) * 1000).getUTCDay();
      grid.appendChild(el("div", "hm-label", dm(start) + " " + WD[(ud + 6) % 7]));
      const arr = cells.get(d);
      for (let hh = 0; hh < 24; hh++) {
        const t = arr[hh];
        const c = el("div", "hm-cell " + hmClass(t));
        c.title = dm(start) + " " + String(hh).padStart(2, "0") + ":00 — " +
          (t === undefined ? "нет данных" : t.toFixed(1) + " °C");
        grid.appendChild(c);
      }
    }
  }

  /* --- календарь осадков: месяц(ы) окна, класс по интенсивности --- */
  function rainClass(mm) {
    if (mm === null || mm === undefined || isNaN(mm) || mm <= 0) return "";
    if (mm < 1) return "cal-rain1";
    if (mm < 5) return "cal-rain2";
    if (mm < 15) return "cal-rain3";
    return "cal-rain4";
  }

  function renderCalendar(daily, nowSec) {
    const box = $("cal-box");
    box.textContent = "";
    if (!daily) {                          // m-11: запрос упал — блок деградирует
      box.appendChild(el("span", "muted", "Календарь недоступен"));
      return;
    }
    const fi = {};
    (daily.fields || []).forEach((f, i) => { fi[f] = i; });
    const byMonth = new Map();             // y*12+m -> Map(dayOfMonth -> row)
    for (const r of (daily.rows || [])) {
      const de = r[fi.day_epoch];
      if (de === null || de === undefined) continue;
      const dt = new Date((de + TZ()) * 1000);
      const key = dt.getUTCFullYear() * 12 + dt.getUTCMonth();
      if (!byMonth.has(key)) byMonth.set(key, new Map());
      byMonth.get(key).set(dt.getUTCDate(), r);
    }
    if (!byMonth.size) {
      box.appendChild(el("span", "muted", "Данных пока нет"));
      return;
    }
    const todayStart = dayIdx(nowSec) * 86400 - TZ();
    for (const key of [...byMonth.keys()].sort((a, b) => a - b)) {
      const y = Math.floor(key / 12);
      const m = key % 12;
      const wrap = el("div", "cal-month");
      wrap.appendChild(el("div", "cal-title", MONTHS[m] + " " + y));
      const grid = el("div", "cal-grid");
      for (const w of WD) grid.appendChild(el("div", "cal-wd", w));
      const lead = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7;
      const nDays = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
      for (let i = 0; i < lead; i++) grid.appendChild(el("div", "cal-blank"));
      const mrows = byMonth.get(key);
      for (let dnum = 1; dnum <= nDays; dnum++) {
        const r = mrows.get(dnum);
        const cell = el("div", "cal-day");
        cell.appendChild(el("span", "d", String(dnum)));
        if (r) {
          const raw = r[fi.rain_mm];
          const mm = (raw === null || raw === undefined) ? null : Number(raw);
          if (mm !== null && mm > 0) {
            cell.classList.add(rainClass(mm));
            cell.appendChild(el("div", "r", mm.toFixed(1) + " мм"));
          }
          cell.title = W.fmtDate(r[fi.day_epoch]) + ": осадки " +
            (mm === null ? "—" : mm.toFixed(1)) + " мм";
          if (r[fi.day_epoch] === todayStart) cell.classList.add("cal-today");
        }
        grid.appendChild(cell);
      }
      wrap.appendChild(grid);
      box.appendChild(wrap);
    }
  }

  /* --- тренд min/avg/max по суткам (Chart.js) --- */
  function renderTrend(daily) {
    if (typeof Chart === "undefined" || !daily) return;    // m-11: null-safe
    const rows = daily.rows || [];
    const fi = {};
    (daily.fields || []).forEach((f, i) => { fi[f] = i; });
    const labels = rows.map((r) => dm(r[fi.day_epoch]));
    const ds = (name, color, label) => ({
      label: label,
      data: rows.map((r) => {
        const v = r[fi[name]];
        return (v === null || v === undefined || isNaN(Number(v))) ? null : Number(v);
      }),
      borderColor: color, borderWidth: 1.5, pointRadius: 2, tension: .2,
      spanGaps: true
    });
    const theme = W.chartTheme();
    if (trendChart) trendChart.destroy();
    trendChart = new Chart($("chart-trend"), { type: "line",
      data: { labels: labels, datasets: [
        ds("t_out_min", "#5b9bd5", "мин"),
        ds("t_out_avg", "#dfa53f", "средн"),
        ds("t_out_max", "#d95d4a", "макс")
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: true,
                             labels: { color: theme.muted, boxWidth: 10,
                                       font: { size: 10 } } } },
        scales: {
          x: { ticks: { maxTicksLimit: 10, autoSkip: true, color: theme.muted,
                        font: { size: 10 } }, grid: { display: false } },
          y: { ticks: { color: theme.muted, font: { size: 10 } },
               grid: { color: theme.grid } }
        }
      } });
  }

  function setSelector() {
    document.querySelectorAll(".seg[data-days]").forEach((b) => {
      b.classList.toggle("active", Number(b.dataset.days) === days);
    });
  }

  async function refresh() {
    const nowSec = Math.floor(Date.now() / 1000);
    const from = dayIdx(nowSec) * 86400 - TZ() - (days - 1) * 86400;
    /* m-11 (ревью r1-r3, §8): независимая деградация — сбой /api/hourly
       не гасит календарь/тренд из /api/daily и наоборот; сбой одного блока
       не гасит остальные и шапку. Баннер уже показан apiFetch. */
    let h = null, d = null;
    try { h = await W.apiFetch("/api/hourly?from=" + from + "&to=" + nowSec); }
    catch (e) { /* heatmap деградирует в «нет данных» */ }
    try { d = await W.apiFetch("/api/daily?from=" + from + "&to=" + nowSec); }
    catch (e) { /* календарь/тренд деградируют */ }
    try { renderHeatmap(h); } catch (e) { /* блок продолжает жить */ }
    try { renderCalendar(d, nowSec); } catch (e) { /* блок продолжает жить */ }
    try { renderTrend(d); } catch (e) { /* блок продолжает жить */ }
    await W.refreshHeader();                     // §4.0: шапка на всех экранах
    const lu = $("last-update");
    if (lu) lu.textContent = "обновлено в " + W.fmtTime(nowSec);
    W.banner(null);
  }

  W.ready.then(() => {
    renderLegend();
    setSelector();
    document.querySelectorAll(".seg[data-days]").forEach((b) => {
      b.addEventListener("click", () => {
        days = Number(b.dataset.days);
        W.lsSet(DAYS_KEY, String(days));          // m-16: guard storage
        setSelector();
        if (poller) poller.refreshNow();
      });
    });
    poller = W.poll(refresh, 3600000);           // §4.3: polling 1 ч
    const btn = $("refresh");
    if (btn) btn.addEventListener("click", () => poller.refreshNow());
  });
})();
