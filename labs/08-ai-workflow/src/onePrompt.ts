/**
 * The way most teams start: the whole spec sheet into one prompt, the whole listing out.
 *
 * It is one call and it reads well. It also sees the supplier's marketing lines next to the specs,
 * and nothing checks what it wrote, so a listing goes live with whatever the model added.
 */
import { z } from 'zod'
import type { Ask } from './llm.js'
import { VOICE, type Listing } from './workflow.js'

export const ONE_PROMPT = `You write product listings for an outdoor store's website. From the supplier's
spec sheet, write a title, a description of 2 or 3 sentences, and 5 to 7 feature bullets that sell the
product to a buyer. ${VOICE}`

const Written = z.object({ title: z.string(), description: z.array(z.string()), features: z.array(z.string()) })

export async function onePrompt(ask: Ask, sheet: string): Promise<Listing> {
  const { object, cost } = await ask(ONE_PROMPT, sheet, Written)
  if (!object) return { status: 'held', items: [], facts: [], repaired: 0, cut: [], why: 'The model returned no listing.', cost }
  // Same shape as the workflow's listing, so the page and the audit treat both ways alike.
  const item = (part: 'title' | 'description' | 'feature') => (text: string) => ({ part, text, facts: [], ok: true, why: '' })
  return {
    status: 'published',
    items: [item('title')(object.title), ...object.description.map(item('description')), ...object.features.map(item('feature'))],
    facts: [],
    repaired: 0,
    cut: [],
    why: 'Published as written: nothing checks a one-prompt listing.',
    cost,
  }
}
