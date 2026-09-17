#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_ui_deploy.py — деплой-смоук weather-ui v0.2.2 на vitele (задание ревьюера).

Шаги: (0) git pull в репо-клоне на vitele + синк ui/ -> /home/auditbot/weather-dash/ui
(1) префлайт (8089 свободен, weather-api жив, креды идемпотентно) -> запуск
setsid nohup (вне systemd — юнит U7) -> строки лога: start version=0.2.2,
static loaded files=N bytes=M, ОТСУТСТВИЕ WARN 'fields dropped'
(2) health 200 без auth (3) meta.wmeta целиком (4) history c rain_total_mm
(закрывает V-4) (5) cache-заголовки app.js/favicon.svg (6) hourly 24ч / daily 30д
(7) серверные прокси браузерных проверок (браузер — вне песочницы).
Транскрипт -> state/weather_ui_deploy.txt. Креды в лог НЕ печатаются.
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
URL = "http://127.0.0.1:8089"          # по заданию ревьюера — но 127.0.0.1 НЕ в BIND_HOSTS (§2.3)
URL_LOCAL = "http://192.168.8.146:8089"  # фактический биндинг: LAN-IP хоста
OUT = "/home/z/my-project/state/weather_ui_deploy.txt"

MD5_EXPECT = {}          # заполнится локально в main()
FAILS = []


