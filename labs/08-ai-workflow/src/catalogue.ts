/**
 * The supplier sheets and the merchant's listing rules.
 *
 * The sheet is the source of truth: every claim in a listing must trace back to its words. The rules
 * are how Northbeck's merchandisers audit a listing before it goes live, written as patterns so the
 * audit runs in code and never asks a model.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export type Rule = { says: string; why?: string; what?: string }
export type Product = { id: string; product: string; price: number; sheet: string; never: Rule[]; must: Rule[] }

export function load(data = 'data'): Product[] {
  const rows = readFileSync(join(data, 'merchant.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l))
  const sheets = new Set(readdirSync(join(data, 'sheets')))
  return rows.map((row) => {
    // A rule row with no sheet would silently drop a product from the review.
    if (!sheets.has(`${row.id}.txt`)) throw new Error(`no supplier sheet for ${row.id}`)
    return { ...row, sheet: readFileSync(join(data, 'sheets', `${row.id}.txt`), 'utf8') }
  })
}

// Copy uses typographic minus signs and dashes; the sheets use plain ones. Compare on plain text.
export function plain(text: string): string {
  return text.replace(/[−–—]/g, '-').replace(/ /g, ' ').replace(/\s+/g, ' ').trim()
}

/** Every number in a text, without thousands commas, so "8,900" on the sheet matches "8900" in copy. */
export function numbers(text: string): string[] {
  return [...plain(text).replace(/(\d),(\d{3})\b/g, '$1$2').matchAll(/\d+(?:\.\d+)?/g)].map((m) => m[0])
}

/** True when a quote appears on the sheet word for word, ignoring spacing and case. */
export function onSheet(quote: string, sheet: string): boolean {
  const q = plain(quote).toLowerCase()
  return q.length > 2 && plain(sheet).toLowerCase().includes(q)
}
