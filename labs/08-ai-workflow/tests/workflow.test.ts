// The workflow's shape, with no model: a fake Ask answers each step by its instructions.
import { describe, expect, it } from 'vitest'
import type { z } from 'zod'
import { audit, cleanlyPublished } from '../src/audit.js'
import { load } from '../src/catalogue.js'
import type { Ask } from '../src/llm.js'
import { onePrompt } from '../src/onePrompt.js'
import { serve } from '../src/serve.js'
import { CHECK, DESCRIPTION, EXTRACT, FEATURES, REPAIR, TITLE, codeCheck, missingLimits, REPAIR_ROUNDS, unId, write, type Fact, type StepEvent } from '../src/workflow.js'

const tent = load().find((product) => product.id === 'tarn-2p-tent')!
const FACTS = [
  { fact: 'The tent sleeps 2 people.', quote: '2 person', limit: false },
  { fact: 'It is not for snow.', quote: 'Not designed for snow loading', limit: true },
  // Not on the sheet: the extract step must drop it.
  { fact: 'It is waterproof to 10000 mm.', quote: 'waterproof to 10000 mm', limit: false },
]

type Plan = { features?: { text: string; facts: string[] }[]; repair?: (prompt: string) => string; reject?: RegExp }

/** A fake model: each step's instructions pick its canned answer, and every call is logged. */
function fake(plan: Plan = {}): { ask: Ask; calls: string[] } {
  const calls: string[] = []
  const answer = (instructions: string, prompt: string): unknown => {
    if (instructions === EXTRACT) return { facts: FACTS }
    if (instructions === TITLE) return { items: [{ text: 'Tarn 2 Tent, 2 person', facts: ['F1'] }] }
    if (instructions === DESCRIPTION) return { items: [{ text: 'A tent with room for 2.', facts: ['F1'] }] }
    if (instructions === FEATURES)
      return { items: plan.features ?? [{ text: 'Sleeps 2.', facts: ['F1'] }, { text: 'Not for snow.', facts: ['F2'] }] }
    if (instructions === REPAIR) return { text: plan.repair ? plan.repair(prompt) : 'Fixed.', facts: [] }
    if (instructions === CHECK) {
      const items = [...prompt.matchAll(/Item (\d+): (.*)/g)]
      return { verdicts: items.map(([, itemNumber, text]) => ({ item: Number(itemNumber), supported: !plan.reject?.test(text), why: 'unsupported' })) }
    }
    return { title: 'Tarn 2', description: ['Roomy.'], features: ['Sleeps 2.'] }
  }
  const ask = (async (instructions: string, prompt: string, shape: z.ZodType) => {
    calls.push(instructions)
    return { object: shape.parse(answer(instructions, prompt)), cost: 0.001 }
  }) as Ask
  return { ask, calls }
}

const fact = (id: string, text: string, limit = false): Fact => ({ id, fact: text, quote: text, limit })

describe('code checks', () => {
  const facts = [fact('F1', 'Packed weight 2.1 kg'), fact('F2', 'Not for snow', true)]

  it('fails a claim that cites nothing, a stray number, or a fact id in the text', () => {
    expect(codeCheck({ text: 'Light.', facts: [] }, facts)).toMatch(/cites no fact/)
    expect(codeCheck({ text: 'Weighs 1.9 kg.', facts: ['F1'] }, facts)).toMatch(/"1.9"/)
    expect(codeCheck({ text: 'Weighs 2.1 kg (F1).', facts: ['F1'] }, facts)).toMatch(/fact id/)
    expect(codeCheck({ text: 'Weighs 2.1 kg.', facts: ['F1'] }, facts)).toBe('')
  })

  it('takes fact ids out of the copy before anything checks it', () => {
    expect(unId('Weighs 2.1 kg (F1, F2).')).toBe('Weighs 2.1 kg.')
    expect(unId('Not for snow [F3]')).toBe('Not for snow')
  })

  it('turns a limit no item cites into a failed bullet for repair to write', () => {
    const items = [{ part: 'feature' as const, text: 'Weighs 2.1 kg.', facts: ['F1'], ok: true, why: '' }]
    expect(missingLimits(items, facts).map((item) => item.facts)).toEqual([['F2']])
  })
})

