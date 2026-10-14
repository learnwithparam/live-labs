/**
 * Both ways on every supplier sheet, audited by the merchant's rules.
 *
 * One listing proves nothing. The review counts what the product team watches: listings that went
 * live with nothing to edit, listings held for a person, and listings that went live wrong.
 */
import { audit, cleanlyPublished, type Edit } from './audit.js'
import type { Product } from './catalogue.js'
import type { Ask } from './llm.js'
import { onePrompt } from './onePrompt.js'
import { write, type Listing } from './workflow.js'

export type How = 'workflow' | 'one-prompt'
export type Row = { id: string; product: string; how: How; listing: Listing; edits: Edit[]; clean: boolean }
export type Summary = { how: How; clean: number; held: number; wrong: number; total: number; cost: number }

export async function one(ask: Ask, product: Product, how: How): Promise<Row> {
  const listing = how === 'workflow' ? await write(ask, product.sheet) : await onePrompt(ask, product.sheet)
  const edits = audit(listing, product)
  return { id: product.id, product: product.product, how, listing, edits, clean: cleanlyPublished(listing, edits) }
}

export function summary(rows: Row[], how: How): Summary {
  const mine = rows.filter((r) => r.how === how)
  return {
    how,
    clean: mine.filter((r) => r.clean).length,
    held: mine.filter((r) => r.listing.status === 'held').length,
    // The costly outcome: live on the store with something a merchant would have to take back.
    wrong: mine.filter((r) => r.listing.status === 'published' && r.edits.length).length,
    total: mine.length,
    cost: mine.reduce((sum, r) => sum + r.listing.cost, 0),
  }
}

/** Every product both ways, a few at a time, reporting each row as it lands. */
export async function review(ask: Ask, products: Product[], onRow: (r: Row) => void = () => {}, width = 6): Promise<Row[]> {
  const jobs = products.flatMap((p) => (['one-prompt', 'workflow'] as How[]).map((how) => ({ p, how })))
  const rows: Row[] = []
  let next = 0
  // A small pool keeps within the provider's rate limit and still finishes in under a minute.
  const lane = async () => {
    while (next < jobs.length) {
      const { p, how } = jobs[next++]
      const row = await one(ask, p, how)
      rows.push(row)
      onRow(row)
    }
  }
  await Promise.all(Array.from({ length: width }, lane))
  return rows
}
