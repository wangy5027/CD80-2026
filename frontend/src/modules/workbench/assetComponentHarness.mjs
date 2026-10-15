import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { compileScript, parse } from '@vue/compiler-sfc';
import { createRenderer, nextTick } from 'vue';

// Vue's text-model directive checks these native types even with a custom renderer.
globalThis.Document ||= class Document {};
globalThis.ShadowRoot ||= class ShadowRoot {};

const vueUrl = pathToFileURL(join(dirname(createRequire(import.meta.url).resolve('vue/package.json')), 'index.mjs')).href;
const cache = new Map();
async function compile(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const { descriptor } = parse(await readFile(url, 'utf8'), { filename: url.pathname });
  let code = compileScript(descriptor, { id: 'asset-component-test', inlineTemplate: true }).content;
  const imports = [...code.matchAll(/from (['"])([^'"]+)\1/g)];
  for (const match of imports) {
    let destination = match[2] === 'vue' ? vueUrl : match[2];
    if (destination.startsWith('.')) {
      const target = new URL(destination, url);
      destination = target.pathname.endsWith('.vue') ? await compile(target) : target.href;
    }
    code = code.replace(match[0], 'from ' + JSON.stringify(destination));
  }
  const result = 'data:text/javascript;base64,' + Buffer.from(code).toString('base64');
  cache.set(url.href, result); return result;
}
export async function component(path) { return (await import(await compile(new URL(path, import.meta.url)))).default; }
export function hostNode(tag, text = '') {
  const listeners = new Map();
  return { tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null, value: '', checked: false, selected: false,
    get options() { return this.children.filter(child => child.tag === 'option'); },
    getRootNode() { return globalThis.document || { activeElement: null }; },
    addEventListener(type, handler) { listeners.set(type, handler); }, removeEventListener(type) { listeners.delete(type); },
    dispatchEvent(event) { return listeners.get(event.type)?.({ target: this, preventDefault() {}, stopPropagation() {} }); },
    getAttribute(name) { return this.props[name]; }, setAttribute(name, value) { this.props[name] = value; }, removeAttribute(name) { delete this.props[name]; },
  };
}
export const renderer = createRenderer({
  createElement: tag => hostNode(tag), createText: text => hostNode('#text', text), createComment: text => hostNode('#comment', text),
  setText: (node, text) => { node.text = text; }, setElementText: (node, text) => { node.text = text; node.children = []; },
  patchProp: (node, key, _previous, value) => { node.props[key] = value; if (!['children','parent','tag'].includes(key)) node[key] = value; },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  insert(node, parent, anchor = null) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = parent; parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, node); },
  remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = null; },
});
export function find(node, predicate) { if (predicate(node)) return node; for (const child of node.children) { const found = find(child, predicate); if (found) return found; } }
export function all(node, predicate) { return [...(predicate(node) ? [node] : []), ...node.children.flatMap(child => all(child, predicate))]; }
export function content(node) { return node.text + node.children.map(content).join(''); }
export const flush = async () => { await new Promise(resolve => setImmediate(resolve)); await nextTick(); };
export const submitEvent = () => ({ preventDefault() {}, stopPropagation() {} });