describe('the workflow', () => {
  it('drops an invented fact, runs the writers together, and publishes when every claim passes', async () => {
    const { ask } = fake()
    const steps: StepEvent[] = []
    const listing = await write(ask, tent.sheet, (event) => steps.push(event))
    expect(listing.status).toBe('published')
    expect(listing.facts.map((fact) => fact.quote)).not.toContain('waterproof to 10000 mm')
    const order = steps.map((event) => `${event.phase}:${event.step}`)
    // All three writers start before any of them ends: that is the parallel step.
    const firstEnd = Math.min(...['title', 'description', 'features'].map((part) => order.indexOf(`end:${part}`)))
    for (const part of ['title', 'description', 'features']) expect(order.indexOf(`start:${part}`)).toBeLessThan(firstEnd)
    expect(order).toContain('end:publish')
    expect(order).not.toContain('start:repair')
  })

  it('repairs a claim with a stray number, then publishes', async () => {
    const { ask, calls } = fake({ features: [{ text: 'Sleeps 3.', facts: ['F1'] }, { text: 'Not for snow.', facts: ['F2'] }], repair: () => 'Sleeps 2.' })
    const listing = await write(ask, tent.sheet)
    expect(calls.filter((call) => call === REPAIR)).toHaveLength(1)
    expect(listing).toMatchObject({ status: 'published', repaired: 1 })
    expect(listing.items.map((item) => item.text)).toContain('Sleeps 2.')
  })

  it('writes a missing limit through the repair branch', async () => {
    const { ask } = fake({ features: [{ text: 'Sleeps 2.', facts: ['F1'] }], repair: () => 'Not designed for snow loading.' })
    const listing = await write(ask, tent.sheet)
    expect(listing.status).toBe('published')
    expect(listing.items.map((item) => item.text)).toContain('Not designed for snow loading.')
  })

  it('cuts a sentence repair cannot source, but holds a limit it cannot source', async () => {
    const cut = fake({ features: [{ text: 'Sleeps 2 BAD.', facts: ['F1'] }, { text: 'Not for snow.', facts: ['F2'] }], reject: /BAD/, repair: () => 'Still BAD.' })
    const kept = await write(cut.ask, tent.sheet)
    expect(kept).toMatchObject({ status: 'published', cut: ['Still BAD.'] })
    // It got every round of repair before it was cut.
    expect(cut.calls.filter((call) => call === REPAIR)).toHaveLength(REPAIR_ROUNDS)

    const held = fake({ features: [{ text: 'Sleeps 2.', facts: ['F1'] }, { text: 'Snow is fine BAD.', facts: ['F2'] }], reject: /BAD/, repair: () => 'Still BAD.' })
    expect((await write(held.ask, tent.sheet)).status).toBe('held')
  })
})

describe('the merchant audit', () => {
  const listing = (texts: string[]) => ({
    status: 'published' as const,
    items: texts.map((text) => ({ part: 'feature' as const, text, facts: [], ok: true, why: '' })),
    facts: [],
    repaired: 0,
    cut: [],
    why: '',
    cost: 0,
  })

  it('passes a negated rule word and fails a forbidden or invented claim', () => {
    const ok = listing(['A 2 person, 3-season tent.', 'Not for winter or snow.'])
    expect(audit(ok, tent)).toEqual([])
    expect(cleanlyPublished(ok, [])).toBe(true)
    const whys = audit(listing(['A 2 person, 3-season tent for winter.', 'Lifetime warranty, 7 pegs.']), tent).map((edit) => edit.why)
    expect(whys.join(' ')).toMatch(/not for snow or winter/)
    expect(whys.join(' ')).toMatch(/lifetime warranty/)
    expect(whys.join(' ')).toMatch(/7 is not on the supplier sheet/)
  })

  it('asks for a fact the merchant requires', () => {
    expect(audit(listing(['A 3-season tent.']), tent).map((edit) => edit.why)).toEqual(['The listing must state its capacity, 2 person.'])
  })

  it('publishes one-prompt copy unchecked, so only the audit can catch it', async () => {
    const one = await onePrompt(fake().ask, tent.sheet)
    expect(one.status).toBe('published')
    expect(one.facts).toEqual([])
  })
})

describe('the import page server', () => {
  it('answers this machine and refuses any other host or site', async () => {
    const port = 8700 + Math.floor(Math.random() * 200)
    const server = serve(load(), fake().ask, port)
    try {
      const at = `http://127.0.0.1:${port}/api/products`
      expect((await fetch(at)).status).toBe(200)
      expect((await fetch(at, { headers: { origin: 'http://evil.example' } })).status).toBe(403)
    } finally {
      server.close()
    }
  })
})
