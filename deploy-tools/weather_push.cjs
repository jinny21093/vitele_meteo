// weather_push.cjs — этап A6 (weather-6): создать/найти Kuma Push-монитор
// для weather-collector. Вывод: EXISTS|CREATED id=.., PUSH_TOKEN=..
// Запуск на vitele: KUMA_PASS=... node weather_push.cjs (socket.io-client в /tmp/kuma-setup)
const { io } = require("socket.io-client");
const crypto = require("crypto");

const URL = "http://127.0.0.1:3001";
const USER = process.env.KUMA_USER || "admin";
const PASS = process.env.KUMA_PASS || "";
const NAME = "Метеостанция .101 — сбор (push)";

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
    if (ex) {
      if (!ex.pushToken) return die("монитор существует, но pushToken не в bean");
      return done("EXISTS id=" + ex.id + "\nPUSH_TOKEN=" + ex.pushToken);
    }
    const token = crypto.randomBytes(16).toString("hex");
    const mo = {
      name: NAME, type: "push", interval: 60, retryInterval: 60, maxretries: 2,
      resendInterval: 0, pushToken: token, notificationIDList: {}, conditions: [],
      rabbitmqNodes: [], kafkaProducerBrokers: [], kafkaProducerSaslOptions: {},
      active: true, upsideDown: false, ignoreTls: false, expiryNotification: false,
      accepted_statuscodes: ["200-299"],
    };
    sock.emit("add", mo, (res) => {
      if (!res || !res.ok) return die("add: " + JSON.stringify(res));
      done("CREATED id=" + res.monitorID + "\nPUSH_TOKEN=" + token);
    });
  });
}
