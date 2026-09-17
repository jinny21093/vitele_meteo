"use strict";
/* weather-ui page-events.js — экран «События» (ТЗ weather-ui-spec.md §4.4 + §5.5;
   v1.2.5: окно — селектор 1/7/30/90 дней, по умолчанию 7, выбор в localStorage).
   Таймлайн — чистый DOM (без Chart.js, CSP §0.6): группировка по датам TZ дачи,
   внутри групп — порядок сервера (ts_start DESC). Overlap-семантика окна (§5.5):
   событие, начавшееся раньше окна и открытое/закрывшееся после from, показывается
   с маркером «идёт с более раннего времени»; открытое (ts_end null) — «активно».
   Фильтры: тип — каталог 21 (§5.5), severity — low/mid/high; пустой выбор = без
   фильтра. truncated -> баннер (m-15: ПОСЛЕ banner(null), иначе сотрётся). */

(function () {
  const W = window.WEATHER;
  const el = W.el;                 // m-17: общий хелпер app.js
  const $ = (id) => document.getElementById(id);
  const DAYS_KEY = "weather-events-days";
  const WD = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];   // getUTCDay-порядок
  /* Каталог типов §5.5 (21) — держать синхронным с server.py EVENT_TYPES. */
  const TYPES = ["FROST", "HARD_FREEZE", "FOG", "STORM_APPROACH", "THUNDER_RISK",
    "HEAVY_RAIN", "DOWNPOUR", "STRONG_WIND", "HURRICANE_GUST", "HEATWAVE",
    "DRY_SPELL", "CALM", "RAPID_TEMP_DROP", "RAPID_TEMP_RISE", "PRESSURE_CRASH",
    "RAIN_COUNTER_RESET", "SENSOR_MISSING", "SENSOR_STUCK", "SENSOR_DRIFT",
    "SENSOR_ANOMALY", "BATTERY_LOW"];
  const SEVERITIES = ["low", "mid", "high"];
  const POLL_MS = 60000;           // §4.4: polling 60 с

  let days = parseInt(W.lsGet(DAYS_KEY), 10);   // m-16: guard заблокированного storage
  if (![1, 7, 30, 90].includes(days)) days = 7;
  const typeSel = new Set();       // пустой выбор = без фильтра (§4.4)
  const sevSel = new Set();
  let poller = null;

  /* локальные сутки дачи (паттерн page-month) */
  const TZ = () => W.tzOffset;
  function dayIdx(ts) { return Math.floor((ts + TZ()) / 86400); }
  function dayStartOf(idx) { return idx * 86400 - TZ(); }

  /* --- фильтры: чипы (мультиселект; клик — toggle + перезапрос) --- */
  function chipRow(boxId, label, values, sel) {
    const box = $(boxId);
    box.textContent = "";
    box.appendChild(el("span", "chip-label", label));
    for (const v of values) {
      const c = el("button", "chip", v);
      c.type = "button";
      c.classList.toggle("active", sel.has(v));
      c.addEventListener("click", () => {
        if (sel.has(v)) sel.delete(v); else sel.add(v);
        renderChips();
        if (poller) poller.refreshNow();
      });
      box.appendChild(c);
    }
  }
  function renderChips() {
    chipRow("type-chips", "Типы:", TYPES, typeSel);
    chipRow("sev-chips", "Severity:", SEVERITIES, sevSel);
  }

  /* --- карточка события (клик — разворачивание с context, §4.4) --- */
  function evCard(r, from) {
    const card = el("div", "ev-card");
    const head = el("div", "ev-head");
    const sev = r.severity || "low";
    head.appendChild(el("span", "ev-type ev-sev-" + sev, r.event_type || "—"));
    head.appendChild(el("span", "muted", sev));
    if (r.ts_end === null) head.appendChild(el("span", "ev-open-badge", "активно"));
    card.appendChild(head);
    if (r.ts_start < from) {       // §5.5: окно перекрыто слева
      card.appendChild(el("div", "ev-earlier", "идёт с более раннего времени"));
    }
    const meta = el("div", "ev-meta");
    meta.appendChild(el("span", "", W.fmtTs(r.ts_start)));
    if (r.duration_s !== null && r.duration_s !== undefined) {
      meta.appendChild(el("span", "", Math.round(r.duration_s / 60) + " мин"));
    }
    if (r.value !== null && r.value !== undefined) {
      meta.appendChild(el("span", "", "значение: " + W.num(r.value, 2)));
    }
    card.appendChild(meta);
    const ctx = el("div", "ev-ctx");
    const pre = el("pre", "");
    pre.textContent = (r.context === null || r.context === undefined)
      ? "context: нет данных"
      : JSON.stringify(r.context, null, 2);   // сервер отдаёт объект, не строку
    ctx.appendChild(pre);
    card.appendChild(ctx);
    card.addEventListener("click", () => card.classList.toggle("open"));
    return card;
  }

  /* --- таймлайн: группы по датам TZ дачи, внутри — порядок сервера --- */
  function renderTimeline(d, from) {
    const box = $("timeline");
    box.textContent = "";
    if (!d) {                      // m-11: запрос упал — деградация блока
      box.appendChild(el("span", "muted", "События недоступны"));
      return;
    }
    const rows = d.rows || [];
    if (!rows.length) {
      box.appendChild(el("span", "muted", "Событий за выбранный период нет"));
      return;
    }
    const groups = new Map();      // dayIdx -> rows (порядок сервера сохранён)
    for (const r of rows) {
      const di = dayIdx(r.ts_start);
      if (!groups.has(di)) groups.set(di, []);
      groups.get(di).push(r);
    }
    const idxs = [...groups.keys()].sort((a, b) => b - a);   // свежие даты сверху
    for (const di of idxs) {
      const start = dayStartOf(di);
      const wd = WD[new Date((start + TZ()) * 1000).getUTCDay()];
      const grp = el("div", "tl-group");
      grp.appendChild(el("div", "tl-date", W.fmtDate(start) + " " + wd));
      const list = el("div", "tl-list");
      for (const r of groups.get(di)) list.appendChild(evCard(r, from));
      grp.appendChild(list);
      box.appendChild(grp);
    }
  }

  /* --- один цикл обновления (§4.4: polling 60 с + ручной refresh) --- */
  async function refresh() {
    const nowSec = Math.floor(Date.now() / 1000);
    const from = nowSec - days * 86400;
    let url = "/api/events?from=" + from + "&to=" + nowSec;
    if (typeSel.size) url += "&types=" + [...typeSel].join(",");
    if (sevSel.size) url += "&severity=" + [...sevSel].join(",");
    let d = null;
    try { d = await W.apiFetch(url); }
    catch (e) { /* m-11: баннер уже показан apiFetch; блок деградирует */ }
    try { renderTimeline(d, from); } catch (e) { /* блок продолжает жить */ }
    await W.refreshHeader();       // §4.0: шапка на всех экранах
    const lu = $("last-update");
    if (lu) lu.textContent = "обновлено в " + W.fmtTime(nowSec);
    W.banner(null);                // m-15: сброс ДО truncated-баннера
    if (d && d.truncated) {
      W.banner("показаны не все события, сузьте окно", "warn");
    }
  }

  function setSelector() {
    document.querySelectorAll(".seg[data-days]").forEach((b) => {
      b.classList.toggle("active", Number(b.dataset.days) === days);
    });
  }

  W.ready.then(() => {
    setSelector();
    renderChips();
    document.querySelectorAll(".seg[data-days]").forEach((b) => {
      b.addEventListener("click", () => {
        days = Number(b.dataset.days);
        W.lsSet(DAYS_KEY, String(days));          // m-16: guard storage
        setSelector();
        if (poller) poller.refreshNow();
      });
    });
    poller = W.poll(refresh, POLL_MS);            // §4.4: polling 60 с
    const btn = $("refresh");
    if (btn) btn.addEventListener("click", () => poller.refreshNow());
  });
})();
