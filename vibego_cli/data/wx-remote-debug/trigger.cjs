#!/usr/bin/env node
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const RESULT_PREFIX = 'VIBEGO_WX_REMOTE_DEBUG_RESULT:';
const ERROR_PREFIX = 'VIBEGO_WX_REMOTE_DEBUG_ERROR:';
const CONNECTION_EVIDENCE = 'Tool.onRemoteDebugConnected';
const SOURCE_DIR = __dirname;
const SOURCE_PACKAGE_JSON = path.join(SOURCE_DIR, 'package.json');
const SOURCE_PACKAGE_LOCK = path.join(SOURCE_DIR, 'package-lock.json');
const PACKAGE_MANIFEST = JSON.parse(fs.readFileSync(SOURCE_PACKAGE_JSON, 'utf8'));
const RUNTIME_VERSION = PACKAGE_MANIFEST.dependencies['miniprogram-automator'];

class RemoteDebugError extends Error {
  constructor(message, exitCode = 1, stage = 'unknown') {
    super(message);
    this.name = 'RemoteDebugError';
    this.exitCode = exitCode;
    this.stage = stage;
  }
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) {
      throw new RemoteDebugError(`无法识别参数：${token}`, 2, 'arguments');
    }
    const value = argv[index + 1];
    if (!value || value.startsWith('--')) {
      throw new RemoteDebugError(`参数 ${token} 缺少值`, 2, 'arguments');
    }
    result[token.slice(2)] = value;
    index += 1;
  }
  return result;
}

function positiveInteger(value, fallback, name) {
  if (value === undefined || value === null || String(value).trim() === '') return fallback;
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new RemoteDebugError(`${name} 必须为正整数`, 2, 'configuration');
  }
  return parsed;
}

function assertSupportedNodeVersion() {
  const major = Number.parseInt(String(process.versions.node || '').split('.', 1)[0], 10);
  if (!Number.isSafeInteger(major) || major < 16) {
    throw new RemoteDebugError(
      `自动真机调试要求 Node.js >=16，当前版本：${process.versions.node || 'unknown'}`,
      2,
      'configuration',
    );
  }
}

function resolveLocalPath(rawPath) {
  const value = String(rawPath || '').trim();
  if (value === '~') return os.homedir();
  if (value.startsWith('~/')) return path.join(os.homedir(), value.slice(2));
  return path.resolve(value);
}

function resolveConfigDir() {
  if (process.env.MASTER_CONFIG_ROOT) return resolveLocalPath(process.env.MASTER_CONFIG_ROOT);
  if (process.env.VIBEGO_CONFIG_DIR) return resolveLocalPath(process.env.VIBEGO_CONFIG_DIR);
  if (process.env.XDG_CONFIG_HOME) return path.join(resolveLocalPath(process.env.XDG_CONFIG_HOME), 'vibego');
  return path.join(os.homedir(), '.config', 'vibego');
}

function resolveRuntimeRoot(configDir) {
  if (process.env.VIBEGO_RUNTIME_ROOT) return resolveLocalPath(process.env.VIBEGO_RUNTIME_ROOT);
  return path.join(configDir, 'runtime');
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function withTimeout(promise, milliseconds, message, exitCode, stage) {
  let timer = null;
  return Promise.race([
    Promise.resolve(promise),
    new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new RemoteDebugError(message, exitCode, stage)), milliseconds);
    }),
  ]).finally(() => {
    if (timer !== null) clearTimeout(timer);
  });
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function runtimeIsReady(runtimeDir, lockHash) {
  const markerPath = path.join(runtimeDir, '.package-lock.sha256');
  const modulePath = path.join(runtimeDir, 'node_modules', 'miniprogram-automator');
  try {
    return fs.readFileSync(markerPath, 'utf8').trim() === lockHash && fs.statSync(modulePath).isDirectory();
  } catch (_error) {
    return false;
  }
}

function tail(text, maxChars = 4000) {
  const value = String(text || '').trim();
  return value.length <= maxChars ? value : value.slice(value.length - maxChars);
}

function spawnAndWait(command, args, options, timeoutMs) {
  return new Promise((resolve, reject) => {
    let stdout = '';
    let stderr = '';
    let settled = false;
    let child;
    try {
      child = spawn(command, args, { ...options, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      reject(error);
      return;
    }
    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString('utf8');
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString('utf8');
    });
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill('SIGKILL');
      reject(new RemoteDebugError(`npm 依赖安装在 ${timeoutMs}ms 内未完成`, 3, 'dependency-install'));
    }, timeoutMs);
    child.once('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.once('exit', (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code: code === null ? 1 : code, signal, stdout, stderr });
    });
  });
}