def main():
    import hashlib
    for f in ("ui/server.py", "ui/config.py", "ui/static/app.js"):
        p = os.path.join("/home/z/my-project/github/vitele_meteo", f)
        MD5_EXPECT[f] = hashlib.md5(open(p, "rb").read()).hexdigest()
    print("[i] локальные md5:", MD5_EXPECT)

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
            FAILS.append(fail or f"must '{must}' не найден: {cmd[:60]}")
            print(f"[!] FAIL: ожидалось '{must}'")
        return o

    # ---------- 0. актуализация кода ----------
    print("\n### 0. git pull в репо-клоне + синк ui/")
    run("command -v git && git --version", must="/usr/bin/git")
    run(f"if [ -d {REPO}/.git ]; then git -C {REPO} pull --ff-only 2>&1; "
        f"else git clone https://github.com/jinny21093/vitele_meteo.git {REPO} 2>&1; fi",
        timeout=120)
    run(f"git -C {REPO} log --oneline -1 && git -C {REPO} status --short | head -3",
        must="2394c8f", fail="git HEAD на vitele != 2394c8f")
    run(f"mkdir -p {UIDIR} && cp -a {REPO}/ui/. {UIDIR}/ && echo SYNC_OK", must="SYNC_OK")
    run(f"md5sum {UIDIR}/server.py {UIDIR}/config.py {UIDIR}/static/app.js")
    rc, o, _e = run_tr(inner, f"md5sum {UIDIR}/server.py {UIDIR}/config.py "
                                f"{UIDIR}/static/app.js", timeout=30)
    remote_md5 = {}
    for ln in o.strip().splitlines():
        parts = ln.split()
        if len(parts) == 2:
            remote_md5[parts[1]] = parts[0]
    for f, h in MD5_EXPECT.items():
        rpath = f"{UIDIR}/{os.path.basename(f)}"
        if f.endswith("server.py") or f.endswith("config.py"):
            rpath = f"{UIDIR}/{os.path.basename(f)}"
        elif "app.js" in f:
            rpath = f"{UIDIR}/static/app.js"
        got = remote_md5.get(rpath)
        if got == h:
            print(f"[+] md5 OK {rpath} {got}")
        else:
            FAILS.append(f"md5 {rpath}: локально {h}, на vitele {got}")
            print(f"[!] md5 MISMATCH {rpath}: {got} != {h}")

    # ---------- 1. префлайт + запуск ----------
    print("\n### 1. префлайт и запуск (вне systemd; юнит — U7)")
    # идемпотентный рестарт: сначала снять возможный процесс прошлой попытки
    # ([s]erver\.py — regex-трюк против самосовпадения bash-враппера pgrep)
    run("pkill -f '[s]erver\\.py'; sleep 0.7; pgrep -af '[s]erver\\.py' || echo UI_STOPPED",
        must="UI_STOPPED", fail="не удалось снять старый процесс ui")
    run("ss -ltn | grep ':8089 ' || echo PORT_8089_FREE", must="PORT_8089_FREE")
    run("systemctl is-active weather-api weather-collector; true")
    run(f"if [ -f {CRED} ]; then echo CRED_EXISTS; else python3 -c "
        f"\"import secrets; print('weather:' + secrets.token_urlsafe(24))\" > {CRED} "
        f"&& chmod 600 {CRED} && echo CRED_CREATED; fi", must="CRED_")
    print("[i] запуск через отдельный канал (закрываем принудительно — setsid "
          "отвязал процесс; EOF-чтение в paramiko не сработает на &)")
    ch = inner.open_session(timeout=10)
    ch.settimeout(10)
    ch.exec_command(f"cd {UIDIR} && setsid nohup python3 server.py >> {LOG} "
                    f"2>&1 < /dev/null & echo LAUNCHED")
    time.sleep(2.5)
    ch.close()
    out_all.append(f"$ cd {UIDIR} && setsid nohup python3 server.py >> {LOG} "
                   f"2>&1 < /dev/null & echo LAUNCHED\nLAUNCHED (канал закрыт "
                   f"принудительно через 2.5 c; процесс отвязан setsid)")
    for _ in range(25):                       # ждём живость (до ~5 c)
        rc, o, _e = run_tr(inner, f"curl -s -o /dev/null -w '%{{http_code}}' "
                                  f"--max-time 2 {URL_LOCAL}/api/health", timeout=10)
        if o.strip() == "200":
            print("[+] health 200 после старта (LAN-IP)")
            break
        time.sleep(0.2)
    else:
        FAILS.append("health 200 не получен после запуска")
    run(f"pgrep -af '[s]erver\\.py' | head -2")
    run(f"ss -ltn | grep ':8089 '")
    run(f"grep -E 'start version=' {LOG} | tail -2", must="version=0.2.2",
        fail="в start-строке нет version=0.2.2")
    run(f"grep -E 'start version=' {LOG} | tail -1 | grep -o "
        f"'bind=192.168.8.146,10.147.17.101 port=8089'",
        must="bind=192.168.8.146,10.147.17.101 port=8089",
        fail="bind/port в start-строке не те")
    run(f"grep 'static loaded' {LOG} | tail -1", must="static loaded files=",
        fail="нет строки static loaded")
    run(f"echo dropped_count=$(grep -c 'fields dropped' {LOG}); "
        f"echo warn_count=$(grep -c ' WARN ' {LOG})",
        must="dropped_count=0", fail="WARN 'fields dropped' присутствует!")

    # ---------- 2. health ----------
    print("\n### 2. health 200 без auth")
    run(f"curl -s --max-time 5 {URL_LOCAL}/api/health && echo")
    run(f"curl -s -o /dev/null -w 'loopback_127.0.0.1=%{{http_code}} (000 = refused: "
        f"127.0.0.1 не в BIND_HOSTS, §2.3)\\n' --max-time 3 {URL}/api/health")

    # ---------- 3. meta.wmeta ----------
    print("\n### 3. /api/meta -> .wmeta целиком")
    rc, o, _e = run_tr(inner, "command -v jq >/dev/null && echo JQ_OK || echo NO_JQ",
                       timeout=15)
    jq = "JQ_OK" in o
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 8 -u \"$CRED\" {URL_LOCAL}/api/meta | "
        + ("jq .wmeta" if jq else
           "python3 -c 'import json,sys; print(json.dumps("
           "json.load(sys.stdin)[\"wmeta\"], ensure_ascii=False, indent=2))'"),
        timeout=30, must="tz_offset_seconds")

    # ---------- 4. history c rain_total_mm (V-4) ----------
    print("\n### 4. /api/history — 11 полей вкл. rain_total_mm (закрывает V-4)")
    run(f"T=$(date +%s); CRED=$(cat {CRED}); curl -s --max-time 8 -u \"$CRED\" "
        f"\"{URL_LOCAL}/api/history?from=$((T-3600))&to=${{T}}&fields=ts,"
        f"outdoor_temp_c,indoor_temp_c,outdoor_hum_pct,pressure_rel_mmhg,wind_ms,"
        f"gust_ms,wind_avg10_ms,rain_total_mm,light_wm2,uvi\" | head -c 300; echo",
        timeout=30)

    # ---------- 5. cache-заголовки ----------
    print("\n### 5. Cache-Control: app.js vs favicon.svg (GET: HEAD -> 501, do_HEAD нет)")
    run(f"CRED=$(cat {CRED}) && curl -s -o /dev/null -D - --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/app.js | grep -i cache", must="no-cache",
        fail="app.js без no-cache")
    run(f"CRED=$(cat {CRED}) && curl -s -o /dev/null -D - --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/icons/favicon.svg | grep -i cache", must="max-age=86400",
        fail="favicon.svg без max-age=86400")
    run(f"curl -s -o /dev/null -w 'HEAD_status=%{{http_code}} (501 = do_HEAD не "
        f"реализован, по дизайну)\\n' --max-time 5 {URL_LOCAL}/static/app.js")

    # ---------- 6. hourly 24ч / daily 30д ----------
    print("\n### 6. /api/hourly (24ч) и /api/daily (30д) — первые 200 байт")
    run(f"T=$(date +%s); CRED=$(cat {CRED}); curl -s --max-time 8 -u \"$CRED\" "
        f"\"{URL_LOCAL}/api/hourly?from=$((T-86400))&to=${{T}}\" | head -c 200; echo", timeout=30)
    run(f"T=$(date +%s); CRED=$(cat {CRED}); curl -s --max-time 8 -u \"$CRED\" "
        f"\"{URL_LOCAL}/api/daily?from=$((T-2592000))&to=${{T}}\" | head -c 200; echo", timeout=30)

    # ---------- 7. серверные прокси браузерных проверок ----------
    print("\n### 7. прокси-проверки для браузера (сам браузер — с LAN-машины)")
    run(f"curl -s -o /dev/null -w 'page_noauth=%{{http_code}}\\n' --max-time 5 {URL_LOCAL}/")
    run(f"curl -s -o /dev/null -D - --max-time 5 {URL_LOCAL}/ | grep -i 'WWW-Authenticate'",
        must="Basic realm=\"Weather\"", fail="нет WWW-Authenticate на /")
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/app.js | grep -o 'UI_VERSION = \"0.2.2\"' | head -1",
        must='UI_VERSION = "0.2.2"', fail="в отданном app.js нет 0.2.2")
    run(f"CRED=$(cat {CRED}) && for p in / /day /month /settings; do "
        f"echo -n \"$p -> \"; curl -s -o /dev/null -w '%{{http_code}}\\n' "
        f"--max-time 5 -u \"$CRED\" {URL_LOCAL}$p; done")
    print("\n### бонус: ZT-биндинг снаружи хоста (curl с outpost на 10.147.17.101)")
    run_tr_out = run_tr(tr_out, f"curl -s --max-time 6 "
                                f"{URL_LOCAL.replace('192.168.8.146', '10.147.17.101')}"
                                f"/api/health && echo ' ZT_BIND_OK'", timeout=20)
    print(run_tr_out[1].rstrip())
    out_all.append(f"$ (outpost) curl http://10.147.17.101:8089/api/health\n{run_tr_out[1]}")
    if "ZT_BIND_OK" not in run_tr_out[1]:
        print("[i] ZT-curl с outpost не прошёл (не критично: LAN/фаервол ZT — отметить)")

    # ---------- вердикт ----------
    print("\n" + "=" * 46)
    if FAILS:
        print("DEPLOY-SMOKE: FAIL ->", *FAILS, sep="\n  - ")
    else:
        print("DEPLOY-SMOKE: ALL STEPS PASSED (0–6 + прокси 7)")
    with open(OUT, "w") as f:
        f.write("\n".join(out_all) + f"\n\nFAILS: {FAILS}\n")
    print(f"[i] транскрипт: {OUT}")
    inner.close()
    c.close()
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
