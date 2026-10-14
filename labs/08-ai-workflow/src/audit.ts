/**
 * The merchant's audit: what a merchandiser would have to edit before this listing could stay live.
 *
 * It is code, not a model, so the score cannot drift with the model it scores. It checks three things
 * against the supplier sheet and the merchant's rules for that product:
 *   - every number in the copy is on the sheet
 *   - no sentence says something the merchant's rules forbid, such as "waterproof" on a softshell
 *   - the copy states each fact the merchant requires, such as a filter's "does not remove viruses"
 */
import { numbers, plain, type Product } from './catalogue.js'
import type { Listing } from './workflow.js'

export type Edit = { says: string; why: string }

// "Not for submersion" mentions submersion to deny it. A forbidden word in a negated sentence is fine.
const NEGATED = /\b(?:not|no|never|without|isn't|aren't|doesn't|don't|excluded|separately|survival only)\b/i
// Every product shares one rule: the merchant never promises a lifetime warranty the supplier does not give.
const EVERYWHERE = [{ says: 'lifetime', why: 'No supplier sheet gives a lifetime warranty.' }]

function sentences(listing: Listing): string[] {
  return listing.items.flatMap((i) => plain(i.text).split(/(?<=[.!?;])\s+/)).filter(Boolean)
}

export function audit(listing: Listing, product: Product): Edit[] {
  const said = sentences(listing)
  const edits: Edit[] = []
  const onSheet = new Set(numbers(product.sheet))
  for (const s of said) {
    const stray = numbers(s).filter((n) => !onSheet.has(n))
    if (stray.length) edits.push({ says: s, why: `${stray.join(', ')} is not on the supplier sheet.` })
  }
  for (const rule of [...product.never, ...EVERYWHERE]) {
    const hit = said.find((s) => new RegExp(rule.says, 'i').test(s) && !NEGATED.test(s))
    if (hit) edits.push({ says: hit, why: rule.why ?? '' })
  }
  const all = said.join(' ')
  for (const rule of product.must) {
    if (!new RegExp(rule.says, 'i').test(all)) edits.push({ says: '(missing)', why: `The listing must state ${rule.what}.` })
  }
  return edits
}

/** The metric: a listing went live and the merchant had nothing to edit. */
export function cleanlyPublished(listing: Listing, edits: Edit[]): boolean {
  return listing.status === 'published' && edits.length === 0
}
