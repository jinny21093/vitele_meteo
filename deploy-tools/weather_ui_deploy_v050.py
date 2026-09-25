#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_ui_deploy_v050.py — деплой U6 (weather-ui 0.5.0) на vitele.
Выполняется ТОЛЬКО ПОСЛЕ ПРИЁМКИ U6 (правило U6: до вердикта ревьювера VM
не трогается). База — манифестная механика v041 (структурный фикс А5), см.
weather_ui_deploy_v041.py: урок Х-1 — синк ВСЕГО кода, не только ui/.

ЧЕКЛИСТ v050 (шаги скрипта):
  0. git pull --ff-only клон репо НА VM (/home/auditbot/vitele_meteo),
     HEAD клона == локальному HEAD (иначе: сначала запушить)
  1. манифест ВСЕГО кода: ui/** (server.py, config.py, static/* — вкл.
     settings.html/page-settings.js/style.css), stage-a/*.py, stage-b/*.py;
     md5-отчёт по КАЖДОМУ файлу (repo vs runtime), бэкап расходящихся в
     .backup-<date>-v050, cp только расходящихся
  2. py_compile всех синкнутых .py
  3. рестарт ТОЛЬКО затронутых сервисов (weather-ui при ui/**; weather-api
     при weather_api.py/weather_zam.py)
  4. смоук 0.5.0: health 200; /api/now с кредами 200; конверт /api/forecast
     (available, persistence=3); НОВОЕ (U6): GET /settings -> 200,
     GET /api/settings -> 200 (wmeta-фильтр: station_ip НЕ отдаётся),
     GET /api/export.csv (окно 1 ч) -> 200 chunked/no-store, заголовок ts;
     Server: weather-ui/0.5.0
  5. verify unit-test: verify_stage_ui.sh заливается по SFTP в /tmp (удаляется
     после), гоняется в режиме unit-test c EXPECT_SERVER_VERSION=0.5.0 —
     он ОСТАНАВЛИВАЕТ юнит weather-ui (systemctl stop), прогоняет полный
     фикстурный набор на КОПИИ runtime-БД (G15 413-путь/пробник/инъекции
     с --export-max-bytes 100000, G14 settings/check-db) и СТАРТУЕТ юнит
     обратно; вердикт «VERIFY unit-test: ALL PASSED» обязателен. На живом
     VM-сервере в этот шаг ничего не пишется — только копии в /tmp/workdir.
  6. u6_smoke.sh (deploy-tools/u6_smoke.sh) — НЕ выполняется на VM: его
     U6-T1/T2/T3 покрывает шаг 5 (G15/G14) на той же механике копий; скрипт
     лежит в репо для локального стенда (транскрипт прогона 56/0 —
     state/u6_smoke_out.txt агентской песочницы).

Отличия от v041: EXPECT_SERVER_VERSION/UI 0.5.0 (было 0.4.1), BACKUP-путь
v050, смоук дополнен U6-проверками, добавлен шаг 5 verify unit-test.

Креды только из /home/auditbot/.weather-ui-credentials, в лог не печатаются.
Транскрипт -> state/weather_ui_deploy_v050.txt.
Зависимость: vitele_recon.py (цепочка outpost->vitele) — как в v041; при
запуске вне агентской среды шаги выполняются вручную по SSH (чеклист полон).
"""
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vitele_recon import connect_vitele, run_tr  # noqa: E402

REPO = "/home/auditbot/vitele_meteo"          # клон НА VM
DASH = "/home/auditbot/weather-dash"          # runtime этапов A/B
UIDIR = "/home/auditbot/weather-dash/ui"      # runtime UI
CRED = "/home/auditbot/.weather-ui-credentials"
BACKUP = f"{DASH}/.backup-20260925-v050"
URL = "http://127.0.0.1:8089"
OUT = "/home/z/my-project/state/weather_ui_deploy_v050.txt"
EXPECT_SERVER_VERSION = "0.5.0"
EXPECT_UI_VERSION = "0.5.0"
VERIFY_SRC = "/home/z/my-project/github/vitele_meteo/deploy-tools/verify_stage_ui.sh"
VERIFY_TMP = "/tmp/verify_stage_ui_v050.sh"

FAILS = []


def md5_local(base, rel):
    with open(os.path.join(base, rel), "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def main():
    base_local = "/home/z/my-project/github/vitele_meteo"
    head_local = subprocess.run(
        ["git", "-C", base_local, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    print(f"[i] local HEAD {head_local}")

    c, inner = connect_vitele(retries=4)
    out_all = []
    changed_ui = 0          # ui/** (или юнит) изменились -> рестарт weather-ui
    changed_stageb_api = 0  # weather_api.py/weather_zam.py -> рестарт weather-api

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

    # ---------- шаг 0: git pull клонa на VM ----------
    print("\n### 0. git pull --ff-only (клон на VM) + HEAD")
    run(f"cd {REPO} && git pull --ff-only 2>&1 && git log --oneline -1",
        timeout=180, must=head_local,
        fail=f"HEAD клона на VM != локальному {head_local} — сначала запушить")

    # ---------- шаг 1: манифест + md5-отчёт + синк расходящихся ----------
    print("\n### 1. манифест ВСЕГО кода: ui/** + stage-a/*.py + stage-b/*.py")
    # разовый cleanup: плоские копии из первого прогона v041 (баг
    # dst-сплющивания static-подпапок) — на runtime не ссылаются
    run(f"rm -f {UIDIR}/static/favicon.svg {UIDIR}/static/chart.min.js && "
        f"echo FLAT_JUNK_CLEANED")
    files = run(
        f"cd {REPO} && (git ls-files ui/ | grep -v '^ui/static/' ; "
        f"git ls-files 'ui/static/' ; git ls-files 'stage-a/*.py' ; "
        f"git ls-files 'stage-b/*.py') | sort", timeout=30)
    manifest = [ln.strip() for ln in files.splitlines() if ln.strip()]

    run(f"mkdir -p {BACKUP} && echo BACKUP_DIR_OK",
        must="BACKUP_DIR_OK", fail="не создан каталог бэкапа")

    report = []
    synced = []
    for rel in manifest:
        src = f"{REPO}/{rel}"
        if rel.startswith("ui/static/"):
            # подпапки сохраняются: ui/static/vendor/chart.min.js ->
            # UIDIR/static/vendor/chart.min.js (НЕ сплющивать!)
            dst = f"{UIDIR}/static/{rel[len('ui/static/'):] }"
        elif rel.startswith("ui/"):
            dst = f"{UIDIR}/{rel[len('ui/'):] }"   # вкл. weather-ui.service (v040-семантика)
        else:  # stage-a/*.py, stage-b/*.py
            dst = f"{DASH}/{rel.split('/')[-1]}"
        _, lm, _ = run_tr(inner, f"md5sum {src}", timeout=20)
        _, rm, _ = run_tr(inner, f"md5sum {dst} 2>/dev/null || echo ABSENT",
                          timeout=20)
        lm = lm.split()[0] if lm.split() else "?"
        rm = rm.split()[0] if rm.split() else "ABSENT"
        if lm == rm:
            report.append(f"  OK      {rel} {lm[:10]} (уже синхронен)")
            continue
        # бэкап прежней копии (если была) + cp
        if rm != "ABSENT":
            run(f"cp -a {dst} {BACKUP}/{rel.replace('/', '_')}.bak", timeout=20)
        run(f"mkdir -p $(dirname {dst}) && cp -a {src} {dst} && chmod 644 {dst} "
            f"&& md5sum {dst}", timeout=20,
            must=lm, fail=f"после cp md5 {dst} != {lm}")
        report.append(f"  SYNCED  {rel} {lm[:10]} (был {rm[:10]})")
        synced.append(rel)
        if rel.startswith("ui/"):
            changed_ui = 1
        if rel in ("stage-b/weather_api.py", "stage-b/weather_zam.py"):
            changed_stageb_api = 1

    # юнит отдельно: /etc/systemd/system требует sudo
    unit_rel = "ui/weather-ui.service"
    if unit_rel in manifest:
        _, lm, _ = run_tr(inner, f"md5sum {REPO}/{unit_rel}", timeout=20)
        lm = lm.split()[0]
        _, rm, _ = run_tr(
            inner, "sudo -n md5sum /etc/systemd/system/weather-ui.service "
                   "2>/dev/null || echo ABSENT", timeout=20)
        rm = rm.split()[0] if rm.split() and rm.split()[0] != "ABSENT" else "ABSENT"
        if lm == rm:
            report.append(f"  OK      {unit_rel} {lm[:10]} (/etc, уже синхронен)")
        else:
            run(f"sudo -n install -m 644 {REPO}/{unit_rel} "
                f"/etc/systemd/system/weather-ui.service && "
                f"sudo -n md5sum /etc/systemd/system/weather-ui.service",
                timeout=30, must=lm, fail="юнит в /etc не синкнулся")
            report.append(f"  SYNCED  {unit_rel} {lm[:10]} (/etc, был {rm[:10]})")
            run("sudo -n systemctl daemon-reload", timeout=30)
            changed_ui = 1

    print("\n=== MD5-ОТЧЁТ по каждому файлу ===")
    for ln in report:
        print(ln)
    out_all.append("MD5-ОТЧЁТ:\n" + "\n".join(report))
    if not synced:
        print("[i] расхождений нет — синк не потребовался, рестартов не будет")

    # ---------- шаг 2: py_compile ----------
    print("\n### 2. py_compile изменённых .py")
    pys = [r for r in synced if r.endswith(".py")]
    if pys:
        paths = []
        for r in pys:
            paths.append(f"{DASH}/{r.split('/')[-1]}" if r.startswith("stage")
                         else f"{UIDIR}/{r[len('ui/'):]}")
        run(f"/usr/bin/python3 -m py_compile {' '.join(paths)} && echo PY_COMPILE_OK",
            must="PY_COMPILE_OK", fail="py_compile провален после синка")
    else:
        print("[i] .py не менялись — компиляция пропущена")

    # ---------- шаг 3: рестарт только затронутых ----------
    print("\n### 3. рестарт затронутых сервисов")
    if changed_stageb_api:
        run("sudo -n systemctl restart weather-api && "
            "systemctl is-active weather-api", must="active",
            fail="weather-api не поднялся после рестарта")
    if changed_ui:
        run("sudo -n systemctl restart weather-ui && sleep 1.5 && "
            "systemctl is-active weather-ui", must="active",
            fail="weather-ui не поднялся после рестарта")
    if not synced:
        print("[i] рестарты не нужны (код не менялся)")
    run("systemctl is-active weather-ui weather-api; true")

    # ---------- шаг 4: смоук 0.5.0 (+ U6) ----------
    print("\n### 4. смоук: health / now / forecast / settings / export / Server")
    run(f"curl -s --max-time 5 -w '\\nHTTP=%{{http_code}}\\n' {URL}/api/health",
        must='"status":"ok"', fail="health не ok")
    run(f"CRED=$(cat {CRED}); curl -s -o /dev/null -w 'now HTTP=%{{http_code}}\\n' "
        f"--max-time 8 -u \"$CRED\" {URL}/api/now", must="now HTTP=200",
        fail="/api/now с кредами не 200")
    run(f"CRED=$(cat {CRED}); curl -s -o /tmp/fc50.json -w 'fc HTTP=%{{http_code}}\\n' "
        f"--max-time 8 -u \"$CRED\" {URL}/api/forecast && "
        f"python3 -c \"import json; d=json.load(open('/tmp/fc50.json')); "
        f"assert d.get('available') is True, 'not available'; "
        f"assert len(d.get('persistence') or [])==3; "
        f"print('FC_ENVELOPE_OK available=true persistence=3')\"",
        timeout=30, must="FC_ENVELOPE_OK", fail="конверт forecast не в порядке")
    # U6: страница и /api/settings
    run(f"CRED=$(cat {CRED}); curl -s -o /tmp/set50.html "
        f"-w 'settings-page HTTP=%{{http_code}}\\n' --max-time 8 -u \"$CRED\" "
        f"{URL}/settings && grep -q 'id=\"set-export-btn\"' /tmp/set50.html "
        f"&& echo SETTINGS_DOM_OK", must="SETTINGS_DOM_OK",
        fail="/settings 200 без DOM-грепа set-export-btn")
    run(f"CRED=$(cat {CRED}); curl -s -o /tmp/set50.json "
        f"-w 'api-settings HTTP=%{{http_code}}\\n' --max-time 8 -u \"$CRED\" "
        f"{URL}/api/settings && python3 -c \"import json; d=json.load("
        f"open('/tmp/set50.json')); assert 'wmeta' in d, 'no wmeta'; "
        f"assert 'station_ip' not in d['wmeta'], 'filter leak station_ip'; "
        f"print('SETTINGS_FILTER_OK')\"", must="SETTINGS_FILTER_OK",
        fail="/api/settings: конверт/фильтр wmeta провален")
    # U6: export.csv малое окно -> 200 chunked (read-only GET, без записи)
    run(f"CRED=$(cat {CRED}); NOW=$(date +%s); "
        f"curl -s -D /tmp/exp50.h -o /tmp/exp50.csv "
        f"-w 'export HTTP=%{{http_code}}\\n' --max-time 15 -u \"$CRED\" "
        f"\"{URL}/api/export.csv?from=$((NOW-3600))&to=$NOW\" && "
        f"tr -d '\\r' < /tmp/exp50.h | grep -i 'Transfer-Encoding: chunked' && "
        f"head -c 3 /tmp/exp50.csv | grep -q '^ts;' && echo EXPORT_CHUNKED_OK",
        timeout=60, must="EXPORT_CHUNKED_OK",
        fail="export.csv (1 ч) не 200/chunked/заголовок ts;")
    run(f"curl -s -D - -o /dev/null --max-time 5 {URL}/ | "
        f"grep -i '^Server:'", must=f"weather-ui/{EXPECT_SERVER_VERSION}",
        fail=f"Server-заголовок не weather-ui/{EXPECT_SERVER_VERSION}")

    # ---------- шаг 5: verify unit-test (останавливает юнит — только здесь) ----------
    print("\n### 5. verify unit-test (systemctl stop/start weather-ui внутри)")
    sftp = inner.open_sftp_client()
    sftp.put(VERIFY_SRC, VERIFY_TMP)
    sftp.chmod(VERIFY_TMP, 0o755)
    sftp.close()
    print(f"[+] verify_stage_ui.sh -> {VERIFY_TMP}")
    out_all.append(f"[+] {VERIFY_SRC} -> {VERIFY_TMP}")
    _, o, _ = run_tr(inner,
                     f"EXPECT_SERVER_VERSION={EXPECT_SERVER_VERSION} "
                     f"EXPECT_UI_VERSION={EXPECT_UI_VERSION} "
                     f"bash {VERIFY_TMP} unit-test 2>&1", timeout=900)
    print(o.rstrip()[-3000:])
    out_all.append(o)
    if "VERIFY unit-test: ALL PASSED" not in o:
        FAILS.append("verify unit-test: ALL PASSED — вердикт не получен")
    run(f"rm -f {VERIFY_TMP} && echo verify_tmp_cleaned")

    # ---------- вердикт ----------
    print("\n" + "=" * 46)
    print(f"файлов в манифесте: {len(manifest)} (+ юнит), синкнуто: {len(synced)}")
    if FAILS:
        print("DEPLOY v0.5.0: FAIL ->", *FAILS, sep="\n  - ")
    else:
        print("DEPLOY v0.5.0: ALL PASSED (md5-отчёт + смоук + verify unit-test)")
    with open(OUT, "w") as f:
        f.write("\n".join(out_all) + f"\n\nFAILS: {FAILS}\n")
    print(f"[i] транскрипт: {OUT}")
    inner.close()
    c.close()
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
