// DOM-independent checks of the script shipped inside template.html. No browser or network.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '..', 'template.html'), 'utf8');
const article = {key: 'example', title: 'Example', version: 1, sourceHash: 'a'.repeat(64),
  blocks: [{p: 0, kind: 'p', text: 'Hello 😀 world'}, {p: 1, kind: 'p', text: 'Second paragraph'}],
  prevBlocks: [], changes: []};
function node(id = '') {
  const queries = new Map();
  return { id, value: '', hidden: false, innerHTML: '', textContent: '', dataset: {}, children: [], handlers: {}, validity: '',
    addEventListener(name, fn) {this.handlers[name] = fn;},
    querySelector(q) {if (!queries.has(q)) queries.set(q, {...node(), parentElement: node()}); return queries.get(q);},
    append(el) {this.children.push(el);}, setAttribute() {}, remove() {}, click() {}, scrollIntoView() {},
    setCustomValidity(s) {this.validity = s;}, reportValidity() {}, contains() {return false;},
  };
}
function setup({saved, storageFails = false} = {}) {
  const elements = new Map();
  const $ = (id) => {if (!elements.has(id)) elements.set(id, node(id)); return elements.get(id);};
  let stored = saved, download;
  const context = vm.createContext({
    document: { getElementById: $, createElement: node, body: node(), addEventListener() {}, querySelector(selector) {
      if (selector === '.replacement:invalid') return $('marks').children.map((li) => li.querySelector('.replacement')).find((input) => input.validity) || null;
      return null;
    } },
    window: {getSelection: () => ({removeAllRanges() {}})},
    localStorage: {getItem() {if (storageFails) throw Error('blocked'); return stored;},
      setItem(key, value) {if (storageFails) throw Error('quota'); stored = value;}},
    URL: {createObjectURL(blob) {download = blob; return 'blob:test';}, revokeObjectURL() {}},
    Blob, setTimeout() {}, clearTimeout() {}, console,
  });
  const script = template.match(/<script>([\s\S]*)<\/script>/)[1]
    .replace('/*DATA*/null', JSON.stringify(article))
    .replace(/  render\(\);\n\}\)\(\);\s*$/, `  globalThis.api = {validateEnvelope, envelope, persist, addPending, removeMark,
      getMarks: () => marks, setPending: (p) => {pending = p;}, storageKey: LSKEY};
  render();\n})();`);
  vm.runInContext(script, context);
  return {api: context.api, $, stored: () => stored, downloaded: () => download};
}
const mark = (extra = {}) => ({id: 'm_one', v: 1, p: 0, start: 0, end: 5, quote: 'Hello', reason: '', ...extra});
const serialized = (value) => JSON.parse(JSON.stringify(value));

test('accepts UTF-16 emoji offsets and preserves exact replacement and hostile plain text', () => {
  const {api} = setup();
  const items = [mark({start: 6, end: 8, quote: '😀', reason: '<img onerror="bad">', replace: ' Exact text  '})];
  assert.deepEqual(serialized(api.validateEnvelope(api.envelope(items))), items);
  assert.match(api.storageKey, /:v1:a{64}$/);
});

test('rejects mismatched article identity and encoding', () => {
  const {api} = setup();
  for (const change of [{schema:'wrong'}, {key:'other'}, {version:2}, {sourceHash:'b'.repeat(64)}, {offsetEncoding:'codepoint'}, {marks:null}]) {
    assert.throws(() => api.validateEnvelope({...api.envelope([mark()]), ...change}));
  }
});

test('rejects malformed, forged, duplicate, overlapping, and split-surrogate ranges', () => {
  const {api} = setup();
  for (const change of [{id:'x" onclick="bad'}, {id:''}, {v:2}, {p:90}, {p:0.5}, {start:-1}, {end:99},
      {end:0}, {quote:'other'}, {reason:42}, {replace:null}, {start:6,end:7,quote:'\ud83d'}]) {
    assert.throws(() => api.validateEnvelope(api.envelope([mark(change)])));
  }
  assert.throws(() => api.validateEnvelope(api.envelope([mark(), mark()])));
  assert.throws(() => api.validateEnvelope(api.envelope([mark(), mark({id:'m_two',start:4,end:8,quote:'o 😀'})])));
  assert.equal(api.validateEnvelope(api.envelope([mark(), mark({id:'m_two',start:5,end:8,quote:' 😀'})])).length, 2);
});

