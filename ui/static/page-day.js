"use strict";
/* weather-ui page-day.js — экран «Сутки» (ТЗ weather-ui-spec.md §4.2, polling 5 мин).
   /api/history за 24 ч; 5 графиков (T_out+T_in+ноль; P; ветер 3 линии; дождь
   столбики+кумулятив; солнце площадь+UVI) + таблица min/max/avg с временами
   (TZ дачи, хелперы app.js §6). Оси — category, labels из fmtTime.
   Дождь: приращения накопительного счётчика типпера rain_total_mm; сброс
   счётчика (событие RAIN_COUNTER_RESET) даёт отрицательную дельту — клампится
   в 0, поэтому сумма за сутки консервативна (не завышается). */

(function () {
  const W = window.WEATHER;
  const $ = (id) => document.getElementById(id);
  const DAY_S = 86400;
  const charts = {};
  let poller = null;

  const FIELDS = ["ts", "outdoor_temp_c", "indoor_temp_c", "outdoor_hum_pct",
                  "pressure_rel_mmhg", "wind_ms", "gust_ms", "wind_avg10_ms",
                  "rain_total_mm", "light_wm2", "uvi"];

  const STAT_ROWS = [
    ["Улица, °C", "outdoor_temp_c", 1],
    ["Дом, °C", "indoor_temp_c", 1],
    ["Влажность, %", "outdoor_hum_pct", 0],
    ["Давление, мм рт. ст.", "pressure_rel_mmhg", 1],
    ["Ветер, м/с", "wind_ms", 1],
    ["Порыв, м/с", "gust_ms", 1],
    ["UVI", "uvi", 1],
    ["Свет, Вт/м²", "light_wm2", 0]
  ];

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function series(rows, fi, name) {
    const i = fi[name];
    return rows.map((r) => {
      const v = r[i];
      return (v === null || v === undefined || isNaN(Number(v))) ? null : Number(v);
    });
  }

  function mkLine(data, color, extra) {
    return Object.assign({ data: data, borderColor: color, borderWidth: 1.5,
                           pointRadius: 0, tension: .2, spanGaps: true },
                         extra || {});
  }

  function lineOpts(theme) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: true,
                           labels: { color: theme.muted, boxWidth: 10,
                                     font: { size: 10 } } } },
      scales: {
        x: { ticks: { maxTicksLimit: 9, autoSkip: true, color: theme.muted,
                      font: { size: 10 } },
             grid: { display: false } },
        y: { ticks: { color: theme.muted, font: { size: 10 } },
             grid: { color: theme.grid } }
      }
    };
  }

  function destroy(name) {
    if (charts[name]) { charts[name].destroy(); charts[name] = null; }
  }

  /* --- дождь: приращения счётчика по корзинам 15 мин --- */
  function rainBuckets(rows, fi) {
    const STEP = 900;
    const buckets = new Map();
    let prev = null;
    for (const r of rows) {
      const ts = r[fi.ts];
      const raw = r[fi.rain_total_mm];
      const cur = (raw === null || raw === undefined || isNaN(Number(raw)))
        ? null : Number(raw);
      if (prev !== null && cur !== null) {
        const d = Math.max(0, cur - prev);       // сброс счётчика -> 0
        const k = Math.floor(ts / STEP) * STEP;
        buckets.set(k, (buckets.get(k) || 0) + d);
      }
      if (cur !== null) prev = cur;
    }
    return buckets;
  }

  function renderRain(rows, fi, theme) {
    const buckets = rainBuckets(rows, fi);
    const keys = [...buckets.keys()].sort((a, b) => a - b);
    const labels = keys.map((k) => W.fmtTime(k));
    const bars = keys.map((k) => Number(buckets.get(k).toFixed(3)));
    let acc = 0;
    const cum = keys.map((k) => Number((acc += buckets.get(k)).toFixed(2)));
    destroy("rain");
    charts.rain = new Chart($("chart-rain"), {
      type: "bar",
      data: { labels: labels, datasets: [
        { type: "bar", label: "мм / 15 мин", data: bars,
          backgroundColor: "rgba(91,155,213,.75)", borderWidth: 0 },
        { type: "line", label: "кумулятив, мм", data: cum, yAxisID: "y1",
          borderColor: "#dfa53f", borderWidth: 2, pointRadius: 0, tension: .2 }
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: true,
                             labels: { color: theme.muted, boxWidth: 10,
                                       font: { size: 10 } } } },
        scales: {
          x: { ticks: { maxTicksLimit: 9, autoSkip: true, color: theme.muted,
                        font: { size: 10 } }, grid: { display: false } },
          y: { beginAtZero: true,
               ticks: { color: theme.muted, font: { size: 10 } },
               grid: { color: theme.grid } },
          y1: { beginAtZero: true, position: "right",
                ticks: { color: theme.muted, font: { size: 10 } },
                grid: { display: false } }
        }
      }
    });
    // rain-total вынесен в setRainTotal (m-11): не зависит от Chart.js
  }

  /* m-11 (ревью r1-r3): сводка осадков — без Chart.js (чистая математика
     по rainBuckets), чтобы жила при незагруженном vendor и при сбое графиков. */
  function setRainTotal(rows, fi) {
    const buckets = rainBuckets(rows, fi);
    let acc = 0;
    for (const v of buckets.values()) acc += v;
    const hasRain = rows.some((r) => r[fi.rain_total_mm] !== null &&
                                    r[fi.rain_total_mm] !== undefined);
    const rt = $("rain-total");
    if (rt) rt.textContent = "Осадки за 24 ч: " + (hasRain ? acc.toFixed(1) + " мм" : "—");
  }

  function renderCharts(rows, fi) {
    const theme = W.chartTheme();
    const labels = rows.map((r) => W.fmtTime(r[fi.ts]));

    destroy("t");
    charts.t = new Chart($("chart-t"), { type: "line",
      data: { labels: labels, datasets: [
        mkLine(series(rows, fi, "outdoor_temp_c"), theme.accent, { label: "Улица" }),
        mkLine(series(rows, fi, "indoor_temp_c"), "#d97b39", { label: "Дом" }),
        mkLine(labels.map(() => 0), theme.muted,
               { label: "0", borderDash: [4, 4], borderWidth: 1 })
      ] },
      options: lineOpts(theme) });

    destroy("p");
    charts.p = new Chart($("chart-p"), { type: "line",
      data: { labels: labels, datasets: [
        mkLine(series(rows, fi, "pressure_rel_mmhg"), "#dfa53f", { label: "P rel" })
      ] },
      options: lineOpts(theme) });

    destroy("wind");
    charts.wind = new Chart($("chart-wind"), { type: "line",
      data: { labels: labels, datasets: [
        mkLine(series(rows, fi, "wind_ms"), theme.accent, { label: "Ветер" }),
        mkLine(series(rows, fi, "gust_ms"), "#d95d4a", { label: "Порыв" }),
        mkLine(series(rows, fi, "wind_avg10_ms"), "#69a97c", { label: "avg10" })
      ] },
      options: lineOpts(theme) });

    renderRain(rows, fi, theme);

    destroy("sun");
    charts.sun = new Chart($("chart-sun"), { type: "line",
      data: { labels: labels, datasets: [
        mkLine(series(rows, fi, "light_wm2"), theme.accent,
               { label: "Свет, Вт/м²", fill: true,
                 backgroundColor: "rgba(47,109,179,.15)" }),
        mkLine(series(rows, fi, "uvi"), "#a06bd9",
               { label: "UVI", yAxisID: "y1" })
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: true,
                             labels: { color: theme.muted, boxWidth: 10,
                                       font: { size: 10 } } } },
        scales: {
          x: { ticks: { maxTicksLimit: 9, autoSkip: true, color: theme.muted,
                        font: { size: 10 } }, grid: { display: false } },
          y: { beginAtZero: true,
               ticks: { color: theme.muted, font: { size: 10 } },
               grid: { color: theme.grid } },
          y1: { beginAtZero: true, position: "right",
                ticks: { color: theme.muted, font: { size: 10 } },
                grid: { display: false } }
        }
      } });
  }

  function stats(rows, fi, name) {
    const i = fi[name];
    let s = null;
    for (const r of rows) {
      const v = r[i];
      if (v === null || v === undefined || isNaN(Number(v))) continue;
      const n = Number(v);
      if (!s) s = { min: n, max: n, minTs: r[fi.ts], maxTs: r[fi.ts], sum: 0, n: 0 };
      if (n < s.min) { s.min = n; s.minTs = r[fi.ts]; }
      if (n > s.max) { s.max = n; s.maxTs = r[fi.ts]; }
      s.sum += n;
      s.n += 1;
    }
    return s;
  }

  function renderTable(rows, fi) {
    const tb = $("stat-body");
    tb.textContent = "";
    for (const [label, field, dig] of STAT_ROWS) {
      const s = stats(rows, fi, field);
      const tr = el("tr");
      tr.appendChild(el("td", "", label));
      if (!s) {
        const td = el("td", "muted", "—");
        td.colSpan = 5;
        tr.appendChild(td);
      } else {
        tr.appendChild(el("td", "", s.min.toFixed(dig)));
        tr.appendChild(el("td", "muted", W.fmtTime(s.minTs)));
        tr.appendChild(el("td", "", s.max.toFixed(dig)));
        tr.appendChild(el("td", "muted", W.fmtTime(s.maxTs)));
        tr.appendChild(el("td", "", (s.sum / s.n).toFixed(dig)));
      }
      tb.appendChild(tr);
    }
  }

  /* m-11 (ревью r1-r3): графики не должны ронять таблицу/осадки (§8).
     Без Chart.js (vendor не загрузился) и при исключении отрисовки —
     таблица (renderTable) и rain-total (setRainTotal) живут. */
  function renderChartsSafe(rows, fi) {
    if (typeof Chart === "undefined") return;
    try {
      renderCharts(rows, fi);
    } catch (e) { /* сбой отрисовки — остальное уже отрисовано */ }
  }

  async function refresh() {
    const nowSec = Math.floor(Date.now() / 1000);
    const q = "from=" + (nowSec - DAY_S) + "&to=" + nowSec +
              "&fields=" + FIELDS.join(",");
    const h = await W.apiFetch("/api/history?" + q);
    const fi = {};
    (h.fields || []).forEach((f, i) => { fi[f] = i; });
    const rows = h.rows || [];
    if (!rows.length) {
      const tb = $("stat-body");
      tb.textContent = "";
      const tr = el("tr");
      const td = el("td", "muted", "нет данных за окно");
      td.colSpan = 6;
      tr.appendChild(td);
      tb.appendChild(tr);
      $("rain-total").textContent = "Осадки за 24 ч: —";
    } else {
      renderTable(rows, fi);                     // m-11: от Chart.js не зависит
      setRainTotal(rows, fi);                    // m-11: от Chart.js не зависит
      renderChartsSafe(rows, fi);                // m-11: guard + try/catch
    }
    await W.refreshHeader();                     // §4.0: шапка на всех экранах
    const lu = $("last-update");
    if (lu) lu.textContent = "обновлено в " + W.fmtTime(nowSec);
    W.banner(null);
  }

  W.ready.then(() => {
    poller = W.poll(refresh, 5 * 60 * 1000);     // §4.2: polling 5 мин
    const btn = $("refresh");
    if (btn) btn.addEventListener("click", () => poller.refreshNow());
  });
})();
