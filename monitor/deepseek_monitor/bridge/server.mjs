/** Loopback transport. A per-launch credential belongs only to this bridge. */
import http from 'node:http';
import {createHash, randomBytes, timingSafeEqual} from 'node:crypto';
import {mkdir, writeFile, rename, readFile, unlink, chmod, realpath} from 'node:fs/promises';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {BridgeError} from './broker.mjs';

const exec = promisify(execFile);
const LIMIT = 512 * 1024;
export async function canonical(value) {
  let resolved = path.resolve(value);
  try { resolved = await realpath(resolved); } catch { /* sessions may not exist yet */ }
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}
async function secureDirectory(directory) {
  await mkdir(directory, {recursive: true, mode: 0o700});
  if (process.platform !== 'win32') { await chmod(directory, 0o700); return; }
  const {stdout} = await exec('whoami.exe', ['/user', '/fo', 'csv', '/nh'], {windowsHide: true});
  const sid = stdout.match(/S-1-5-[0-9-]+/)?.[0];
  if (!sid) throw new Error('Cannot determine bridge file owner');
  await exec('icacls.exe', [directory, '/inheritance:r', '/grant:r', `*${sid}:(OI)(CI)F`], {windowsHide: true});
}
async function body(req) {
  const chunks = []; let count = 0;
  for await (const chunk of req) {
    count += chunk.length;
    if (count > LIMIT) throw new BridgeError('TOO_LARGE', '交互内容超过限制');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
function send(res, status, value) {
  const data = JSON.stringify(value);
  res.writeHead(status, {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'content-length': Buffer.byteLength(data)});
  res.end(data);
}

export async function startServer(broker, directory, sessionsRoot) {
  await secureDirectory(directory);
  const root = await canonical(sessionsRoot);
  const key = createHash('sha256').update(root).digest('hex').slice(0, 24);
  const filename = path.join(directory, `${key}.json`);
  const token = randomBytes(32).toString('hex');
  const expected = Buffer.from(`Bearer ${token}`);
  let port;
  const server = http.createServer(async (req, res) => {
    const supplied = Buffer.from(req.headers.authorization || '');
    if (req.headers.origin || req.headers.host !== `127.0.0.1:${port}` || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      send(res, 403, {ok: false, error: {code: 'UNAUTHORIZED', message: '连接身份无效'}}); return;
    }
    if (req.method !== 'POST' || !['/v1/snapshot', '/v1/respond'].includes(req.url)
        || req.headers['content-type'] !== 'application/json') {
      send(res, 404, {ok: false, error: {code: 'BAD_ROUTE', message: '接口不存在'}}); return;
    }
    try {
      const value = await body(req);
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid payload');
      let result;
      if (req.url === '/v1/snapshot') {
        if (!/^[a-zA-Z0-9-]{8,80}$/.test(value.client_id || '') || !Array.isArray(value.holds)
            || value.holds.some(id => typeof id !== 'string')) throw new Error('invalid snapshot');
        result = broker.snapshot(value.client_id, value.holds);
      } else result = await broker.respond(value);
      send(res, 200, {ok: true, value: result});
    } catch (error) {
      send(res, error instanceof BridgeError ? 409 : 400, {ok: false, error: {
        code: error instanceof BridgeError ? error.code : 'BAD_REQUEST',
        message: error instanceof BridgeError ? error.message : '请求格式无效',
      }});
    }
  });
  server.requestTimeout = 3000;
  server.headersTimeout = 3000;
  server.timeout = 4000;
  await new Promise((resolve, reject) => {
    server.once('error', reject); server.listen(0, '127.0.0.1', resolve);
  });
  port = server.address().port;
  const descriptor = {protocol: 1, port, token, instance: broker.instance, sessions_root: root, pid: process.pid};
  const temporary = `${filename}.${broker.instance}.tmp`;
  try {
    // Never replace another live Host owning the same Harness home.
    let previous;
    try { previous = JSON.parse(await readFile(filename, 'utf8')); } catch { /* absent or stale descriptor */ }
    if (previous?.pid && previous.pid !== process.pid) {
      let live = true;
      try { process.kill(previous.pid, 0); } catch (error) { live = error.code !== 'ESRCH'; }
      if (live) throw new Error('Another bridge already owns this Harness home');
    }
    await writeFile(temporary, JSON.stringify(descriptor), {mode: 0o600, flag: 'wx'});
    await rename(temporary, filename);
  } catch (error) {
    server.close(); await unlink(temporary).catch(() => {}); throw error;
  }
  // Leases expire even if the monitor disappears and stops polling entirely.
  const timer = setInterval(() => { try { broker.refresh(); } catch { /* unavailable service */ } }, 750);
  timer.unref();
  return {
    descriptor,
    async close() {
      clearInterval(timer);
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
      try {
        const current = JSON.parse(await readFile(filename, 'utf8'));
        if (current.instance === broker.instance) await unlink(filename);
      } catch { /* only remove our own descriptor */ }
    },
  };
}
