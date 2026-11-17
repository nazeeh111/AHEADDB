import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Exercise the shipped handler with an unresolved, then unconfirmed source edit.
// A response can be lost after commit, so old estimates must disappear at submit.
const elements = new Map();
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
let rejectEdit;
const context = vm.createContext({
  document: {
    getElementById: element, querySelector: element,
    createElement: () => ({value: '', textContent: ''}),
    addEventListener() {},
  },
  performance, Date, setTimeout: () => 1, clearTimeout() {},
  fetch: async (url) => {
    if (url === '/orders') return await new Promise((_, reject) => { rejectEdit = reject; });
    return {ok: true, json: async () => data};
  },
});
const html = readFileSync(new URL('../examples/live_desk/index.html', import.meta.url), 'utf8');
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
await new Promise(setImmediate);
assert.equal(element('return').textContent, '50.0%');
assert.equal(element('revenue').textContent, '100');
const submission = element('order-form').listeners.submit({preventDefault() {}});
const during = {value: element('return').textContent, disabled: element('add').disabled};
rejectEdit(new Error('Order was not confirmed. Check the source API before retrying.'));
await submission;
assert.equal(during.value, 'Unavailable', 'hide estimates while the edit is unconfirmed');
assert.equal(during.disabled, true, 'prevent a duplicate submission');
assert.equal(element('return').textContent, 'Unavailable');
assert.equal(element('revenue').textContent, 'Unavailable');
assert.equal(element('add').disabled, false, 'retain known customers for source entry');
assert.match(element('message').textContent, /not confirmed/);
assert.match(element('boundary').textContent, /not been verified/);
console.log('Shipped Live Desk handler: pending and unconfirmed edits hide old estimates.');