async function ensureRuntime(runtimeRoot, installTimeoutMs) {
  if (!fs.existsSync(SOURCE_PACKAGE_LOCK)) {
    throw new RemoteDebugError(`依赖锁文件不存在：${SOURCE_PACKAGE_LOCK}`, 3, 'dependency-install');
  }
  const runtimeParent = path.join(runtimeRoot, 'wx-remote-debug');
  const runtimeDir = path.join(runtimeParent, RUNTIME_VERSION);
  const lockHash = sha256File(SOURCE_PACKAGE_LOCK);
  fs.mkdirSync(runtimeParent, { recursive: true });
  if (runtimeIsReady(runtimeDir, lockHash)) return runtimeDir;

  const installLock = path.join(runtimeParent, `.install-${RUNTIME_VERSION}.lock`);
  let releaseInstallLock = tryAcquireDirectoryLock(installLock);
  if (releaseInstallLock === null) {
    const deadline = Date.now() + installTimeoutMs;
    while (Date.now() < deadline && releaseInstallLock === null) {
      if (runtimeIsReady(runtimeDir, lockHash)) return runtimeDir;
      await sleep(250);
      releaseInstallLock = tryAcquireDirectoryLock(installLock);
    }
    if (releaseInstallLock === null) {
      throw new RemoteDebugError(
        `等待另一个 npm 依赖安装进程超时；安装锁：${installLock}`,
        3,
        'dependency-install',
      );
    }
  }

  const tempDir = path.join(
    runtimeParent,
    `.tmp-${RUNTIME_VERSION}-${process.pid}-${crypto.randomBytes(5).toString('hex')}`,
  );
  try {
    if (runtimeIsReady(runtimeDir, lockHash)) return runtimeDir;
    fs.mkdirSync(tempDir, { recursive: false });
    fs.copyFileSync(SOURCE_PACKAGE_JSON, path.join(tempDir, 'package.json'));
    fs.copyFileSync(SOURCE_PACKAGE_LOCK, path.join(tempDir, 'package-lock.json'));
    const npmBin = process.env.NPM_BIN || 'npm';
    let installResult;
    try {
      installResult = await spawnAndWait(
        npmBin,
        ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'],
        { cwd: tempDir, env: process.env },
        installTimeoutMs,
      );
    } catch (error) {
      throw new RemoteDebugError(`npm 依赖安装失败：${error.message}`, 3, 'dependency-install');
    }
    if (installResult.code !== 0) {
      const diagnostics = tail(installResult.stderr || installResult.stdout) || `退出码 ${installResult.code}`;
      throw new RemoteDebugError(`npm 依赖安装失败：${diagnostics}`, 3, 'dependency-install');
    }
    const installedModule = path.join(tempDir, 'node_modules', 'miniprogram-automator');
    if (!fs.existsSync(installedModule)) {
      throw new RemoteDebugError('npm 返回成功但未生成 miniprogram-automator', 3, 'dependency-install');
    }
    fs.writeFileSync(path.join(tempDir, '.package-lock.sha256'), `${lockHash}\n`, 'utf8');
    fs.rmSync(runtimeDir, { recursive: true, force: true });
    fs.renameSync(tempDir, runtimeDir);
    return runtimeDir;
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
    releaseInstallLock();
  }
}

function processIsAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return Boolean(error && error.code === 'EPERM');
  }
}

function directoryLockIsStale(lockPath) {
  try {
    const owner = JSON.parse(fs.readFileSync(path.join(lockPath, 'owner.json'), 'utf8'));
    return !processIsAlive(Number(owner.pid));
  } catch (_error) {
    try {
      return Date.now() - fs.statSync(lockPath).mtimeMs >= 5000;
    } catch (_statError) {
      return true;
    }
  }
}

function tryAcquireDirectoryLock(lockPath) {
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      fs.mkdirSync(lockPath);
      fs.writeFileSync(
        path.join(lockPath, 'owner.json'),
        JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() }),
        'utf8',
      );
      return () => fs.rmSync(lockPath, { recursive: true, force: true });
    } catch (error) {
      if (!error || error.code !== 'EEXIST') throw error;
      if (attempt === 0 && directoryLockIsStale(lockPath)) {
        fs.rmSync(lockPath, { recursive: true, force: true });
        continue;
      }
      return null;
    }
  }
  return null;
}

