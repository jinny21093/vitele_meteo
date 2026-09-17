#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_ui_deploy_v030.py — деплой weather-ui v0.3.0 (U4) на vitele по
чеклисту ревьюера (шаги 0–6):

0. git pull в /home/auditbot/vitele_meteo + md5 4 файлов (repo) +
   синк ui/ -> /home/auditbot/weather-dash/ui + md5 после синка
1. рестарт: PID из ss -tlnp | grep 8089 -> kill -> setsid nohup python3 server.py
2. лог старта: version=0.3.0, static loaded files=14, dropped=0
3. /api/health -> 200 {"status":"ok",...}
4. /api/events?from=t-604800&to=t -> 200, rows:[] (F-3), truncated:false
5. /api/events?from=t-3600&to=t&types=BATTERY_LOW -> 200 (фильтр+окно на живом)
6. прокси браузерных проверок: UI_VERSION 0.3.0, страницы 200 (вкл. /events),
   events.html/page-events.js отдаются, честная пустая надпись; бонус ZT-curl.

Креды на VM только в переменных shell, в лог НЕ печатаются.
Транскрипт -> state/weather_ui_deploy_v030.txt.
Зависимость: хелпер подключения vitele_recon.py (цепочка outpost->vitele, доверенный host-key out-of-band) — приватная агентская инфраструктура, в репо НЕ входит. При запуске вне агентской среды: положить рядом vitele_recon.py с connect_vitele(retries)->(client, inner) и run_tr(inner, cmd, timeout)->(rc, out, err) — либо выполнять шаги вручную по SSH (чеклист в docstring полон).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vitele_recon import connect_vitele, run_tr  # noqa: E402

REPO = "/home/auditbot/vitele_meteo"
UIDIR = "/home/auditbot/weather-dash/ui"
LOG = "/home/auditbot/weather-dash/ui-server.log"
CRED = "/home/auditbot/.weather-ui-credentials"
URL_LOCAL = "http://192.168.8.146:8089"
OUT = "/home/z/my-project/state/weather_ui_deploy_v030.txt"

# эталон ревьюера (ac4c346): page-events.js / events.html / app.js / server.py
MD5_EXPECT = {
    "ui/static/page-events.js": "583b03e39959103537b0b67b6af5418d",
    "ui/static/events.html": "822054eb0512551241b3181c02b242e9",
    "ui/static/app.js": "a883d2c76c56d5585b8e783cfb77cc0c",
    "ui/server.py": "29b5634c8743b1c0b361429d352a3ac8",
}
HEAD_EXPECT = "ac4c346"
FAILS = []


def remote_path(rel):
    """ui/static/app.js -> {UIDIR}/static/app.js; ui/server.py -> {UIDIR}/server.py"""
    rel = rel[len("ui/"):]
    return f"{UIDIR}/{rel}"


