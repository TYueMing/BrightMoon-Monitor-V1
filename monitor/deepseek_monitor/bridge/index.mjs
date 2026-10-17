/** Harness bundle entry; uses the already-running Host and its arbitration. */
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {existsSync} from 'node:fs';
import {InteractionBroker} from './broker.mjs';
import {startServer} from './server.mjs';

export const name = 'brightmoon-deepseek-monitor-bridge';
export const inject = ['typertGateway', 'agents', 'userQuestions', 'sessionProjections'];
export async function apply(ctx) {
  let configured = process.env.DSH_HOME?.trim() || path.join(os.homedir(), '.dsh');
  if (configured === '~') configured = os.homedir();
  else if (/^~[\\/]/.test(configured)) configured = path.join(os.homedir(), configured.slice(2));
  const home = path.resolve(configured);
  // Release bundles are immutable. The marker is added by the release spec;
  // source checkouts retain their existing project-local bridge directory.
  const installed = existsSync(new URL('../installed-layout.json', import.meta.url));
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  const directory = installed
    ? path.join(local, 'MonitorSolo', 'data', 'runtime', 'deepseek')
    : fileURLToPath(new URL('../runtime/', import.meta.url));
  const broker = new InteractionBroker(ctx, home);
  if (!broker.supported()) throw new Error('BrightMoon bridge: unsupported Harness interaction gateway (verified: 0.2.0-rc.2)');
  let server;
  ctx.effect(() => async () => {
    if (server) await server.close();
    await broker.close();
  }, 'brightmoon: interaction bridge');
  broker.start();
  try { server = await startServer(broker, directory, path.join(home, 'sessions')); }
  catch (error) { await broker.close(); throw error; }
}
