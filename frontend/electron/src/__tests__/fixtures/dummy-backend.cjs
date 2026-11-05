/**
 * Backend dummy pros testes reais de backend-lifecycle.ts — processo Node
 * puro (sem TS, sem depender de build), imita o essencial do binário real:
 * imprime VECTORA_IPC_PIPE=<algo> no stdout e sobe um HTTP server que
 * responde /health.
 *
 * Configurável via env vars (cada teste escolhe o comportamento):
 *   TEST_HEALTH_PORT     — porta onde o /health escuta (obrigatória)
 *   CRASH_IMMEDIATELY=1  — sai com código 1 antes de fazer qualquer coisa
 *   NEVER_HEALTHY=1      — nunca abre o servidor de /health (fica vivo)
 *   DELAY_HEALTH_MS=N    — espera N ms antes de abrir o /health
 *   SPLIT_PIPE_WRITE=1   — escreve "VECTORA_IPC_PIPE=...” fatiado em dois
 *                          process.stdout.write() com um tick entre eles
 *   SPAWN_CHILD=1        — spawna um "neto" (processo filho deste dummy,
 *                          replicando o padrão real Python→nats-server)
 *                          que fica vivo indefinidamente; imprime
 *                          "CHILD_PID=<pid>" no stdout assim que nasce
 */
const http = require("http");
const { spawn } = require("child_process");

if (process.env.CRASH_IMMEDIATELY === "1") {
  process.exit(1);
}

if (process.env.SPAWN_CHILD === "1") {
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
    stdio: "ignore",
  });
  process.stdout.write(`CHILD_PID=${child.pid}\n`);
}

const pipeValue = "\\\\.\\pipe\\test-dummy-backend";

if (process.env.SPLIT_PIPE_WRITE === "1") {
  process.stdout.write(`VECTORA_IPC_PIPE=\\\\.\\pipe\\test-du`);
  setTimeout(() => {
    process.stdout.write(`mmy-backend\n`);
  }, 20);
} else {
  process.stdout.write(`VECTORA_IPC_PIPE=${pipeValue}\n`);
}

if (process.env.NEVER_HEALTHY === "1") {
  // Fica vivo sem nunca responder /health — usado pro teste de timeout.
} else {
  const startHealthServer = () => {
    const port = Number(process.env.TEST_HEALTH_PORT);
    const server = http.createServer((req, res) => {
      if (req.url === "/health") {
        res.writeHead(200);
        res.end("ok");
      } else {
        res.writeHead(404);
        res.end();
      }
    });
    server.listen(port, "127.0.0.1");
  };

  const delay = Number(process.env.DELAY_HEALTH_MS || "0");
  if (delay > 0) {
    setTimeout(startHealthServer, delay);
  } else {
    startHealthServer();
  }
}

process.on("SIGTERM", () => process.exit(0));
