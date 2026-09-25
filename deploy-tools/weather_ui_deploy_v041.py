#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_ui_deploy_v041.py — ПОЛНЫЙ синк кода weather-стека на vitele
(структурный фикс А5 ревьюера: защита от повторения Х-1 навсегда).

Урок Х-1: деплой v0.4.0 синкал только ui/ (cp -a REPO/ui/. UIDIR/) —
stage-a/stage-b остались в runtime от 16.09, migrate_letter не доехал,
letter Замбретти был null до ручного синка 25.09.

Что делает v0.4.1 (в отличие от v040):
  0. git pull --ff-only клон репо НА VM (/home/auditbot/vitele_meteo)
  1. манифест ВСЕГО кода: ui/** (server.py, config.py, static/*),
     stage-a/*.py, stage-b/*.py (+ ui/weather-ui.service -> /etc/systemd/system)
  2. md5-отчёт по КАЖДОМУ файлу (repo vs runtime); cp только расходящихся,
     бэкап прежней копии в DASH/.backup-<date>-v041/
  3. py_compile всех .py после синка
  4. рестарт ТОЛЬКО затронутых сервисов: weather-ui (если ui/** или юнит),
     weather-api (если weather_api.py/weather_zam.py); агрегатор/коллектор —
     таймерные, код подхватят следующим слотом *:02 (рестарт не нужен)
  5. смоук: health 200, /api/now с кредами 200, конверт /api/forecast
     (available:true), Server: weather-ui/0.4.1

Креды только из /home/auditbot/.weather-ui-credentials, в лог не печатаются.
Транскрипт -> state/weather_ui_deploy_v041.txt.
Зависимость: vitele_recon.py (цепочка outpost->vitele) — как в v040; при
запуске вне агентской среды шаги выполняются вручную по SSH (docstring полон).
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
BACKUP = f"{DASH}/.backup-20260925-v041"
URL = "http://127.0.0.1:8089"
OUT = "/home/z/my-project/state/weather_ui_deploy_v041.txt"
EXPECT_SERVER_VERSION = "0.4.1"

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
    # разовый cleanup: плоские копии из первого прогона (баг dst-сплющивания
    # static-подпапок) — на runtime не ссылаются, подлежат удалению
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

    # ---------- шаг 4: смоук ----------
    print("\n### 4. смоук: health / now / forecast / Server-заголовок")
    run(f"curl -s --max-time 5 -w '\\nHTTP=%{{http_code}}\\n' {URL}/api/health",
        must='"status":"ok"', fail="health не ok")
    run(f"CRED=$(cat {CRED}); curl -s -o /dev/null -w 'now HTTP=%{{http_code}}\\n' "
        f"--max-time 8 -u \"$CRED\" {URL}/api/now", must="now HTTP=200",
        fail="/api/now с кредами не 200")
    run(f"CRED=$(cat {CRED}); curl -s -o /tmp/fc41.json -w 'fc HTTP=%{{http_code}}\\n' "
        f"--max-time 8 -u \"$CRED\" {URL}/api/forecast && "
        f"python3 -c \"import json; d=json.load(open('/tmp/fc41.json')); "
        f"assert d.get('available') is True, 'not available'; "
        f"assert len(d.get('persistence') or [])==3; "
        f"print('FC_ENVELOPE_OK available=true persistence=3')\"",
        timeout=30, must="FC_ENVELOPE_OK", fail="конверт forecast не в порядке")
    run(f"curl -s -D - -o /dev/null --max-time 5 {URL}/ | "
        f"grep -i '^Server:'", must=f"weather-ui/{EXPECT_SERVER_VERSION}",
        fail=f"Server-заголовок не weather-ui/{EXPECT_SERVER_VERSION}")

    # ---------- вердикт ----------
    print("\n" + "=" * 46)
    print(f"файлов в манифесте: {len(manifest)} (+ юнит), синкнуто: {len(synced)}")
    if FAILS:
        print("DEPLOY-SYNC v0.4.1: FAIL ->", *FAILS, sep="\n  - ")
    else:
        print("DEPLOY-SYNC v0.4.1: ALL PASSED (md5-отчёт + смоук)")
    with open(OUT, "w") as f:
        f.write("\n".join(out_all) + f"\n\nFAILS: {FAILS}\n")
    print(f"[i] транскрипт: {OUT}")
    inner.close()
    c.close()
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
