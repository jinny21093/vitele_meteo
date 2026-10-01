#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""u8_mocks.py — тестовые моки U8 (stdlib-only), поднимаются тест-раннером.

Три HTTP-сервера на 127.0.0.1 (эфемерные порты):
  llm  — OpenAI-совместимый POST /chat/completions (эмулирует Z.ai);
  tg   — POST /bot<token>/sendMessage (эмулирует Bot API через релей);
  kuma — коллектор Kuma-push (M6), пишет строки запроса.

Управление — POST /control на ЛЮБОМ из серверов:
  {"action": "reset"}  — сброс всех сценариев/счётчиков;
  {"action": "stats"}  — {"stats": {"requests": N, "bodies": [...]}} для
                          llm/tg, {"stats": {"queries": [...]}} для kuma;
  {"target": "llm", ...} — content, finish ("stop"), model_override,
      reasoning, tier, http_status (401/402/500/...), fail_always (bool),
      fail_times (int — первые N запросов неуспешны, затем recovery —
      тест 25), sleep_s (задержка ответа — тест 4);
  {"target": "tg", "fail_always": bool, "fail_times": int}.

Моки ничего не пишут на диск и в stdout; токены — только в памяти.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_LLM_CONTENT = (
    "Днём воздух прогрелся до 12.1 °C, минимальная температура опустилась "
    "до 3.8 °C. Осадков не зафиксировано: 0.0 мм. Ветер к вечеру "
    "усиливался до 5.8 м/с при среднем 1.4 м/с."
)


class LLMState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.content = DEFAULT_LLM_CONTENT
            self.finish = "stop"
            self.model_override = None
            self.reasoning = None
            self.tier = None
            self.http_status = None
            self.fail_always = False
            self.fail_times = 0
            self.sleep_s = 0.0
            self.seen = 0
            self.bodies = []


class TGState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.fail_always = False
            self.fail_times = 0
            self.seen = 0
            self.bodies = []
            self.next_id = 100


class KumaState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.queries = []


def _make_handler(kind, state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # тишина: вывод теста — только от раннера
            pass

        def _reply(self, code, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _control(self, req):
            act = req.get("action")
            if act == "reset":
                state.reset()
                return self._reply(200, {"ok": True, "kind": kind})
            if act == "stats":
                with state.lock:
                    if kind == "kuma":
                        st = {"queries": list(state.queries)}
                    else:
                        st = {"requests": state.seen, "bodies": list(state.bodies)}
                return self._reply(200, {"ok": True, "stats": st})
            tgt = req.get("target")
            if tgt != kind:
                return self._reply(200, {"ok": False, "error": "wrong target"})
            with state.lock:
                for k in ("content", "finish", "model_override", "reasoning",
                          "tier", "http_status", "fail_always", "fail_times",
                          "sleep_s"):
                    if k in req:
                        setattr(state, k, req[k])
            return self._reply(200, {"ok": True})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if self.path == "/control":
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                return self._control(req)
            if kind == "llm" and self.path.endswith("/chat/completions"):
                return self._llm(self._read_body(length))
            if kind == "tg" and "/bot" in self.path and self.path.endswith("/sendMessage"):
                return self._tg(self._read_body(length))
            return self._reply(404, {"ok": False, "error": "no route"})

        def do_GET(self):  # noqa: N802 — Kuma push идёт GET с query
            if self.path == "/control":
                return self._control({})
            if kind == "kuma":
                with state.lock:
                    state.queries.append(self.path)
                return self._reply(200, {"ok": True})
            return self._reply(404, {"ok": False, "error": "no route"})

        def _read_body(self, length):
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _llm(self, body):
            with state.lock:
                state.seen += 1
                state.bodies.append(body)
                fail = state.http_status is not None and (
                    state.fail_always or state.fail_times > 0)
                if state.http_status is not None and not state.fail_always \
                        and state.fail_times > 0:
                    state.fail_times -= 1
                sleep_s, content, finish = state.sleep_s, state.content, state.finish
                model_override = state.model_override
                reasoning, tier = state.reasoning, state.tier
                http_status = state.http_status or 500
            if sleep_s:
                time.sleep(sleep_s)
            if fail:
                return self._reply(http_status,
                                   {"error": {"code": str(http_status),
                                              "message": "mock failure"}})
            resp = {
                "model": model_override or body.get("model"),
                "choices": [{
                    "message": {"content": content},
                    "finish_reason": finish,
                }],
                "usage": {"prompt_tokens": 210, "completion_tokens": 96,
                          "total_tokens": 306, "reasoning_tokens": 0},
            }
            if reasoning is not None:
                resp["choices"][0]["message"]["reasoning_content"] = reasoning
            if tier is not None:
                resp["tier"] = tier
            return self._reply(200, resp)

        def _tg(self, body):
            with state.lock:
                state.seen += 1
                state.bodies.append({"path": self.path, "body": body})
                fail = state.fail_always or state.fail_times > 0
                if not state.fail_always and state.fail_times > 0:
                    state.fail_times -= 1
                mid = None
                if not fail:
                    mid = state.next_id
                    state.next_id += 1
            if fail:
                return self._reply(503, {"ok": False, "error_code": 503,
                                         "description": "mock unavailable"})
            return self._reply(200, {"ok": True, "result": {"message_id": mid}})

    return Handler


class MockBase:
    def __init__(self, kind, state):
        self.kind = kind
        self.state = state
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                         _make_handler(kind, state))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def start_all():
    """Поднять llm/tg/kuma. -> (llm, tg, kuma)."""
    return (MockBase("llm", LLMState()), MockBase("tg", TGState()),
            MockBase("kuma", KumaState()))