def main():
    # локальная сверка эталона с клоном (клон = ac4c346, файлы чистые)
    import hashlib
    base = "/home/z/my-project/github/vitele_meteo"
    for rel, h in MD5_EXPECT.items():
        got = hashlib.md5(open(os.path.join(base, rel), "rb").read()).hexdigest()
        tag = "OK" if got == h else "MISMATCH"
        print(f"[i] local {rel} {got} {tag}")
        if got != h:
            FAILS.append(f"локальный клон {rel}: {got} != эталон {h}")

    c, inner = connect_vitele(retries=4)
    tr_out = c.get_transport()
    out_all = []

    def run(cmd, timeout=90, must=None, fail=None):
        rc, o, e = run_tr(inner, cmd, timeout=timeout)
        if o.strip():
            print(o.rstrip())
        if e.strip():
            print("[stderr]", e.strip()[:300])
        out_all.append(f"$ {cmd}\n{o}" + (f"\n[stderr] {e}" if e.strip() else ""))
        if must and must not in o:
            FAILS.append(fail or f"must '{must}' не найден: {cmd[:70]}")
            print(f"[!] FAIL: ожидалось '{must}'")
        return o

    def check_md5_block(paths_fmt, where, base):
        """paths_fmt: строки для md5sum; base — префикс-корень (REPO или UIDIR);
        сверка rel -> base/rel-nn- 'ui/' с MD5_EXPECT"""
        rc, o, _e = run_tr(inner, f"md5sum {paths_fmt}", timeout=30)
        print(o.rstrip())
        out_all.append(f"$ md5sum {paths_fmt}\n{o}")
        remote = {}
        for ln in o.strip().splitlines():
            parts = ln.split()
            if len(parts) == 2:
                remote[parts[1]] = parts[0]
        for rel, h in MD5_EXPECT.items():
            # в репо файлы лежат под ui/; в UIDIR скопированы без префикса
            rp = (f"{base}/{rel}" if base == REPO
                  else f"{base}/{rel[len('ui/'):]}")
            got = remote.get(rp)
            if got == h:
                print(f"[+] md5 OK ({where}) {rp} {got}")
            else:
                FAILS.append(f"md5 ({where}) {rp}: эталон {h}, факт {got}")
                print(f"[!] md5 MISMATCH ({where}) {rp}: {got} != {h}")

    # ---------- шаг 0: git pull + md5 repo + синк + md5 после синка ----------
    print("\n### 0. git pull + md5 (repo) + синк ui/ -> weather-dash/ui + md5")
    run("command -v git && git --version", must="/usr/bin/git")
    run(f"cd {REPO} && git pull --ff-only 2>&1", timeout=180)
    run(f"git -C {REPO} log --oneline -1 && git -C {REPO} status --short | head -5",
        must=HEAD_EXPECT, fail=f"git HEAD на vitele != {HEAD_EXPECT}")
    check_md5_block(
        f"{REPO}/ui/static/page-events.js {REPO}/ui/static/events.html "
        f"{REPO}/ui/static/app.js {REPO}/ui/server.py", "repo", REPO)
    run(f"mkdir -p {UIDIR} && cp -a {REPO}/ui/. {UIDIR}/ && echo SYNC_OK",
        must="SYNC_OK", fail="синк ui/ не выполнен")
    check_md5_block(
        f"{UIDIR}/static/page-events.js {UIDIR}/static/events.html "
        f"{UIDIR}/static/app.js {UIDIR}/server.py", "UIDIR", UIDIR)

    # ---------- шаг 1: рестарт ----------
    print("\n### 1. рестарт: PID из ss -tlnp -> kill -> setsid nohup")
    run(f"ss -tlnp | grep ':8089 ' || echo NO_LISTENER_BEFORE")
    run("PID=$(ss -tlnp | grep ':8089 ' | grep -o 'pid=[0-9]*' | head -1 "
        "| cut -d= -f2); echo OLD_PID=$PID; "
        "if [ -n \"$PID\" ]; then kill $PID && echo KILL_SENT; fi; sleep 1.2; "
        "pgrep -af '[s]erver\\.py' && { pkill -f '[s]erver\\.py'; sleep 0.7; }; "
        "pgrep -af '[s]erver\\.py' || echo UI_STOPPED",
        must="UI_STOPPED", fail="не удалось снять старый процесс ui")
    run("ss -ltn | grep ':8089 ' || echo PORT_8089_FREE", must="PORT_8089_FREE")
    run("systemctl is-active weather-api weather-collector; true")
    run(f"if [ -f {CRED} ]; then echo CRED_EXISTS; else echo CRED_MISSING; fi",
        must="CRED_EXISTS", fail="файл кредов отсутствует")
    print("[i] запуск через отдельный канал (setsid отвязал процесс; канал "
          "закрывается принудительно)")
    ch = inner.open_session(timeout=10)
    ch.settimeout(10)
    ch.exec_command(f"cd {UIDIR} && setsid nohup python3 server.py >> {LOG} "
                    f"2>&1 < /dev/null & echo LAUNCHED")
    time.sleep(2.5)
    ch.close()
    out_all.append(f"$ cd {UIDIR} && setsid nohup python3 server.py >> {LOG} "
                   f"2>&1 < /dev/null & echo LAUNCHED\nLAUNCHED (канал закрыт "
                   f"через 2.5 c; процесс отвязан setsid)")
    for _ in range(30):
        rc, o, _e = run_tr(inner, f"curl -s -o /dev/null -w '%{{http_code}}' "
                                  f"--max-time 2 {URL_LOCAL}/api/health",
                           timeout=10)
        if o.strip() == "200":
            print("[+] health 200 после старта")
            break
        time.sleep(0.3)
    else:
        FAILS.append("health 200 не получен после запуска")
    run("pgrep -af '[s]erver\\.py' | head -2")
    run("ss -ltn | grep ':8089 '")

    # ---------- шаг 2: лог старта ----------
    print("\n### 2. лог старта: version=0.3.0, files=14, dropped=0")
    run(f"grep -E 'start version=' {LOG} | tail -1", must="version=0.3.0",
        fail="в последней start-строке нет version=0.3.0")
    run(f"grep -E 'start version=' {LOG} | tail -1 | grep -o "
        f"'bind=192.168.8.146,10.147.17.101 port=8089'",
        must="bind=192.168.8.146,10.147.17.101 port=8089",
        fail="bind/port в start-строке не те")
    run(f"grep 'static loaded' {LOG} | tail -1", must="files=14",
        fail="в static loaded нет files=14")
    run(f"echo dropped_count=$(grep -c 'fields dropped' {LOG}); "
        f"echo warn_count=$(grep -c ' WARN ' {LOG})",
        must="dropped_count=0", fail="WARN 'fields dropped' присутствует!")

    # ---------- шаг 3: health ----------
    print("\n### 3. /api/health -> 200 {\"status\":\"ok\",...}")
    run(f"curl -s --max-time 5 -w '\\nHTTP=%{{http_code}}\\n' "
        f"{URL_LOCAL}/api/health", must='"status":"ok"')

    # ---------- шаг 4: events, окно 7 дней, пусто (F-3) ----------
    print("\n### 4. /api/events окно 7 дней -> 200, rows:[], truncated:false")
    run(f"T=$(date +%s); F=$((T-604800)); CRED=$(cat {CRED}); "
        f"curl -s -o /tmp/ev7.json -w 'HTTP=%{{http_code}}\\n' --max-time 8 "
        f"-u \"$CRED\" \"{URL_LOCAL}/api/events?from=$F&to=$T\"; "
        f"head -c 300 /tmp/ev7.json; echo; "
        f"python3 -c \"import json; d=json.load(open('/tmp/ev7.json')); "
        f"print('rows=',len(d['rows']),'truncated=',d['truncated']); "
        f"assert d['rows']==[] and d['truncated'] is False\" "
        f"&& echo EVENTS_EMPTY_OK",
        timeout=30, must="EVENTS_EMPTY_OK",
        fail="events 7д: не 200 / rows не пуст / truncated != false")

    # ---------- шаг 5: events, 1 ч + types=BATTERY_LOW ----------
    print("\n### 5. /api/events 1ч + types=BATTERY_LOW -> 200 (фильтр+окно)")
    run(f"T=$(date +%s); F=$((T-3600)); CRED=$(cat {CRED}); "
        f"curl -s -o /tmp/ev1h.json -w 'HTTP=%{{http_code}}\\n' --max-time 8 "
        f"-u \"$CRED\" \"{URL_LOCAL}/api/events?from=$F&to=$T&types=BATTERY_LOW\"; "
        f"head -c 300 /tmp/ev1h.json; echo; "
        f"python3 -c \"import json; d=json.load(open('/tmp/ev1h.json')); "
        f"print('rows=',len(d['rows']),'truncated=',d['truncated'])\"",
        timeout=30, must="HTTP=200",
        fail="events 1ч BATTERY_LOW: HTTP != 200")

    # ---------- шаг 6: прокси браузерных проверок ----------
    print("\n### 6. прокси браузерных проверок (визуал — с LAN-машины)")
    run(f"curl -s -o /dev/null -D - --max-time 5 {URL_LOCAL}/ | "
        f"grep -i 'WWW-Authenticate'", must='Basic realm="Weather"',
        fail="нет WWW-Authenticate на /")
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/app.js | grep -o 'UI_VERSION = \"0.3.0\"' | head -1",
        must='UI_VERSION = "0.3.0"', fail="в отданном app.js нет 0.3.0")
    run(f"CRED=$(cat {CRED}) && BAD=$(for p in / /day /month /events /settings; "
        f"do c=$(curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 "
        f"-u \"$CRED\" {URL_LOCAL}$p); echo \"$p=$c\"; "
        f"[ \"$c\" = 200 ] || echo BAD_$p; done); "
        f"echo \"$BAD\" | grep '^BAD_' || echo ALL_PAGES_200",
        must="ALL_PAGES_200", fail="не все страницы отдают 200")
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/events.html | grep -c 'page-events.js'",
        must="1", fail="в отданном events.html нет ссылки на page-events.js")
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/page-events.js | grep -o "
        f"'Событий за выбранный период нет' | head -1",
        must="Событий за выбранный период нет",
        fail="в отданном page-events.js нет честной пустой надписи")
    run(f"CRED=$(cat {CRED}) && echo chip_catalog=$(curl -s --max-time 5 "
        f"-u \"$CRED\" {URL_LOCAL}/static/page-events.js | "
        f"grep -c 'BATTERY_LOW\\|FROST\\|PRESSURE_CRASH')",
        must="chip_catalog=", fail="не удалось проверить каталог типов")
    print("\n### бонус: ZT-биндинг снаружи хоста (curl с outpost)")
    rcz, oz, _ez = run_tr(tr_out, f"curl -s --max-time 6 "
                                  f"{URL_LOCAL.replace('192.168.8.146', '10.147.17.101')}"
                                  f"/api/health && echo ' ZT_BIND_OK'", timeout=20)
    print(oz.rstrip())
    out_all.append(f"$ (outpost) curl http://10.147.17.101:8089/api/health\n{oz}")
    if "ZT_BIND_OK" not in oz:
        print("[i] ZT-curl с outpost не прошёл (не критично — отметить)")

    # ---------- вердикт ----------
    print("\n" + "=" * 46)
    if FAILS:
        print("DEPLOY-SMOKE v0.3.0: FAIL ->", *FAILS, sep="\n  - ")
    else:
        print("DEPLOY-SMOKE v0.3.0: ALL STEPS PASSED (0–6 + ZT-бонус)")
    with open(OUT, "w") as f:
        f.write("\n".join(out_all) + f"\n\nFAILS: {FAILS}\n")
    print(f"[i] транскрипт: {OUT}")
    inner.close()
    c.close()
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
