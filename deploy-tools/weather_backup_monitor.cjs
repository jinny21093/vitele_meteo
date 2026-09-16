// weather_backup_monitor.cjs — weather-7 («вдогонка» §4): создать/найти ОТДЕЛЬНЫЙ
// Kuma Push-монитор для бэкап-джобы. Вывод: EXISTS|CREATED id=.., PUSH_TOKEN=..
// Алерт диска/бэкапа нельзя слать на монитор «сбор» — коллектор затрёт down своим
// up через 60 с. Интервал 86400 (push раз в сутки в 04:20); если Kuma не примет —
// повтор с 43200. Запуск на vitele: KUMA_PASS=... node weather_backup_monitor.cjs
// (socket.io-client в /tmp/kuma-setup).
const { io } = require("socket.io-client");
const crypto = require("crypto");

const URL = "http://127.0.0.1:3001";
const USER = process.env.KUMA_USER || "admin";
const PASS = process.env.KUMA_PASS || "";
const NAME = "Метеостанция .101 — бэкап (push)";

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

function addWithInterval(token, interval) {
  const mo = {
    name: NAME, type: "push", interval: interval, retryInterval: interval,
    maxretries: 1, resendInterval: 0, pushToken: token, notificationIDList: {},
    conditions: [], rabbitmqNodes: [], kafkaProducerBrokers: [],
    kafkaProducerSaslOptions: {}, active: true, upsideDown: false, ignoreTls: false,
    expiryNotification: false, accepted_statuscodes: ["200-299"],
  };
  sock.emit("add", mo, (res) => {
    if (!res || !res.ok) {
      if (interval > 43200) {
        console.log("[i] add interval=" + interval + " отклонён, повтор с 43200");
        return addWithInterval(token, 43200);
      }
      return die("add: " + JSON.stringify(res));
    }
    done("CREATED id=" + res.monitorID + " interval=" + interval + "\nPUSH_TOKEN=" + token);
  });
}

function afterAuth() {
  sock.emit("getMonitorList", (list) => {
    const items = Object.values(list || {});
    console.log("[i] мониторов в Kuma: " + items.length);
    const ex = items.find((m) => m.name === NAME);
    if (ex) {
      if (!ex.pushToken) return die("монитор существует, но pushToken не в bean");
      return done("EXISTS id=" + ex.id + "\nPUSH_TOKEN=" + ex.pushToken);
    }
    addWithInterval(crypto.randomBytes(16).toString("hex"), 86400);
  });
}
