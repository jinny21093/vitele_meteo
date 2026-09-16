"use strict";
/* weather-ui app.js — общий клиентский слой (ТЗ weather-ui-spec.md v1.2.2 §6).
   Единственная глобальная точка — window.WEATHER. UI_VERSION — semver КОДА
   (§4.0): major — ломает API-контракт, minor — новая фича/экран, patch — фикс. */

window.WEATHER = (function () {
  const UI_VERSION = "0.2.0";          // U2 «Сутки» + U3 «Месяц» (semver кода, §4.0)
  const TZ_FALLBACK = 10800;           // Europe/Moscow fixed (wmeta.tz_policy)
  const POLL_BACKOFF_MAX_MS = 300000;  // §6: backoff до 5 мин

  let TZ_OFFSET = TZ_FALLBACK;

  /* Батарея: whitelist OK-строк коллектора (M-1, ревью r1-r3): regex-подстрока
     матчит "not all battery are ok" как OK. Источник:
     stage-a/weather_collector.py BATTERY_OK_PATTERNS — держать синхронным. */
  const BATTERY_OK = new Set(["all battery are ok"]);
  function batteryOk(raw) {
    return BATTERY_OK.has(String(raw || "").trim().toLowerCase());
  }

  /* --- время: только хелперы с TZ_OFFSET (§6); toLocaleTimeString без
         явного timeZone запрещён — и не используется вовсе --- */
  function fmtTs(epoch) {
    // m-3: нет значения (null/undefined/мусор) — «—», а не «1970…»/RangeError.
    // (Number(null) === 0 — финитно, поэтому null/undefined отсечены явно)
    if (epoch === null || epoch === undefined) return "—";
    const n = Number(epoch);
    if (!isFinite(n)) return "—";
    const d = new Date((n + TZ_OFFSET) * 1000);
    return d.toISOString().replace("T", " ").slice(0, 16);
  }
  function fmtTime(epoch) { return fmtTs(epoch).slice(11, 16); }
  function fmtDate(epoch) { return fmtTs(epoch).slice(0, 10); }

  function timeAgo(epoch, nowSec) {
    const s = Math.max(0, Math.floor((nowSec - Number(epoch))));
    if (s < 60) return "только что";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " мин назад";
    const h = Math.floor(m / 60);
    if (h < 48) return h + " ч назад";
    return Math.floor(h / 24) + " д назад";
  }

  /* --- баннер (§8) --- */
  function banner(msg, kind, sticky) {
    const el = document.getElementById("banner");
    if (!el) return;
    clearTimeout(banner._t);   // M-2: старый таймер не гасит новый sticky-баннер
    if (!msg) { el.classList.add("hidden"); el.textContent = ""; return; }
    el.textContent = msg;
    el.className = "banner " + (kind || "info");
    el.classList.remove("hidden");
    if (!sticky) {
      clearTimeout(banner._t);
      banner._t = setTimeout(() => el.classList.add("hidden"), 8000);
    }
  }

  /* --- apiFetch (§6): 401 — браузер сам показывает диалог из
         WWW-Authenticate (заголовок обязан прийти от сервера, §3);
         429/5xx — баннер; 503 с «current is empty» — режим «Инициализация…». --- */
  class ApiError extends Error {
    constructor(status, payload) {
      super("API " + status);
      this.status = status;
      this.payload = payload || null;
      this.initMode = status === 503 && !!(payload && payload.error &&
        String(payload.error).indexOf("current is empty") === 0);
      this.retryMs = this.initMode ? 10000 : null;   // §5.1: повтор через 10 с
    }
  }

  async function apiFetch(path, opts) {
    // m-1 (ревью r1-r3): зависший TCP не должен держать экран без фидбека —
    // 15 с на ответ, затем AbortError -> баннер + ApiError(0).
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 15000);
    let res;
    try {
      res = await fetch(path, Object.assign(
        { headers: { "Accept": "application/json" } }, opts || {},
        { signal: ac.signal }));   // сигнал наш — поверх opts (таймаут обязателен)
    } catch (e) {
      if (e && e.name === "AbortError") {
        banner("Сервер не отвечает (таймаут 15 с)", "bad");
        throw new ApiError(0, null);
      }
      banner("Сеть недоступна: " + e.message, "bad");
      throw new ApiError(0, null);
    } finally {
      clearTimeout(timer);
    }
    if (res.ok) return res.json();
    let body = null;
    try { body = await res.json(); } catch (e) { /* не JSON — ладно */ }
    if (res.status === 401) { banner("Требуется авторизация", "warn"); }
    else if (res.status === 429) { banner("Слишком много запросов — пауза 60 с", "warn", true); }
    else if (res.status >= 500 && !new ApiError(res.status, body).initMode) {
      banner("Ошибка API " + res.status + " — повтор через 30 с", "bad");
    }
    throw new ApiError(res.status, body);
  }

  /* --- polling (§6): рекурсивный setTimeout, backoff до 5 мин, пауза при
         document.hidden, мгновенный refresh при visibilitychange --- */
  function poll(fn, intervalMs) {
    let timer = null;
    let stopped = false;
    let busy = false;                 // m-6: in-flight guard
    let delay = intervalMs;
    async function tick() {
      if (stopped || busy) return;    // m-6: параллельный tick пропускается
      busy = true;
      try {
        await fn();
        delay = intervalMs;                       // успех сбрасывает backoff
      } catch (e) {
        delay = (e && e.retryMs) ? e.retryMs
          : Math.min((delay || intervalMs) * 2, POLL_BACKOFF_MAX_MS);
      } finally {
        busy = false;
        schedule();
      }
    }
    function schedule() {
      if (stopped) return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (document.hidden) { schedule(); return; }   // пауза в фоне
        tick();
      }, delay);
    }
    function onVisible() {
      if (!document.hidden && !stopped) {              // мгновенный refresh
        clearTimeout(timer);
        tick();
      }
    }
    document.addEventListener("visibilitychange", onVisible);
    tick();
    return {
      stop() {
        stopped = true;
        clearTimeout(timer);
        document.removeEventListener("visibilitychange", onVisible);   // m-6
      },
      refreshNow() { if (!stopped) { clearTimeout(timer); tick(); } }
    };
  }

  /* --- тема (§6): CSS-переменные + prefers-color-scheme + тумблер --- */
  const THEME_KEY = "weather-theme";
  function applyTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") {
      document.documentElement.dataset.theme = saved;
    } else {
      delete document.documentElement.dataset.theme;   // по системе
    }
  }
  function toggleTheme() {
    const cur = document.documentElement.dataset.theme ||
      (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem(THEME_KEY, next);
  }

  /* --- шапка §4.0: статус свежести и батареи --- */
  function setFreshness(kind, text) { setDot("fresh-dot", "fresh-text", kind, text); }
  function setBattery(kind, text) { setDot("batt-dot", "batt-text", kind, text); }
  function setDot(dotId, textId, kind, text) {
    const d = document.getElementById(dotId);
    const t = document.getElementById(textId);
    if (d) d.className = "dot " + (kind || "");
    if (t) t.textContent = text || "—";
  }

  /* --- цветовые шкалы. Пороги v1.0 в наличии нет — заданы здесь константами,
         правятся в одном месте (§4.1 «цветовые пороги как в v1.0»). --- */
  function colorForTemp(t) {
    if (t === null || t === undefined || isNaN(t)) return null;
    if (t <= -20) return "#7fa8d9";
    if (t <= -10) return "#5b9bd5";
    if (t <= 0) return "#79b8d0";
    if (t <= 10) return "#69a97c";
    if (t <= 20) return "#a3b56b";
    if (t <= 30) return "#dfa53f";
    return "#d95d4a";
  }
  function colorForWind(ms) {
    if (ms === null || ms === undefined || isNaN(ms)) return null;
    if (ms < 1) return "#8b9aab";
    if (ms < 5) return "#69a97c";
    if (ms < 10) return "#dfa53f";
    if (ms < 15) return "#e07b39";
    return "#d95d4a";
  }
  function colorForUvi(uvi) {
    if (uvi === null || uvi === undefined || isNaN(uvi)) return null;
    if (uvi < 3) return "#69a97c";
    if (uvi < 6) return "#dfa53f";
    if (uvi < 8) return "#e07b39";
    if (uvi < 11) return "#d95d4a";
    return "#a06bd9";
  }
  /* стрелка/класс тенденции давления — пороги этапа B (classify_trend ±0.5/±1.5) */
  function pressureTrend(delta) {
    if (delta === null || delta === undefined || isNaN(delta)) {
      return { arrow: "→", cls: "steady", word: "нет данных" };
    }
    if (delta >= 1.5) return { arrow: "↑", cls: "rise", word: "растёт быстро" };
    if (delta >= 0.5) return { arrow: "↗", cls: "rise", word: "растёт" };
    if (delta <= -1.5) return { arrow: "↓", cls: "fall", word: "падает быстро" };
    if (delta <= -0.5) return { arrow: "↘", cls: "fall", word: "падает" };
    return { arrow: "→", cls: "steady", word: "стабильно" };
  }
  /* румб -> стрелка (8 направлений из 16-румбовой строки) */
  const RUMB_ARROW = {
    N: "↑", NNE: "↑", NE: "↗", ENE: "↗", E: "→", ESE: "→", SE: "↘", SSE: "↘",
    S: "↓", SSW: "↓", SW: "↙", WSW: "↙", W: "←", WNW: "←", NW: "↖", NNW: "↖"
  };

  function num(v, digits) {
    if (v === null || v === undefined || isNaN(Number(v))) return "—";
    return Number(v).toFixed(digits === undefined ? 1 : digits);
  }

  /* --- тема Chart.js: цвета из CSS-переменных (живёт с тумблером темы) --- */
  function chartTheme() {
    const css = getComputedStyle(document.documentElement);
    const g = (name, fb) => (css.getPropertyValue(name).trim() || fb);
    return { fg: g("--fg", "#1d2733"), muted: g("--muted", "#6b7a8c"),
             grid: g("--border", "#dbe2ea"), accent: g("--accent", "#2f6db3") };
  }

  /* --- шапка для экранов U2+ (§4.0 — на всех экранах): свежесть + батарея.
         page-now.js заполняет её из своих данных; здесь — самостоятельные
         запросы /api/now + /api/events (24 ч, логика 🔴 BATTERY_LOW — как в
         page-now). Ошибки глотаются: баннер уже показан apiFetch, экран
         продолжает рисовать графики (§8 graceful degradation). --- */
  async function refreshHeader() {
    try {
      const d = await apiFetch("/api/now");
      const gap = d.status ? d.status.gap_s : 1e9;
      const nowS = d.now || Math.floor(Date.now() / 1000);
      let kind = "ok";
      if (gap > 600) kind = "bad"; else if (gap > 120) kind = "warn";
      setFreshness(kind, "обновлено " + timeAgo(d.status.last_poll_ts, nowS));
      const ok = batteryOk((d.current || {}).battery_raw);
      let evs = [];
      try {
        const ev = await apiFetch("/api/events?from=" + (nowS - 86400) + "&to=" + nowS);
        evs = ev.rows || [];
      } catch (e) { evs = []; }
      const lowOpen = evs.some(
        (r) => r.event_type === "BATTERY_LOW" && r.ts_end === null);
      if (lowOpen) setBattery("bad", "батарея: LOW");
      else if (ok) setBattery("ok", "батарея: ok");
      else setBattery("warn", "батарея: ?");
    } catch (e) { /* баннер уже показан apiFetch */ }
  }

  /* --- init: TZ из /api/meta (§6), версия в шапке, тема --- */
  async function init() {
    applyTheme();
    const tt = document.getElementById("theme-toggle");
    if (tt) tt.addEventListener("click", toggleTheme);
    const v = document.getElementById("ui-version");
    if (v) v.textContent = "v" + UI_VERSION;
    try {
      const meta = await apiFetch("/api/meta");
      const off = parseInt(meta && meta.wmeta && meta.wmeta.tz_offset_seconds, 10);
      if (Number.isFinite(off)) TZ_OFFSET = off;
    } catch (e) {
      banner("TZ из /api/meta не получен — по умолчанию UTC+3", "warn");
    }
  }
  const ready = init();

  return {
    UI_VERSION, ready, apiFetch, ApiError, poll, banner,
    fmtTs, fmtTime, fmtDate, timeAgo, num, batteryOk,
    setFreshness, setBattery,
    colorForTemp, colorForWind, colorForUvi, pressureTrend, RUMB_ARROW,
    chartTheme, refreshHeader,
    get tzOffset() { return TZ_OFFSET; }
  };
})();
