import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import process from 'node:process';

const require = createRequire(import.meta.url);
const playwrightCli = require.resolve('@playwright/test/cli');
const forwardedArgs = process.argv.slice(2);
const externalBaseURL = process.env.YIGDESK_BASE_URL;
const port = process.env.YIGDESK_E2E_PORT || '8791';
const baseURL = externalBaseURL || `http://127.0.0.1:${port}`;
let serverProcess;
let testProcess;
let shuttingDown = false;

function runProcess(command, args, options) {
  const child = spawn(command, args, options);
  return {
    child,
    completed: new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => {
        resolve(code ?? (signal ? 1 : 0));
      });
    }),
  };
}

async function waitForHealth(url, child, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error('Yigdesk E2E server exited before becoming ready.');
    }
    try {
      const response = await fetch(`${url}/api/health`);
      if (response.ok) return;
    } catch {
      // The direct child is still starting; retry until the bounded deadline.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Yigdesk E2E server did not become ready within ${timeoutMs}ms.`);
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, 'exit');
  child.kill();
  await Promise.race([
    exited,
    new Promise((resolve) => setTimeout(resolve, 5_000)),
  ]);
  if (child.exitCode === null && child.signalCode === null && process.platform !== 'win32') {
    child.kill('SIGKILL');
  }
}

async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  if (testProcess && testProcess.exitCode === null && testProcess.signalCode === null) {
    testProcess.kill(signal);
  }
  await stopChild(serverProcess);
  process.exit(signal === 'SIGINT' ? 130 : 143);
}

process.once('SIGINT', () => void shutdown('SIGINT'));
process.once('SIGTERM', () => void shutdown('SIGTERM'));

let exitCode = 1;
try {
  const python = process.env.YIGDESK_PYTHON || 'python';
  const generatedSample = runProcess(
    python,
    ['-m', 'scripts.generate_sample_workbook', '--output', 'runtime/e2e-upload-sample.xlsx'],
    {
      cwd: process.cwd(),
      env: process.env,
      shell: false,
      stdio: ['ignore', 'inherit', 'inherit'],
      windowsHide: true,
    },
  );
  if (await generatedSample.completed !== 0) {
    throw new Error('Could not generate the synthetic E2E upload workbook.');
  }
  if (!externalBaseURL) {
    const launched = runProcess(python, ['-m', 'yigdesk.app'], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        HOST: '127.0.0.1',
        PORT: port,
        YIGDESK_RUNTIME: process.env.YIGDESK_RUNTIME || 'runtime/e2e',
      },
      shell: false,
      stdio: ['ignore', 'inherit', 'inherit'],
      windowsHide: true,
    });
    serverProcess = launched.child;
    await waitForHealth(baseURL, serverProcess);
  }

  const launchedTests = runProcess(
    process.execPath,
    [playwrightCli, 'test', ...forwardedArgs],
    {
      cwd: process.cwd(),
      env: { ...process.env, YIGDESK_BASE_URL: baseURL },
      shell: false,
      stdio: 'inherit',
      windowsHide: true,
    },
  );
  testProcess = launchedTests.child;
  exitCode = await launchedTests.completed;
} catch (error) {
  const message = error instanceof Error ? error.message : 'Unknown E2E runner failure.';
  console.error(`[Yigdesk E2E] ${message}`);
} finally {
  await stopChild(serverProcess);
}

process.exitCode = exitCode;
