"use strict";
/* weather-ui page-settings.js — экран «Настройки» (ТЗ weather-ui-spec.md v1.2.8
   §4.6, U6). Данные — GET /api/settings (wmeta отдаётся УЖЕ отфильтрованным
   сервером по WMETA_VISIBLE — §5.10; на клиенте фильтра нет, §4.6). Polling
   5 мин (§4.6: «по запросу — 5 мин достаточно»).

   U6-C2 экспорт: пробник = лёгкий GET тех же параметров с limit=1 (решение C2:
   HEAD -> 501 по §3, поэтому GET-пробник; сервер применяет limit ПОСЛЕ pre-COUNT,
   т.е. пробник проходит ту же проверку 413). 200 -> window.location на полный
   URL (браузер качает сам); 413 -> баннер с числом строк из тела. TOCTOU-
   оговорка: между пробником и скачиванием размер теоретически может вырасти —
   тогда браузер покажет 413-JSON (редко; повтор кнопки решает).

   U6-C3 «Проверить БД»: confirm -> POST /api/check-db (единственный POST
   системы, §5.11; тело не отправляется — сервер закрывает соединение в любом
   исходе). Fetch здесь СВОЙ, не apiFetch: 429 check-db — это лимит 1/мин
   (не «неудачные логины», как говорит баннер apiFetch), а 503 «check timed
   out» должен попасть в модалку, а не в общий баннер. Результат — в модалке;
   карточка db_health обновляется в рамках сессии (сервер результат НЕ
   кэширует — решение S3: UI не пишет в БД, wmeta db_health — ключ этапа B).

   Старение db_health (§4.6 дословно): updated_at старше 26 ч — жёлтым
   «Проверка БД: не проводилась (N дн назад)»; ключа нет вовсе — жёлтым
   «Проверка БД: не проводилась». */