test('adds, immediately saves, downloads latest edits, and immediately saves undo', async () => {
  const app = setup();
  app.$('selType').value = 'comment';
  app.$('selReason').value = 'First note';
  app.api.setPending([{p:0,start:0,end:5,quote:'Hello'}]);
  app.api.addPending();
  assert.equal(JSON.parse(app.stored()).marks[0].reason, 'First note');
  const item = app.$('marks').children.at(-1);
  const input = item.querySelector('.reason');
  input.value = 'Edited immediately before export'; input.handlers.input();
  app.$('exportMarks').handlers.click();
  const exported = JSON.parse(await app.downloaded().text());
  assert.equal(exported.marks[0].reason, input.value);
  assert.equal(exported.offsetEncoding, 'utf16');
  app.api.removeMark(exported.marks[0].id);
  assert.equal(JSON.parse(app.stored()).marks.length, 0);
});

test('storage errors are visible and export continues to work', async () => {
  const app = setup({storageFails:true});
  app.$('selType').value = 'delete';
  app.api.setPending([{p:0,start:0,end:5,quote:'Hello'}]); app.api.addPending();
  assert.match(app.$('saveStatus').textContent, /保存失败/);
  app.$('exportMarks').handlers.click();
  assert.equal(JSON.parse(await app.downloaded().text()).marks[0].replace, '');
});

test('invalid imports are atomic; valid imports restore a full round', async () => {
  const app = setup();
  const original = app.api.envelope([mark()]);
  app.$('importFile').files = [{size:200, text:async () => JSON.stringify(original)}];
  await app.$('importFile').handlers.change();
  assert.equal(app.api.getMarks().length, 1);
  const before = app.stored();
  app.$('importFile').files = [{size:200, text:async () => JSON.stringify({...original, marks:[mark({quote:'Forged'})]})}];
  await app.$('importFile').handlers.change();
  assert.equal(app.stored(), before);
  assert.equal(app.api.getMarks()[0].quote, 'Hello');
  assert.match(app.$('status').textContent, /现有批注未改变/);
  const restored = setup({saved:before});
  assert.equal(restored.api.getMarks()[0].id, 'm_one');
});

test('overlap and multi-paragraph literal replacement do not modify current marks', () => {
  const app = setup({saved: JSON.stringify({...article, schema:'draft-markup/marks-v1', offsetEncoding:'utf16', marks:[mark()]})});
  app.$('selType').value = 'comment';
  app.api.setPending([{p:0,start:1,end:3,quote:'el'}]); app.api.addPending();
  assert.equal(app.api.getMarks().length, 1);
  assert.match(app.$('status').textContent, /重叠/);
  app.$('selType').value = 'replace'; app.$('selReplace').value = 'Replacement';
  app.api.setPending([{p:0,start:8,end:14,quote:' world'},{p:1,start:0,end:6,quote:'Second'}]); app.api.addPending();
  assert.equal(app.api.getMarks().length, 1);
  assert.match(app.$('status').textContent, /逐段/);
});

test('template has no vendor, remote-font, network, or debounce persistence dependency', () => {
  assert.doesNotMatch(template, /window\.claude|\bGemini\b|\bClaude\b|fonts\.googleapis|\bfetch\(|\bXMLHttpRequest\b|db\.doc|setTimeout\(\(\) => persist/);
  assert.match(template, /\.textContent = "「" \+ m\.quote/);
  assert.match(template, /data-id="\$\{esc\(mk\[k\]\)\}"/);
});

test('unfinished exact replacement cannot silently become deletion or be exported', () => {
  const app = setup({saved: JSON.stringify({...article, schema:'draft-markup/marks-v1', offsetEncoding:'utf16', marks:[mark()]})});
  const li = app.$('marks').children.at(-1), type = li.querySelector('select'), input = li.querySelector('.replacement');
  type.value = 'replace'; type.handlers.change();
  assert.equal(app.api.getMarks()[0].replace, 'Hello');
  assert.equal(input.value, 'Hello');
  input.value = 'New literal'; input.handlers.input();
  input.value = ''; input.handlers.input();
  assert.equal(app.api.getMarks()[0].replace, 'New literal');
  assert.equal(JSON.parse(app.stored()).marks[0].replace, 'New literal');
  app.$('exportMarks').handlers.click();
  assert.equal(app.downloaded(), undefined);
  assert.match(app.$('status').textContent, /替换文本为空/);
  const restored = setup({saved: app.stored()});
  assert.equal(restored.api.getMarks()[0].replace, 'New literal');
  type.value = 'delete'; type.handlers.change();
  assert.equal(app.api.getMarks()[0].replace, '');
  assert.equal(input.validity, '');
});