function acquireDirectoryLock(lockPath, stage) {
  const release = tryAcquireDirectoryLock(lockPath);
  if (release !== null) return release;
  const message = stage === 'project-lock'
    ? '同一小程序已有自动真机调试任务正在等待连接'
    : `自动真机调试资源锁已被占用：${lockPath}`;
  throw new RemoteDebugError(message, 4, stage);
}

function portIsFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once('error', () => resolve(false));
    server.listen({ host: '127.0.0.1', port, exclusive: true }, () => {
      server.close(() => resolve(true));
    });
  });
}

async function acquireAutomationPort(configDir, excludedPorts = new Set()) {
  const requested = process.env.WX_AUTOMATION_PORT
    ? positiveInteger(process.env.WX_AUTOMATION_PORT, null, 'WX_AUTOMATION_PORT')
    : null;
  const candidates = requested ? [requested] : Array.from({ length: 100 }, (_item, index) => 19420 + index);
  for (const port of candidates) {
    if (port > 65535 || excludedPorts.has(port)) continue;
    const lockPath = path.join(configDir, 'locks', 'wx-remote-debug', 'ports', String(port));
    let release;
    try {
      release = acquireDirectoryLock(lockPath, 'automation-port');
    } catch (error) {
      if (error instanceof RemoteDebugError && !requested) continue;
      throw error;
    }
    if (await portIsFree(port)) return { port, release, requested: requested !== null };
    release();
    if (requested) {
      throw new RemoteDebugError(`自动化 WebSocket 端口已被占用：${port}`, 4, 'automation-port');
    }
  }
  throw new RemoteDebugError('未找到可用的自动化 WebSocket 端口（19420-19519）', 4, 'automation-port');
}

function isAutomationPortConflict(error, port) {
  if (!(error instanceof RemoteDebugError)) return false;
  const message = String(error.message || '');
  return new RegExp(
    `(?:EADDRINUSE|address already in use|port\\s+${port}\\s+is\\s+in\\s+use|端口[^\\n]*${port}[^\\n]*(?:占用|冲突))`,
    'i',
  ).test(message);
}

function startWechatCli(cliPath, projectPath, idePort, automationPort, logPath) {
  const logFd = fs.openSync(logPath, 'a');
  let spawnError = null;
  let resolveSpawned;
  let rejectSpawned;
  const spawned = new Promise((resolve, reject) => {
    resolveSpawned = resolve;
    rejectSpawned = reject;
  });
  const child = spawn(
    cliPath,
    [
      'auto',
      '--project',
      projectPath,
      '--auto-port',
      String(automationPort),
      '--port',
      String(idePort),
    ],
    { stdio: ['ignore', logFd, logFd], detached: false, env: process.env },
  );
  fs.closeSync(logFd);
  child.once('spawn', () => resolveSpawned());
  child.once('error', (error) => {
    spawnError = error;
    rejectSpawned(error);
  });
  return { child, spawned, getSpawnError: () => spawnError };
}

async function stopWechatCli(cliState, timeoutMs = 2000) {
  if (!cliState || !cliState.child) return;
  const child = cliState.child;
  if (child.exitCode !== null || child.signalCode !== null) return;
  if (typeof child.ref === 'function') child.ref();

  const waitForExit = (waitMs) => new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      resolve(true);
      return;
    }
    let timer = null;
    const onExit = () => {
      if (timer !== null) clearTimeout(timer);
      resolve(true);
    };
    child.once('exit', onExit);
    timer = setTimeout(() => {
      child.removeListener('exit', onExit);
      resolve(false);
    }, waitMs);
    timer.unref();
  });

  if (await waitForExit(100)) return;
  try {
    child.kill('SIGTERM');
  } catch (_error) {
    return;
  }
  if (await waitForExit(timeoutMs)) return;
  try {
    child.kill('SIGKILL');
  } catch (_error) {
    return;
  }
  await waitForExit(timeoutMs);
}

