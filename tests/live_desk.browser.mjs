import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Execute the shipped script. Transport and storage are controlled fixtures;
// actual PostgreSQL receipts and native browser checks are separate gates.
const html = readFileSync(process.argv[2] ?? new URL('../examples/live_desk/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const key = 'signaldesk-pending-order-v1';
let nextId = 0;
function controller(storage = new Map(), storageFails = false, apiOffline = false) {
  const elements = new Map(), sent = [], replies = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', value: '', checked: false, disabled: false, dataset: {},
      listeners: {}, children: [],
      addEventListener(name, fn) { this.listeners[name] = fn; },
      replaceChildren(...children) {
        this.children = children;
        if (id === 'customer') this.value = children[0]?.value ?? '';
      },
      append(child) { this.children.push(child); },
    });
    return elements.get(id);
  }
  element('amount').value = '450';
  const feature = (name, prediction) => ({
    task: {name, horizon_days: 14}, predictions: {C0000: prediction},
    coverage: {revision: 1, state: 'fresh', source_as_of: 'fixture'},
    prediction_cutoff: '2024-07-15',
  });
  const data = {features: [feature('return_14d', .5), feature('revenue_14d', 100)],
    status: {revision: 1, clock_unknown: false},
    covers_observed_head: true, same_source_boundary: true};
  const context = vm.createContext({
    document: {getElementById: element, querySelector: element,
      createElement: () => ({value: '', textContent: ''}), addEventListener() {}},
    sessionStorage: {
      getItem(k) { if (storageFails) throw Error('Storage unavailable'); return storage.get(k) ?? null; },
      setItem(k, value) { if (storageFails) throw Error('Storage unavailable'); storage.set(k, value); },
      removeItem(k) { storage.delete(k); },
    },
    crypto: {randomUUID: () => '00000000-0000-4000-8000-' + String(++nextId).padStart(12, '0')},
    performance, Date, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options) => {
      if (url === '/orders') {
        const body = JSON.parse(options.body);
        // Persistence must precede sending, even if the transport never resolves.
        assert.deepEqual(JSON.parse(storage.get(key)), body);
        sent.push(body);
        return await new Promise((resolve, reject) => replies.push({resolve, reject}));
      }
      if (apiOffline) throw Error("Prediction service unavailable");
      return {ok: true, json: async () => data};
    },
  });
  vm.runInContext(script, context);
  return {element, sent, replies, storage,
    ready: async () => await new Promise(setImmediate),
    submit: () => element('order-form').listeners.submit({preventDefault() {}}),
    reply(status, body) { replies.shift().resolve({ok: status === 200, status, json: async () => body}); },
  };
}
const first = controller();
await first.ready();
assert.equal(first.element('return').textContent, '50.0%');
assert.equal(first.element('revenue').textContent, '100');
const submission = first.submit();
assert.equal(first.element('return').textContent, 'Unavailable', 'hide estimates while the edit is unconfirmed');
assert.equal(first.element('add').disabled, true, 'prevent a duplicate in-flight submission');
assert.equal(first.element('customer').disabled, true);
assert.equal(first.element('amount').disabled, true);
const original = first.sent[0];
first.replies.shift().reject(new Error('Order was not confirmed.'));
await submission;
assert.equal(first.element('return').textContent, 'Unavailable');
assert.equal(first.element('revenue').textContent, 'Unavailable');
assert.equal(first.element('add').disabled, false, 'keep source-entry retry available');
assert.match(first.element('add').textContent, /Retry saved order/);
assert.match(first.element('message').textContent, /not confirmed/);
assert.match(first.element('boundary').textContent, /not been verified/);
assert.deepEqual(JSON.parse(first.storage.get(key)), original);


const offlineReload = controller(new Map([[key, JSON.stringify(original)]]), false, true);
await offlineReload.ready();
assert.equal(offlineReload.element('customer').value, original.entity, 'show the saved customer when estimates are offline');
assert.equal(offlineReload.element('customer').disabled, true);
assert.equal(offlineReload.element('add').disabled, false);

// A fresh execution models reloading the same tab with its existing session data.
const reloaded = controller(first.storage);
await reloaded.ready();
assert.equal(reloaded.element('customer').disabled, true);
const retry = reloaded.submit();
assert.deepEqual(reloaded.sent[0], original, 'reload retries the identical caller intent');
reloaded.reply(200, {request_id: original.request_id, order: 'LIVE-' + original.request_id, revision: 2});
await retry;
assert.equal(reloaded.storage.has(key), false);
assert.equal(reloaded.element('add').textContent, 'Record order');
assert.equal(reloaded.element('customer').disabled, false);
const fresh = reloaded.submit();
assert.notEqual(reloaded.sent[1].request_id, original.request_id, 'a second deliberate order is distinct');
reloaded.reply(422, {error: 'Order rejected'});
await fresh;
assert.equal(reloaded.storage.has(key), false, 'definitive rejection permits correction');
assert.equal(reloaded.element('amount').disabled, false);

for (const outcome of ['conflict', 'mismatch']) {
  const app = controller(); await app.ready(); const operation = app.submit();
  if (outcome === 'conflict') app.reply(409, {error: 'Saved order conflicts'});
  else app.reply(200, {request_id: 'different', revision: 2});
  await operation;
  assert.equal(app.storage.has(key), true, outcome + ' must retain the original intent');
  assert.equal(app.element('amount').disabled, true);
  assert.match(app.element('add').textContent, /Retry saved order/);
}
for (const app of [controller(new Map(), true), controller(new Map([[key, '{broken']]))]) {
  await app.ready(); await app.submit();
  assert.equal(app.sent.length, 0, 'unreadable saved state cannot start another order');
  assert.equal(app.element('add').disabled, true);
}
console.log('Shipped Live Desk: pending/ambiguous hiding, saved intent/reload/exact retry, new intent, rejection/conflict, matching receipt and storage refusal pass.');
