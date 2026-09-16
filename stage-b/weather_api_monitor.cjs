// weather_api_monitor.cjs — этап B5 (weather-8): создать/найти Kuma HTTP-монитор
// для weather-api (:8090/health, без auth — эндпоинт не выдаёт метеоданных).
// Вывод: EXISTS|CREATED id=.. Запуск: KUMA_PASS=... node weather_api_monitor.cjs
const { io } = require("socket.io-client");

const URL = "http://127.0.0.1:3001";
const USER = process.env.KUMA_USER || "admin";
const PASS = process.env.KUMA_PASS || "";
const NAME = "Метеостанция .101 — API (http)";

if (!PASS) { console.error("[FAIL] KUMA_PASS not set"); process.exit(1); }

const sock = io(URL, { transports: ["websocket"], reconnection: false, timeout: 10000 });
const die = (m) => { console.error("[FAIL] " + m); process.exit(1); };
const t = setTimeout(() => die("global timeout 90s"), 90000);
const done = (m) => { console.log(m); clearTimeout(t); process.exit(0); };

sock.on("connect_error", (e) => die("connect_error: " + e.message));
sock.on("connect", () => {
  console.log("[+] socket connected");
  sock.emit("login", { username: USER, password: PASS, token: "" }, (res) => {
    if (!res || !res.ok) return die("login: " + JSON.stringify(res));
    console.log("[+] login OK");
    afterAuth();
  });
});

function afterAuth() {
  sock.emit("getMonitorList", (list) => {
    const items = Object.values(list || {});
    console.log("[i] мониторов в Kuma: " + items.length);
    const ex = items.find((m) => m.name === NAME);
    if (ex) return done("EXISTS id=" + ex.id);
    const mo = {
      name: NAME, type: "http", url: "http://127.0.0.1:8090/health",
      method: "GET", interval: 60, retryInterval: 60, maxretries: 2,
      resendInterval: 0, notificationIDList: {}, conditions: [],
      rabbitmqNodes: [], kafkaProducerBrokers: [], kafkaProducerSaslOptions: {},
      active: true, upsideDown: false, ignoreTls: true, expiryNotification: false,
      accepted_statuscodes: ["200-299"], maxredirects: 5, timeout: 10,
    };
    sock.emit("add", mo, (res) => {
      if (!res || !res.ok) return die("add: " + JSON.stringify(res));
      done("CREATED id=" + res.monitorID);
    });
  });
}
