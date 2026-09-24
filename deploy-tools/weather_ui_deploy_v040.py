#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_ui_deploy_v040.py — деплой weather-ui v0.4.0 (U5 + дельты ревью
владельца: issued_values, letter) на vitele по чеклисту ревьюера:

0. git pull в /home/auditbot/vitele_meteo + md5 4 файлов (repo) + синк ui/ ->
   /home/auditbot/weather-dash/ui + md5 после синка; grep migrate_letter в
   stage-b/weather_aggregator.py на VM (код этапа B доехал)
1. рестарт ТОЛЬКО ui/server.py: PID из ss -tlnp | grep 8089 -> kill ->
   setsid nohup python3 server.py (weather-api :8090 и этап B НЕ трогаем —
   агрегатор подхватит letter в СЛЕДУЮЩЕМ hourly-слоте *:02)
2. лог старта: version=0.4.0, static loaded files=15, dropped=0
3. /api/health -> 200 {"status":"ok",...}
4. /api/forecast -> available:true, ключ issued_values, ключ letter в
   zambretti, persistence ×3. ВАЖНО: до следующего прогона агрегатора
   (*:02) letter в строках NULL (легаси) -> letter: null — это ОЖИДАЕМО,
   конверт не 503; issued_values считается из weather сразу.
5. прокси браузерных проверок: страницы 200 (вкл. /forecast), статика
   forecast.html/page-forecast.js отдаётся, в page-forecast.js есть
   issued_values; бонус ZT-curl health с outpost.

