import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("..", import.meta.url));
const reportPath = fileURLToPath(
  new URL("../reports/report.json", import.meta.url),
);
const testCodePath = fileURLToPath(
  new URL("../tests/test_generated.py", import.meta.url),
);

const runState = {
  running: false,
  startedAt: null,
  finishedAt: null,
  exitCode: null,
  message: "Idle",
  output: "",
};

function pythonExecutable() {
  const venvPython = fileURLToPath(
    new URL("../.venv/Scripts/python.exe", import.meta.url),
  );
  return existsSync(venvPython) ? venvPython : "python";
}

function jsonEndpoint(pathname, filePath) {
  return async (request, response) => {
    if (!request.url?.startsWith(pathname)) return false;
    try {
      const content = await readFile(filePath, "utf-8");
      response.statusCode = 200;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      response.end(content);
    } catch (error) {
      response.statusCode = 404;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      response.end(JSON.stringify({ error: String(error) }));
    }
    return true;
  };
}

function textEndpoint(pathname, filePath) {
  return async (request, response) => {
    if (!request.url?.startsWith(pathname)) return false;
    try {
      const content = await readFile(filePath, "utf-8");
      response.statusCode = 200;
      response.setHeader("Content-Type", "text/plain; charset=utf-8");
      response.end(content);
    } catch (error) {
      response.statusCode = 404;
      response.setHeader("Content-Type", "text/plain; charset=utf-8");
      response.end(String(error));
    }
    return true;
  };
}

function runPipelineEndpoint(pathname) {
  return async (request, response) => {
    if (request.url !== pathname || request.method !== "POST") return false;

    if (runState.running) {
      response.statusCode = 409;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      response.end(
        JSON.stringify({ error: "Pipeline is already running", runState }),
      );
      return true;
    }

    runState.running = true;
    runState.startedAt = new Date().toISOString();
    runState.finishedAt = null;
    runState.exitCode = null;
    runState.message = "Pipeline started from dashboard";
    runState.output = "";

    const child = spawn(
      pythonExecutable(),
      ["main.py", "--predictive-test-selection"],
      {
        cwd: repoRoot,
        windowsHide: true,
        env: {
          ...process.env,
          PYTHONUNBUFFERED: "1",
        },
      },
    );

    let output = "";
    child.stdout.on("data", (chunk) => {
      output += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      output += chunk.toString();
    });

    child.on("close", (code) => {
      runState.running = false;
      runState.finishedAt = new Date().toISOString();
      runState.exitCode = code;
      runState.message =
        code === 0
          ? "Pipeline finished successfully"
          : "Pipeline finished with errors";
      runState.output = output;
    });

    child.on("error", (error) => {
      runState.running = false;
      runState.finishedAt = new Date().toISOString();
      runState.exitCode = 1;
      runState.message = error.message;
    });

    response.statusCode = 202;
    response.setHeader("Content-Type", "application/json; charset=utf-8");
    response.end(JSON.stringify({ started: true, runState }));
    return true;
  };
}

function runStatusEndpoint(pathname) {
  return async (request, response) => {
    if (!request.url?.startsWith(pathname)) return false;
    response.statusCode = 200;
    response.setHeader("Content-Type", "application/json; charset=utf-8");
    response.end(JSON.stringify(runState));
    return true;
  };
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: "pipeline-artifact-api",
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          const handlers = [
            jsonEndpoint("/api/report", reportPath),
            textEndpoint("/api/test-code", testCodePath),
            runPipelineEndpoint("/api/run"),
            runStatusEndpoint("/api/run-status"),
          ];

          for (const handler of handlers) {
            if (await handler(request, response)) return;
          }

          next();
        });
      },
    },
  ],
  server: {
    fs: {
      allow: [repoRoot],
    },
  },
});