(function () {
  const W = window.WEATHER;
  const el = W.el;
  const $ = (id) => document.getElementById(id);
  const POLL_MS = 300000;                 // §4.6: 5 мин
  const FRESH_H = 26;                     // §4.6: старение db_health — 26 ч
  const CHECK_TIMEOUT_MS = 70000;         // серверный таймаут 60 с + запас

  /* wmeta-ключи к показу (§5.10 фильтрует сервер; здесь — только подписи
     и порядок; неизвестные ключи сервер не отдаст). */
  const WMETA_LABELS = [
    ["units", "Единицы измерения"],
    ["tz", "Часовой пояс"],
    ["tz_offset_seconds", "Смещение TZ, с"],
    ["gdd_tbase_c", "База GDD, °C"],
    ["ok_total", "Опросов успешно"],
    ["err_total", "Опросов с ошибкой"],
    ["last_ok", "Последний успешный опрос"],
    ["schema_version", "Версия схемы (wmeta)"],
  ];

  let poller = null;
  let sessionCheck = null;   // результат POST check-db в рамках сессии

  /* --- даты формы в эпохе TZ дачи (§6: только через TZ_OFFSET) --- */
  function tz() { return W.tzOffset; }
  function epochOfDate(s, endOfDay) {
    // "YYYY-MM-DD" локальной даты дачи -> UTC-эпоха полуночи (+конец дня)
    const t = Date.parse(s + "T00:00:00Z") / 1000;
    if (!isFinite(t)) return NaN;
    return t - tz() + (endOfDay ? 86400 : 0);
  }
  function dateOfEpoch(sec) { return W.fmtDate(sec); }

  function defaultFrom() {
    const now = Math.floor(Date.now() / 1000);
    return dateOfEpoch(now - 7 * 86400);
  }
  function defaultTo() { return dateOfEpoch(Math.floor(Date.now() / 1000)); }

  /* --- db_health: старение §4.6 --- */
  function renderDbh(d) {
    const box = $("set-dbh");
    box.textContent = "";
    let line, cls = "warn";
    if (sessionCheck) {
      cls = sessionCheck.status === "ok" ? "ok" : "bad";
      line = "Проверка БД: только что выполнена (" + sessionCheck.whenText +
             ") — " + (sessionCheck.status === "ok" ? "ok" : "ошибки");
    } else if (d && typeof d === "object" && Number.isFinite(Number(d.updated_at))) {
      const ageH = (Math.floor(Date.now() / 1000) - Number(d.updated_at)) / 3600;
      const res = d.status || d.result || "результат не указан";
      if (ageH <= FRESH_H) {
        cls = res === "ok" ? "ok" : "warn";
        line = "Проверка БД: " + res + " (" + W.timeAgo(d.updated_at,
          Math.floor(Date.now() / 1000)) + ")";
      } else {
        line = "Проверка БД: не проводилась (" +
          Math.floor(ageH / 24) + " дн назад)";
      }
    } else {
      line = "Проверка БД: не проводилась";
    }
    const b = el("span", "badge " + cls, line);
    box.appendChild(b);
    if (d && typeof d === "object" && d.updated_at !== undefined) {
      box.appendChild(el("span", "muted",
        "  ключ wmeta db_health, updated_at: " + W.fmtTs(d.updated_at)));
    } else if (!sessionCheck) {
      box.appendChild(el("span", "muted",
        "  ключ wmeta db_health пуст — пишет этап B (сегодня не пишет никто; " +
        "кнопка ниже проверяет прямо сейчас)"));
    }
  }

  /* --- wmeta / схема / журнал --- */
  function renderWmeta(wmeta) {
    const box = $("set-wmeta");
    box.textContent = "";
    const wrap = el("div", "kv");
    let shown = 0;
    for (const [k, label] of WMETA_LABELS) {
      if (!(k in wmeta)) continue;
      shown++;
      wrap.appendChild(el("div", "k", label));
      const v = wmeta[k];
      const txt = (k === "last_ok" && isFinite(Number(v)))
        ? W.fmtTs(Number(v)) : String(v);
      wrap.appendChild(el("div", "v", txt));
    }
    if (!shown) {
      box.appendChild(el("span", "muted", "ключей whitelist в wmeta нет"));
      return;
    }
    box.appendChild(wrap);
  }

  function renderMig(mig) {
    const box = $("set-mig");
    box.textContent = "";
    if (!mig) {
      box.appendChild(el("span", "muted", "миграций нет (schema_migrations пуста)"));
      return;
    }
    const wrap = el("div", "kv");
    wrap.appendChild(el("div", "k", "Версия схемы"));
    wrap.appendChild(el("div", "v", String(mig.version)));
    wrap.appendChild(el("div", "k", "Применена"));
    wrap.appendChild(el("div", "v", mig.applied_at ? W.fmtTs(mig.applied_at) : "—"));
    wrap.appendChild(el("div", "k", "Описание"));
    wrap.appendChild(el("div", "v", mig.description || "—"));
    box.appendChild(wrap);
  }

  function renderClog(rows) {
    const body = $("set-clog-body");
    body.textContent = "";
    if (!rows || !rows.length) {
      const tr = el("tr");
      const td = el("td", "muted", "записей нет (collector_log пуст)");
      td.colSpan = 5;
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    for (const r of rows) {
      const tr = el("tr");
      tr.appendChild(el("td", null, W.fmtTs(r.ts)));
      const ok = String(r.status || "").toLowerCase() === "ok";
      tr.appendChild(el("td")).appendChild(
        el("span", "badge " + (ok ? "ok" : "bad"), String(r.status || "—")));
      tr.appendChild(el("td", null, r.latency_ms === null || r.latency_ms === undefined
        ? "—" : String(r.latency_ms)));
      tr.appendChild(el("td", null, r.bytes === null || r.bytes === undefined
        ? "—" : String(r.bytes)));
      const err = String(r.error || "");
      const tdE = el("td", ok ? "muted" : null, err ? (err.length > 80
        ? err.slice(0, 80) + "…" : err) : "—");
      if (err.length > 80) tdE.title = err;
      tr.appendChild(tdE);
      body.appendChild(tr);
    }
  }

  /* --- загрузка данных (§5.10) --- */
  async function loadSettings() {
    const d = await W.apiFetch("/api/settings");
    renderWmeta(d.wmeta || {});
    renderMig(d.schema_migrations);
    renderClog(d.collector_log);
    renderDbh(d.db_health);
  }

  /* --- U6-C2: экспорт (пробник limit=1 -> 413 баннер / полный URL) --- */
  function exportUrl(limitOne) {
    const from = epochOfDate($("set-from").value, false);
    const to = epochOfDate($("set-to").value, true);
    if (!isFinite(from) || !isFinite(to)) {
      W.banner("Укажите даты (с/по)", "warn");
      return null;
    }
    if (from >= to) {
      W.banner("Дата «с» должна быть раньше «по»", "warn");
      return null;
    }
    const type = $("set-type").value;
    const sep = $("set-sep").value;
    const fields = $("set-fields").value.trim();
    let url = "/api/export.csv?from=" + from + "&to=" + to +
      "&type=" + encodeURIComponent(type) +
      "&separator=" + encodeURIComponent(sep);
    if (fields) url += "&fields=" + encodeURIComponent(fields);
    if (limitOne) url += "&limit=1";
    return url;
  }

  async function probeExport() {
    const url = exportUrl(false);
    if (!url) return;
    const probe = url + "&limit=1";
    const btn = $("set-export-btn");
    btn.disabled = true;
    try {
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), 15000);
      let res;
      try {
        res = await fetch(probe, { headers: { "Accept": "application/json" },
                                   signal: ac.signal });
      } finally { clearTimeout(timer); }
      if (res.status === 200) {
        window.location.href = url;          // браузер сам качает файл
        return;
      }
      if (res.status === 413) {
        let rows = "?";
        try { rows = String((await res.json()).rows); } catch (e) { /* тело не JSON */ }
        W.banner("Экспорт слишком велик (413): строк " + rows +
                 " — сузьте окно", "warn", true);
        return;
      }
      if (res.status === 401) { W.banner("Требуется авторизация", "warn"); return; }
      W.banner("Пробник экспорта: HTTP " + res.status, "bad");
    } catch (e) {
      W.banner("Пробник экспорта не отвечает: " + e.message, "bad");
    } finally {
      btn.disabled = false;
    }
  }

  /* --- U6-C3: проверка БД (свой fetch — см. шапку) --- */
  function openModal() {
    $("set-modal-body").textContent = "";
    $("set-modal-body").appendChild(el("span", "muted", "выполняется…"));
    $("set-modal").classList.remove("hidden");
  }
  function fillModal(rows) {
    const body = $("set-modal-body");
    body.textContent = "";
    const ok = rows.status === "ok";
    body.appendChild(el("div", "badge " + (ok ? "ok" : "bad"),
      ok ? "quick_check: ok" : "quick_check: проблемы"));
    body.appendChild(el("p", "muted",
      "Выполнено за " + rows.duration_ms + " мс; ответов: " + rows.row_count +
      (rows.truncated ? " (показаны первые " + rows.rows.length + ")" : "")));
    const list = el("ul");
    for (const r of rows.rows) list.appendChild(el("li", null, String(r)));
    body.appendChild(list);
  }
  function rememberSession(rows) {
    sessionCheck = {
      status: rows.status,
      whenText: new Date((Math.floor(Date.now() / 1000) + tz()) * 1000)
        .toISOString().replace("T", " ").slice(0, 16) + " TZ дачи",
    };
  }

  async function checkDb() {
    if (!window.confirm("Проверка целостности БД (PRAGMA quick_check) может " +
                        "занять несколько секунд. Запустить?")) return;
    openModal();
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), CHECK_TIMEOUT_MS);
    let res;
    try {
      res = await fetch("/api/check-db", { method: "POST", signal: ac.signal });
    } catch (e) {
      W.banner("Проверка БД не отвечает: " +
        (e.name === "AbortError" ? "таймаут клиента" : e.message), "bad");
      $("set-modal-body").textContent = "";
      $("set-modal-body").appendChild(el("span", "muted", "запрос не выполнен"));
      return;
    } finally { clearTimeout(timer); }
    let payload = null;
    try { payload = await res.json(); } catch (e) { /* не JSON */ }
    if (res.ok && payload) {
      fillModal(payload);
      rememberSession(payload);
      renderDbh(null);            // перерисовать карточку с sessionCheck
      return;
    }
    $("set-modal-body").textContent = "";
    if (res.status === 429) {
      const ra = parseInt(res.headers.get("Retry-After"), 10) || 60;
      W.banner("Проверка БД: не чаще 1 раза в минуту — подождите " + ra + " с",
               "warn", true);
      $("set-modal-body").appendChild(el("span", "muted",
        "лимит: 1 проверка в минуту"));
    } else if (res.status === 503 && payload && payload.error === "check timed out") {
      $("set-modal-body").appendChild(el("span", null,
        "Превышен таймаут (60 с) — проверка прервана. Повторите позже; " +
        "повтор возможен не раньше, чем через минуту."));
    } else if (res.status === 503) {
      $("set-modal-body").appendChild(el("span", "muted",
        "БД недоступна (503)"));
    } else {
      $("set-modal-body").appendChild(el("span", "muted",
        "HTTP " + res.status));
    }
  }

  /* --- init --- */
  function init() {
    $("set-from").value = defaultFrom();
    $("set-to").value = defaultTo();
    $("set-export-btn").addEventListener("click", probeExport);
    $("set-check-btn").addEventListener("click", checkDb);
    $("set-modal-close").addEventListener("click",
      () => $("set-modal").classList.add("hidden"));
    $("set-modal").addEventListener("click", (e) => {
      if (e.target === $("set-modal")) $("set-modal").classList.add("hidden");
    });
    $("refresh").addEventListener("click", () => {
      if (poller) poller.refreshNow();
    });
    poller = W.poll(loadSettings, POLL_MS);
    W.refreshHeader();
  }

  W.ready.then(init).catch((e) => {
    W.banner("Инициализация страницы не удалась: " + e.message, "bad");
  });
})();
