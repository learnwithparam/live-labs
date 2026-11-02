// Parse every ```mermaid block in the Markdown files named on the command line with the pinned
// mermaid build, in the Chromium shell playwright-core installs. A block GitHub would refuse to
// render fails make check.
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright-core';

const files = process.argv.slice(2).filter((f) => f.endsWith('.md'));
const blocks = files.flatMap((f) =>
  [...readFileSync(f, 'utf8').matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m, i) => ({ where: `${f} block ${i + 1}`, code: m[1] })));

const browser = await chromium.launch();
const page = await browser.newPage();
await page.addScriptTag({ path: new URL('../node_modules/mermaid/dist/mermaid.min.js', import.meta.url).pathname });
let bad = 0;
for (const b of blocks) {
  const err = await page.evaluate(async (code) => {
    try { await window.mermaid.parse(code); return null; } catch (e) { return String(e.message || e).split('\n').slice(0, 4).join('\n'); }
  }, b.code);
  if (err) { bad++; console.log(`${b.where}: ${err}`); }
}
await browser.close();
console.log(`mermaid: ${bad} problems in ${blocks.length} blocks`);
process.exit(bad ? 1 : 0);
