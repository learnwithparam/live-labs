// `npm run listing -- write <product>`, `review` and `serve`: the lab from a terminal.
import { mkdirSync, writeFileSync } from 'node:fs'
import { load } from './catalogue.js'
import { settings } from './config.js'
import { mastraAsk } from './llm.js'
import { audit } from './audit.js'
import { onePrompt } from './onePrompt.js'
import { review, summary } from './review.js'
import { serve } from './serve.js'
import { write, type Listing } from './workflow.js'

const usage = 'usage: npm run listing -- write <product> [--one-prompt] | review | serve [--port 8000]'

function show(listing: Listing): void {
  for (const item of listing.items) {
    const mark = item.ok ? ' ' : 'x'
    const cites = item.facts.length ? `  [${item.facts.join(', ')}]` : ''
    console.log(`  ${mark} ${item.part.padEnd(11)} ${item.text}${cites}`)
    if (!item.ok && item.why) console.log(`      why: ${item.why}`)
  }
}

async function main(): Promise<number> {
  const [command, ...rest] = process.argv.slice(2)
  const products = load()
  if (command === 'write') {
    const product = products.find((p) => p.id === rest[0])
    if (!product) {
      console.log(`Pick a product: ${products.map((p) => p.id).join(', ')}`)
      return 2
    }
    const ask = mastraAsk(settings())
    const listing = rest.includes('--one-prompt')
      ? await onePrompt(ask, product.sheet)
      : await write(ask, product.sheet, (e) => console.log(`${String(e.ms).padStart(6)} ms  ${e.phase === 'start' ? 'start' : 'done '}  ${e.step}`))
    console.log(`\n${product.product}: ${listing.status}. ${listing.why}`)
    show(listing)
    const edits = audit(listing, product)
    console.log(`\nMerchant audit: ${edits.length ? `${edits.length} edit(s) needed` : 'nothing to edit'}`)
    for (const e of edits) console.log(`  - ${e.why}  (${e.says})`)
    console.log(`Cost: $${listing.cost.toFixed(5)}`)
    return 0
  }
  if (command === 'review') {
    const rows = await review(mastraAsk(settings()), products, (r) =>
      console.log(`${r.how.padEnd(10)} ${r.id.padEnd(20)} ${r.clean ? 'clean' : r.listing.status === 'held' ? 'held' : `live, ${r.edits.length} edit(s)`}`),
    )
    console.log('')
    for (const s of [summary(rows, 'one-prompt'), summary(rows, 'workflow')]) {
      console.log(`${s.how.padEnd(10)} published without an edit: ${s.clean} of ${s.total}, held ${s.held}, live with errors ${s.wrong}, $${s.cost.toFixed(4)}`)
    }
    mkdirSync('out', { recursive: true })
    writeFileSync('out/review.json', JSON.stringify(rows, null, 2))
    console.log('Every listing and edit: out/review.json')
    return 0
  }
  if (command === 'serve') {
    const port = Number(rest[rest.indexOf('--port') + 1]) || 8000
    serve(products, mastraAsk(settings()), port)
    console.log(`Northbeck product import on http://127.0.0.1:${port}  (Ctrl-C to stop)`)
    return -1
  }
  console.log(usage)
  return 2
}

main().then((code) => {
  if (code >= 0) process.exit(code)
})
