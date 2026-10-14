// frost_monitor_add.cjs — создание push-монитора «Заморозок — ночной дежурный»
// в Uptime Kuma 2.5.3 (vitele, 127.0.0.1:3001) через socket.io.
// Паттерн: scripts/kuma_files/monitor_add.cjs (проверен 25.09; форс websocket
// НЕ использовать — дефолтный polling→ws апгрейд работает).
// Push-токен после создания достаётся деплой-скриптом из sqlite (mode=ro).
// Idempotency: деплой-скрипт проверяет имя в sqlite ДО вызова этого файла.
const { io } = require("socket.io-client");

const URL = "http://127.0.0.1:3001";
const USER = process.env.KUMA_USER || "admin";
const PASS = process.env.KUMA_PASS || "";
if (!PASS) { console.error("KUMA_PASS not set"); process.exit(1); }

const MONITOR = {
  name: "Заморозок — ночной дежурный (push)",
  type: "push",
  // ожидание биения: прогоны 21:30 / 00:10 / 09:30 (макс. разрыв 9.3 ч);
  // 10 ч + 1 ретрай по 2 ч = DOWN ~12 ч после последнего пуша
  interval: 36000,
  retryInterval: 7200,
  maxretries: 1,
  resendInterval: 0,
  notificationIDList: {},
  // полный набор полей, как в рабочем http-примере (Kuma 2.5.3 на "add"
  // вызывает .every() у части из них — без них "Cannot read properties
  // of undefined", проверено 14.10)
  conditions: [],
  kafkaProducerBrokers: [],
  kafkaProducerSaslOptions: {},
  rabbitmqNodes: [],
  accepted_statuscodes: ["200-299"],
  ignoreTls: true,
  maxredirects: 10,
  timeout: 48,
  active: true,
  upsideDown: false,
  expiryNotification: false,
};

const sock = io(URL, { reconnection: false, timeout: 10000 });
const die = (m) => { console.error("[FAIL] " + m); process.exit(1); };
const t = setTimeout(() => die("global timeout 120s"), 120000);

sock.on("connect_error", (e) => die("connect_error: " + e.message));
sock.on("connect", () => {
  sock.emit("login", { username: USER, password: PASS, token: "" }, (res) => {
    if (!res || !res.ok) return die("login failed: " + JSON.stringify(res));
    console.log("[+] login OK");
    sock.emit("add", MONITOR, (res2) => {
      if (!res2 || !res2.ok) return die("add: " + JSON.stringify(res2));
      console.log("[+] монитор добавлен, monitorID=" + res2.monitorID);
      console.log("[DONE] id=" + res2.monitorID);
      clearTimeout(t);
      process.exit(0);
    });
  });
});
