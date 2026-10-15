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
    const product = products.find((candidate) => candidate.id === rest[0])
    if (!product) {
      console.log(`Pick a product: ${products.map((option) => option.id).join(', ')}`)
      return 2
    }
    const ask = mastraAsk(settings())
    const listing = rest.includes('--one-prompt')
      ? await onePrompt(ask, product.sheet)
      : await write(ask, product.sheet, (event) => console.log(`${String(event.ms).padStart(6)} ms  ${event.phase === 'start' ? 'start' : 'done '}  ${event.step}`))
    console.log(`\n${product.product}: ${listing.status}. ${listing.why}`)
    show(listing)
    const edits = audit(listing, product)
    console.log(`\nMerchant audit: ${edits.length ? `${edits.length} edit(s) needed` : 'nothing to edit'}`)
    for (const edit of edits) console.log(`  - ${edit.why}  (${edit.says})`)
    console.log(`Cost: $${listing.cost.toFixed(5)}`)
    return 0
  }
  if (command === 'review') {
    const rows = await review(mastraAsk(settings()), products, (row) =>
      console.log(`${row.how.padEnd(10)} ${row.id.padEnd(20)} ${row.clean ? 'clean' : row.listing.status === 'held' ? 'held' : `live, ${row.edits.length} edit(s)`}`),
    )
    console.log('')
    for (const totals of [summary(rows, 'one-prompt'), summary(rows, 'workflow')]) {
      console.log(`${totals.how.padEnd(10)} published without an edit: ${totals.clean} of ${totals.total}, held ${totals.held}, live with errors ${totals.wrong}, $${totals.cost.toFixed(4)}`)
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