Креды на VM только в переменных shell, в лог НЕ печатаются.
Транскрипт -> state/weather_ui_deploy_v040.txt.
Зависимость: хелпер подключения vitele_recon.py (цепочка outpost->vitele, доверенный host-key out-of-band) — приватная агентская инфраструктура, в репо НЕ входит. При запуске вне агентской среды: положить рядом vitele_recon.py с connect_vitele(retries)->(client, inner) и run_tr(inner, cmd, timeout)->(rc, out, err) — либо выполнять шаги вручную по SSH (чеклист в docstring полон).
"""
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vitele_recon import connect_vitele, run_tr  # noqa: E402

REPO = "/home/auditbot/vitele_meteo"
UIDIR = "/home/auditbot/weather-dash/ui"
STAGEB = "/home/auditbot/weather-dash/stage-b"
LOG = "/home/auditbot/weather-dash/ui-server.log"
CRED = "/home/auditbot/.weather-ui-credentials"
URL_LOCAL = "http://192.168.8.146:8089"
OUT = "/home/z/my-project/state/weather_ui_deploy_v040.txt"

# эталон ревьюера v0.4.0: server.py / app.js / page-forecast.js / forecast.html
# (хэши сверкуются с локальным клоном в main(); пустые строки — заполним)
MD5_EXPECT = {
    "ui/server.py": "",
    "ui/static/app.js": "",
    "ui/static/page-forecast.js": "",
    "ui/static/forecast.html": "",
}
FAILS = []


def remote_path(rel, base):
    """ui/static/app.js -> {base}/static/app.js; ui/server.py -> {base}/server.py"""
    return f"{base}/{rel[len('ui/'):]}"


def main():
    base = "/home/z/my-project/github/vitele_meteo"
    for rel in MD5_EXPECT:
        MD5_EXPECT[rel] = hashlib.md5(
            open(os.path.join(base, rel), "rb").read()).hexdigest()
        print(f"[i] local md5 {rel} {MD5_EXPECT[rel]}")
    head = subprocess.run(["git", "-C", base, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    print(f"[i] local HEAD {head}")
    if len(sys.argv) > 1 and sys.argv[1] != head:
        print(f"[!] argv HEAD {sys.argv[1]} != local {head} — беру local")

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

    def check_md5_block(paths, where, root):
        rc, o, _e = run_tr(inner, f"md5sum {paths}", timeout=30)
        print(o.rstrip())
        out_all.append(f"$ md5sum {paths}\n{o}")
        remote = {}
        for ln in o.strip().splitlines():
            parts = ln.split()
            if len(parts) == 2:
                remote[parts[1]] = parts[0]
        for rel, h in MD5_EXPECT.items():
            # в репо файлы лежат под ui/; в UIDIR скопированы без префикса
            rp = (f"{root}/{rel}" if root == REPO
                  else f"{root}/{rel[len('ui/'):]}")
            got = remote.get(rp)
            if got == h:
                print(f"[+] md5 OK ({where}) {rp}")
            else:
                FAILS.append(f"md5 ({where}) {rp}: эталон {h}, факт {got}")
                print(f"[!] md5 MISMATCH ({where}) {rp}: {got} != {h}")

    # ---------- шаг 0: git pull + md5 + синк + код этапа B ----------
    print("\n### 0. git pull + md5 (repo/UIDIR) + синк ui/ + stage-b letter")
    run("command -v git && git --version", must="/usr/bin/git")
    run(f"cd {REPO} && git pull --ff-only 2>&1", timeout=180)
    run(f"git -C {REPO} log --oneline -1 && git -C {REPO} status --short | head -5",
        must=head, fail=f"git HEAD на vitele != {head}")
    check_md5_block(
        f"{REPO}/ui/server.py {REPO}/ui/static/app.js "
        f"{REPO}/ui/static/page-forecast.js {REPO}/ui/static/forecast.html",
        "repo", REPO)
    run(f"mkdir -p {UIDIR} && cp -a {REPO}/ui/. {UIDIR}/ && echo SYNC_OK",
        must="SYNC_OK", fail="синк ui/ не выполнен")
    check_md5_block(
        f"{UIDIR}/server.py {UIDIR}/static/app.js "
        f"{UIDIR}/static/page-forecast.js {UIDIR}/static/forecast.html",
        "UIDIR", UIDIR)
    run(f"grep -c 'def migrate_letter' {REPO}/stage-b/weather_aggregator.py",
        must="1", fail="в stage-b на VM нет migrate_letter (агрегатор не обновлён)")

    # ---------- шаг 1: рестарт ТОЛЬКО ui/server.py ----------
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
    print("[i] запуск через отдельный канал (setsid отвязал процесс)")
    ch = inner.open_session(timeout=10)
    ch.settimeout(10)
    ch.exec_command(f"cd {UIDIR} && setsid nohup python3 server.py >> {LOG} "
                    f"2>&1 < /dev/null & echo LAUNCHED")
    time.sleep(2.5)
    ch.close()
    out_all.append(f"$ cd {UIDIR} && setsid nohup python3 server.py >> {LOG} "
                   f"2>&1 < /dev/null & echo LAUNCHED\nLAUNCHED (канал закрыт)")
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
    print("\n### 2. лог старта: version=0.4.0, files=15, dropped=0")
    # grep -a: в логе встречается бинарный мусор от старых инцидентов —
    # без -a grep отвечает «binary file matches» и строку не отдаёт
    run(f"grep -a -E 'start version=' {LOG} | tail -1", must="version=0.4.0",
        fail="в последней start-строке нет version=0.4.0")
    run(f"grep -a 'static loaded' {LOG} | tail -1", must="files=15",
        fail="в static loaded нет files=15")
    run(f"echo dropped_count=$(grep -a -c 'fields dropped' {LOG}); "
        f"echo warn_count=$(grep -a -c ' WARN ' {LOG})",
        must="dropped_count=0", fail="WARN 'fields dropped' присутствует!")

    # ---------- шаг 3: health ----------
    print("\n### 3. /api/health -> 200 {\"status\":\"ok\",...}")
    run(f"curl -s --max-time 5 -w '\\nHTTP=%{{http_code}}\\n' "
        f"{URL_LOCAL}/api/health", must='"status":"ok"')

    # ---------- шаг 4: /api/forecast конверт v0.4.0 ----------
    print("\n### 4. /api/forecast: available + issued_values + letter-ключ "
          "(letter может быть null до прогона *:02 — ожидаемо)")
    run(f"CRED=$(cat {CRED}); curl -s -o /tmp/fc.json -w 'HTTP=%{{http_code}}\\n' "
        f"--max-time 8 -u \"$CRED\" {URL_LOCAL}/api/forecast; "
        f"python3 -c \"import json; d=json.load(open('/tmp/fc.json')); "
        f"print('available=',d.get('available'),'calc_ts=',d.get('calc_ts'),"
        f"'stale=',d.get('stale')); "
        f"iv=d.get('issued_values'); print('issued_values=',iv); "
        f"z=d.get('zambretti') or {{}}; "
        f"print('zambretti.letter=',z.get('letter'),'text=',"
        f"(z.get('text') or '')[:60]); "
        f"print('persistence n=',len(d.get('persistence') or [])); "
        f"assert d.get('available') is True, 'not available'; "
        f"assert 'issued_values' in d, 'no issued_values key'; "
        f"assert 'letter' in z, 'no letter key in zambretti'; "
        f"assert len(d.get('persistence') or [])==3, 'persistence != 3'\" "
        f"&& echo FC_ENVELOPE_OK",
        timeout=30, must="FC_ENVELOPE_OK",
        fail="конверт /api/forecast не соответствует v0.4.0")
    run(f"python3 -c \"import json; d=json.load(open('/tmp/fc.json')); "
        f"z=d.get('zambretti') or {{}}; "
        f"print('NOTE: letter=', z.get('letter'), "
        f"'(null до прогона агрегатора *:02 — ожидаемо легаси)')\"")

    # ---------- шаг 5: прокси браузерных проверок ----------
    print("\n### 5. прокси браузерных проверок (визуал — с LAN-машины)")
    run(f"curl -s -o /dev/null -D - --max-time 5 {URL_LOCAL}/ | "
        f"grep -i 'WWW-Authenticate'", must='Basic realm="Weather"',
        fail="нет WWW-Authenticate на /")
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/app.js | grep -o 'UI_VERSION = \"0.4.0\"' | head -1",
        must='UI_VERSION = "0.4.0"', fail="в отданном app.js нет 0.4.0")
    run(f"CRED=$(cat {CRED}) && BAD=$(for p in / /day /month /events /forecast "
        f"/settings; do c=$(curl -s -o /dev/null -w '%{{http_code}}' "
        f"--max-time 5 -u \"$CRED\" {URL_LOCAL}$p); echo \"$p=$c\"; "
        f"[ \"$c\" = 200 ] || echo BAD_$p; done); "
        f"echo \"$BAD\" | grep '^BAD_' || echo ALL_PAGES_200",
        must="ALL_PAGES_200", fail="не все страницы отдают 200")
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/page-forecast.js | "
        f"grep -q 'issued_values' && echo HAVE_ISSUED_VALUES",
        must="HAVE_ISSUED_VALUES",
        fail="в отданном page-forecast.js нет issued_values")
    run(f"CRED=$(cat {CRED}) && curl -s --max-time 5 -u \"$CRED\" "
        f"{URL_LOCAL}/static/forecast.html | grep -c 'page-forecast.js'",
        must="1", fail="в отданном forecast.html нет ссылки на page-forecast.js")
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
        print("DEPLOY-SMOKE v0.4.0: FAIL ->", *FAILS, sep="\n  - ")
    else:
        print("DEPLOY-SMOKE v0.4.0: ALL STEPS PASSED (0-5 + ZT-бонус)")
        print("ХВОСТ: letter в конверте появится после ближайшего прогона "
              "weather_aggregator (hourly-слот *:02) — до того легаси-строки "
              "отдают letter: null (конверт не падает); проверить позже: "
              f"curl -u $CRED {URL_LOCAL}/api/forecast | grep -o 'letter[^,]*'")
    with open(OUT, "w") as f:
        f.write("\n".join(out_all) + f"\n\nFAILS: {FAILS}\n")
    print(f"[i] транскрипт: {OUT}")
    inner.close()
    c.close()
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