async function connectAutomation(automator, wsEndpoint, cliState, cliLogPath, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  try {
    await withTimeout(
      cliState.spawned,
      timeoutMs,
      '微信开发者工具 CLI 进程启动超时',
      5,
      'cli-auto',
    );
  } catch (error) {
    throw new RemoteDebugError(`微信开发者工具 CLI 启动失败：${error.message}`, 5, 'cli-auto');
  }
  await sleep(Math.min(250, timeoutMs));
  while (Date.now() < deadline) {
    const spawnError = cliState.getSpawnError();
    if (spawnError) {
      throw new RemoteDebugError(`微信开发者工具 CLI 启动失败：${spawnError.message}`, 5, 'cli-auto');
    }
    if (cliState.child.exitCode !== null && cliState.child.exitCode !== 0) {
      const diagnostics = fs.existsSync(cliLogPath) ? tail(fs.readFileSync(cliLogPath, 'utf8')) : '';
      throw new RemoteDebugError(
        `微信开发者工具 CLI auto 失败（退出码 ${cliState.child.exitCode}）：${diagnostics || '无输出'}`,
        5,
        'cli-auto',
      );
    }
    try {
      return await automator.connect({ wsEndpoint });
    } catch (error) {
      lastError = error;
      await sleep(250);
    }
  }
  const diagnostics = fs.existsSync(cliLogPath) ? tail(fs.readFileSync(cliLogPath, 'utf8')) : '';
  const reason = diagnostics || (lastError && lastError.message) || 'automation WebSocket 未就绪';
  throw new RemoteDebugError(`自动化连接在 ${timeoutMs}ms 内未就绪：${reason}`, 5, 'automation-connect');
}

async function probeSystemInfo(miniProgram, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    const remaining = Math.max(1, deadline - Date.now());
    try {
      const info = await withTimeout(
        miniProgram.systemInfo(),
        remaining,
        '运行时探测调用超时',
        7,
        'runtime-probe',
      );
      if (info && typeof info === 'object') {
        const platform = String(info.platform || '').trim();
        const system = String(info.system || '').trim();
        if (platform || system) return { info, platform, system };
      }
      lastError = new Error('systemInfo 缺少 platform/system');
    } catch (error) {
      lastError = error;
    }
    if (Date.now() < deadline) await sleep(Math.min(250, deadline - Date.now()));
  }
  throw new RemoteDebugError(
    `运行时探测失败：${(lastError && lastError.message) || '未获得设备信息'}`,
    7,
    'runtime-probe',
  );
}

async function run() {
  assertSupportedNodeVersion();
  const args = parseArgs(process.argv.slice(2));
  if (!args.project || !args['ide-port'] || !args.cli) {
    throw new RemoteDebugError('必须提供 --project、--ide-port 与 --cli', 2, 'arguments');
  }
  const idePort = positiveInteger(args['ide-port'], null, '--ide-port');
  if (idePort > 65535) throw new RemoteDebugError('--ide-port 必须在 1-65535 范围内', 2, 'arguments');
  let projectPath;
  try {
    projectPath = fs.realpathSync(path.resolve(args.project));
  } catch (_error) {
    throw new RemoteDebugError(`小程序目录不存在：${args.project}`, 2, 'arguments');
  }
  if (!fs.statSync(projectPath).isDirectory()) {
    throw new RemoteDebugError(`小程序路径不是目录：${projectPath}`, 2, 'arguments');
  }
  const cliPath = path.resolve(args.cli);
  try {
    fs.accessSync(cliPath, fs.constants.X_OK);
  } catch (_error) {
    throw new RemoteDebugError(`微信开发者工具 CLI 不可执行：${cliPath}`, 2, 'arguments');
  }

  const configDir = resolveConfigDir();
  const runtimeRoot = resolveRuntimeRoot(configDir);
  const installTimeoutMs = positiveInteger(
    process.env.WX_REMOTE_DEBUG_INSTALL_TIMEOUT_MS,
    120000,
    'WX_REMOTE_DEBUG_INSTALL_TIMEOUT_MS',
  );
  const cliTimeoutMs = positiveInteger(
    process.env.WX_REMOTE_DEBUG_CLI_TIMEOUT_MS,
    45000,
    'WX_REMOTE_DEBUG_CLI_TIMEOUT_MS',
  );
  const connectTimeoutMs = positiveInteger(
    process.env.WX_REMOTE_DEBUG_CONNECT_TIMEOUT_MS,
    120000,
    'WX_REMOTE_DEBUG_CONNECT_TIMEOUT_MS',
  );
  const probeTimeoutMs = positiveInteger(
    process.env.WX_REMOTE_DEBUG_PROBE_TIMEOUT_MS,
    10000,
    'WX_REMOTE_DEBUG_PROBE_TIMEOUT_MS',
  );

  const projectHash = crypto.createHash('sha256').update(projectPath).digest('hex');
  const releaseProjectLock = acquireDirectoryLock(
    path.join(configDir, 'locks', 'wx-remote-debug', 'projects', projectHash),
    'project-lock',
  );
  let releasePortLock = null;
  let miniProgram = null;
  let cliState = null;
  let cliLogPath = null;
  let primaryError = null;
  let result = null;
  try {
    const runtimeDir = await ensureRuntime(runtimeRoot, installTimeoutMs);
    const automator = require(path.join(runtimeDir, 'node_modules', 'miniprogram-automator'));
    if (!automator || typeof automator.connect !== 'function') {
      throw new RemoteDebugError('miniprogram-automator 缺少 connect() 接口', 3, 'dependency-load');
    }
    const logDir = path.join(runtimeRoot, 'wx-remote-debug', 'logs');
    fs.mkdirSync(logDir, { recursive: true });
    const excludedPorts = new Set();
    const launchAttempts = process.env.WX_AUTOMATION_PORT ? 1 : 2;
    for (let attempt = 0; attempt < launchAttempts; attempt += 1) {
      const portLease = await acquireAutomationPort(configDir, excludedPorts);
      releasePortLock = portLease.release;
      const wsEndpoint = `ws://127.0.0.1:${portLease.port}`;
      cliLogPath = path.join(logDir, `cli-${process.pid}-${Date.now()}-${attempt}.log`);
      cliState = startWechatCli(cliPath, projectPath, idePort, portLease.port, cliLogPath);
      try {
        miniProgram = await connectAutomation(automator, wsEndpoint, cliState, cliLogPath, cliTimeoutMs);
        break;
      } catch (error) {
        await stopWechatCli(cliState);
        cliState = null;
        releasePortLock();
        releasePortLock = null;
        fs.rmSync(cliLogPath, { force: true });
        cliLogPath = null;
        if (attempt === 0 && !portLease.requested && isAutomationPortConflict(error, portLease.port)) {
          excludedPorts.add(portLease.port);
          continue;
        }
        throw error;
      }
    }
    if (!miniProgram) {
      throw new RemoteDebugError('自动化连接未建立', 5, 'automation-connect');
    }
    await withTimeout(
      miniProgram.remote(true),
      connectTimeoutMs,
      `真机连接超时：${connectTimeoutMs}ms 内未收到 ${CONNECTION_EVIDENCE}；开发者工具可能仍停留在准备态，可手动关闭`,
      6,
      'remote-connect',
    );
    const probe = await probeSystemInfo(miniProgram, probeTimeoutMs);
    result = {
      status: 'success',
      project: projectPath,
      platform: probe.platform,
      system: probe.system,
      model: String(probe.info.model || '').trim(),
      connectionEvidence: CONNECTION_EVIDENCE,
    };
  } catch (error) {
    primaryError = error;
  } finally {
    if (miniProgram && typeof miniProgram.disconnect === 'function') {
      try {
        miniProgram.disconnect();
      } catch (error) {
        if (!primaryError) {
          primaryError = new RemoteDebugError(`释放 automation 连接失败：${error.message}`, 8, 'cleanup');
        }
      }
    }
    await stopWechatCli(cliState);
    if (releasePortLock) releasePortLock();
    releaseProjectLock();
    if (cliLogPath) fs.rmSync(cliLogPath, { force: true });
  }
  if (primaryError) throw primaryError;
  if (!result) throw new RemoteDebugError('自动真机调试未生成结果', 8, 'result');
  process.stdout.write(`${RESULT_PREFIX}${JSON.stringify(result)}\n`);
}

run().catch((error) => {
  const normalized = error instanceof RemoteDebugError
    ? error
    : new RemoteDebugError(error && error.message ? error.message : String(error), 1, 'unexpected');
  process.stderr.write(`[错误] ${normalized.message}\n`);
  process.stderr.write(
    `${ERROR_PREFIX}${JSON.stringify({ status: 'failed', stage: normalized.stage, message: normalized.message })}\n`,
  );
  process.exitCode = normalized.exitCode;
});
