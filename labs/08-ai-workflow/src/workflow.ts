/**
 * The listing workflow: a supplier spec sheet in, a product listing out, every claim traced.
 *
 *   extract facts -> (title | description | features, in parallel) -> check claims -> publish or repair
 *
 * Each step does one small job a person could check. The writers never see the sheet, only the facts
 * pulled from it, so the supplier's marketing lines cannot leak into the copy. There is no agent loop:
 * the order of steps is fixed in code, and the one choice, publish or repair, is a branch on a count.
 */
import { createStep, createWorkflow } from '@mastra/core/workflows'
import { z } from 'zod'
import { numbers, onSheet } from './catalogue.js'
import type { Ask } from './llm.js'

// Northbeck's brand voice. Both ways get it, because a store's copy is never neutral, and a voice that
// sells is exactly what pulls a writer past the facts.
export const VOICE = `Write in Northbeck Outdoor's voice: confident, warm and adventurous, speaking to the
trip the buyer is planning.`

export const EXTRACT = `You read a supplier's product spec sheet for an outdoor store. List every fact a buyer
could rely on: the product name and brand first, then specs, materials, ratings, sizes, what is and is
not included, care, warranty, limits and warnings. Skip the supplier's marketing lines, such as
"shrugs off the weather": they are claims, not facts. For each fact, copy the exact words from the
sheet that state it as quote. Write fact as a full sentence with its value, such as "The drop is 6 mm",
never a bare label. Set limit to true for a limit or warning (not included, not for, do not, only),
and for a rating a buyer relies on to stay safe, warm or dry: season, water rating, age range.`

const CITE = `Use only the facts given. Each item cites the ids of the facts it states, in facts, never in
its text. Do not add a
material, rating, use, comparison or number that the facts do not state, and do not round numbers.
${VOICE}`

export const TITLE = `You write the product title for an outdoor store's website: brand, product name,
and the one or two specs a buyer filters on, such as capacity or volume. One item. ${CITE}`

export const DESCRIPTION = `You write the description for an outdoor store's product page: exactly 3
sentences on what the product is and what its facts mean for a buyer. One sentence per item. Leave the
spec list to the feature bullets. ${CITE}`

export const FEATURES = `You write the feature bullets for an outdoor store's product page: 6 to 8
short bullets, one item each, the specs a buyer compares. Every fact marked limit gets its own bullet
in plain words, because a buyer who misses a limit returns the product. ${CITE}`

export const CHECK = `You check product copy before it goes live. Each item lists the facts it cites,
taken from the supplier's sheet. An item is supported only when its cited facts state everything it
says: every material, rating, number, use and comparison. Plain wording of a stated fact is supported,
and so is brand voice that claims nothing, such as "ready for your next trip" or "light in the pack"
for a stated weight. A claim about safety, quality, ethics or what the product can survive is a fact,
and it needs a source. Anything the facts do not state, or state differently, is unsupported: say what in why.`

export const REPAIR = `You fix one item of product copy that failed a check, or write one that is missing.
Rewrite it so it says only what its cited facts state, keeping it useful to a buyer. Do not repeat what
the rest of the listing already says. Keep the same fact ids.`

const Fact = z.object({ id: z.string(), fact: z.string(), quote: z.string(), limit: z.boolean() })
const Item = z.object({ text: z.string(), facts: z.array(z.string()) })
const Checked = Item.extend({ part: z.enum(['title', 'description', 'feature']), ok: z.boolean(), why: z.string() })
const Copy = z.object({ items: z.array(Item), cost: z.number() })
const Extracted = z.object({ facts: z.array(Fact), dropped: z.array(z.string()), cost: z.number() })
const Review = z.object({ facts: z.array(Fact), items: z.array(Checked), cost: z.number() })
export const Listing = z.object({
  status: z.enum(['published', 'held']),
  items: z.array(Checked),
  facts: z.array(Fact),
  repaired: z.number(),
  // Sentences the repair could not source, cut instead of published. The page shows them struck out.
  cut: z.array(z.string()),
  why: z.string(),
  cost: z.number(),
})
export type Fact = z.infer<typeof Fact>
export type Checked = z.infer<typeof Checked>
export type Listing = z.infer<typeof Listing>

// What the model returns: no ids, so it cannot invent one, and the code numbers the facts itself.
const Facts = z.object({ facts: z.array(Fact.omit({ id: true })) })
const Verdicts = z.object({ verdicts: z.array(z.object({ item: z.number(), supported: z.boolean(), why: z.string() })) })

// The writers see each fact with the sheet's own words, so a terse fact still carries its value.
// Models like to show their working, "(F3)", inside the copy. Code takes it out before anything checks it.
export const unId = (text: string) => text.replace(/\s*[([]?\bF\d+(?:\s*,\s*F\d+)*[)\]]?/g, '').trim()

function listFacts(facts: Fact[]): string {
  return facts.map((fact) => `${fact.id}${fact.limit ? ' (limit)' : ''}: ${fact.fact} (sheet: "${fact.quote}")`).join('\n')
}

/** A limit no item cites becomes an empty, failed bullet, so the repair branch writes it. */
export function missingLimits(items: Checked[], facts: Fact[]): Checked[] {
  const cited = new Set(items.flatMap((item) => item.facts))
  return facts
    .filter((fact) => fact.limit && !cited.has(fact.id))
    .map((fact) => ({ part: 'feature' as const, text: '', facts: [fact.id], ok: false, why: `A limit the buyer must know is missing: ${fact.fact}` }))
}

/**
 * The checks code can make with no model: the item cites real facts, and every number in it appears
 * in one of them. A model checks the rest, but only items that pass here reach it.
 */
export function codeCheck(item: z.infer<typeof Item>, facts: Fact[]): string {
  const cited = facts.filter((fact) => item.facts.includes(fact.id))
  if (!cited.length) return 'It cites no fact from the sheet.'
  if (/\bF\d+\b/.test(item.text)) return 'It shows a fact id to the buyer.'
  const known = new Set(cited.flatMap((fact) => numbers(`${fact.fact} ${fact.quote}`)))
  const stray = numbers(item.text).find((number) => !known.has(number))
  return stray ? `"${stray}" is in no fact it cites.` : ''
}

/** Code first, then one model call for every item that code passed, so the model never sees a stray number. */
async function checkItems(ask: Ask, items: Checked[], facts: Fact[]): Promise<{ items: Checked[]; cost: number }> {
  const out = items.map((item) => {
    const why = codeCheck(item, facts)
    return { ...item, ok: !why, why }
  })
  const open = out.map((item, position) => ({ item, position })).filter(({ item }) => item.ok)
  if (!open.length) return { items: out, cost: 0 }
  const prompt = open
    .map(({ item, position }) => `Item ${position}: ${item.text}\nCited facts:\n${listFacts(facts.filter((fact) => item.facts.includes(fact.id)))}`)
    .join('\n\n')
  const { object, cost } = await ask(CHECK, prompt, Verdicts)
  for (const { item, position } of open) {
    const verdict = object?.verdicts.find((entry) => entry.item === position)
    // An item the check did not return a verdict for is not a pass.
    item.ok = verdict?.supported ?? false
    item.why = verdict ? (verdict.supported ? '' : verdict.why) : 'The check returned no verdict.'
  }
  return { items: out, cost }
}

// A second rewrite fixes most of what the first missed; a third rarely does, and each costs a call per item.
export const REPAIR_ROUNDS = 2

export function build(ask: Ask) {
  const extract = createStep({
    id: 'extract',
    inputSchema: z.object({ sheet: z.string() }),
    outputSchema: Extracted,
    execute: async ({ inputData }) => {
      const { object, cost } = await ask(EXTRACT, inputData.sheet, Facts)
      const all = object?.facts ?? []
      // A fact whose quote is not on the sheet came from the model, not the supplier: drop it.
      const kept = all.filter((fact) => onSheet(fact.quote, inputData.sheet))
      const facts = kept.map((fact, index) => ({ ...fact, id: `F${index + 1}` }))
      return { facts, dropped: all.filter((fact) => !kept.includes(fact)).map((fact) => fact.fact), cost }
    },
  })

  // Three writers with one input and no need for each other's output: Mastra runs them at once.
  const writer = (id: 'title' | 'description' | 'features', instructions: string) =>
    createStep({
      id,
      inputSchema: Extracted,
      outputSchema: Copy,
      execute: async ({ inputData }) => {
        const { object, cost } = await ask(instructions, listFacts(inputData.facts), z.object({ items: z.array(Item) }))
        return { items: (object?.items ?? []).map((item) => ({ ...item, text: unId(item.text) })), cost }
      },
    })

  const check = createStep({
    id: 'check',
    inputSchema: z.object({ title: Copy, description: Copy, features: Copy }),
    outputSchema: Review,
    execute: async ({ inputData, getStepResult }) => {
      // The parallel step hands over each writer's output by id; the facts come from extract.
      const { facts, cost: extracted } = getStepResult(extract) as z.infer<typeof Extracted>
      const parts = [
        ...inputData.title.items.slice(0, 1).map((item) => ({ ...item, part: 'title' as const })),
        // A model asked for 3 sentences can return 13; the page has room for 3, so code holds the line.
        ...inputData.description.items.slice(0, 3).map((item) => ({ ...item, part: 'description' as const })),
        ...inputData.features.items.map((item) => ({ ...item, part: 'feature' as const })),
      ].map((item) => ({ ...item, ok: false, why: '' }))
      const written = inputData.title.cost + inputData.description.cost + inputData.features.cost
      if (!parts.length) return { facts, items: [], cost: extracted + written }
      const { items, cost } = await checkItems(ask, parts, facts)
      return { facts, items: [...items, ...missingLimits(items, facts)], cost: extracted + written + cost }
    },
  })

  const publish = createStep({
    id: 'publish',
    inputSchema: Review,
    outputSchema: Listing,
    execute: async ({ inputData }) => ({
      status: 'published' as const,
      ...inputData,
      repaired: 0,
      cut: [],
      why: `All ${inputData.items.length} claims trace to the supplier sheet.`,
    }),
  })

  // Each round rewrites every failed item and checks it again, up to REPAIR_ROUNDS. A sentence that still
  // fails is cut, since a listing reads fine without it. A title or a limit cannot be cut: a person decides.
  const repair = createStep({
    id: 'repair',
    inputSchema: Review,
    outputSchema: Listing,
    execute: async ({ inputData }) => {
      const { facts } = inputData
      let cost = inputData.cost
      const failed = inputData.items.filter((check) => !check.ok)
      let all = inputData.items
      for (let round = 0; round < REPAIR_ROUNDS && all.some((check) => !check.ok); round++) {
        const open = all.filter((check) => !check.ok)
        const rewritten = await Promise.all(
          open.map(async (item) => {
            const cited = facts.filter((fact) => item.facts.includes(fact.id))
            const rest = all.filter((other) => other !== item && other.text).map((other) => `- ${other.text}`).join('\n')
            const prompt = `Item: ${item.text || '(missing)'}\nWhy it failed: ${item.why}\nCited facts:\n${listFacts(cited)}\n\nThe rest of the listing:\n${rest}`
            const { object, cost: repairCost } = await ask(REPAIR, prompt, Item)
            cost += repairCost
            return { ...item, text: object ? unId(object.text) : item.text, facts: object?.facts.length ? object.facts : item.facts }
          }),
        )
        const rechecked = await checkItems(ask, rewritten, facts)
        cost += rechecked.cost
        const fixed = new Map(open.map((item, index) => [item, rechecked.items[index]]))
        all = all.map((item) => fixed.get(item) ?? item)
      }
      const limits = new Set(facts.filter((fact) => fact.limit).map((fact) => fact.id))
      const mustKeep = (check: Checked) => check.part === 'title' || check.facts.some((id) => limits.has(id))
      const cut = all.filter((check) => !check.ok && !mustKeep(check) && check.text).map((check) => check.text)
      const items = all.filter((check) => check.ok || mustKeep(check))
      const still = items.filter((check) => !check.ok)
      const repaired = failed.length - still.length - cut.length
      if (!items.length) return { status: 'held' as const, items, facts, repaired: 0, cut, why: 'The writers returned no copy.', cost }
      return still.length
        ? { status: 'held' as const, items, facts, repaired, cut, why: `${still.length} of ${items.length} claims still have no source: ${still[0].why}`, cost }
        : { status: 'published' as const, items, facts, repaired, cut, why: `${repaired} claims rewritten and ${cut.length} cut; all ${items.length} left trace to the sheet.`, cost }
    },
  })

  return createWorkflow({ id: 'listing', inputSchema: z.object({ sheet: z.string() }), outputSchema: Listing })
    .then(extract)
    .parallel([writer('title', TITLE), writer('description', DESCRIPTION), writer('features', FEATURES)])
    .then(check)
    .branch([
      [async ({ inputData }) => inputData.items.length > 0 && inputData.items.every((item) => item.ok), publish],
      [async ({ inputData }) => !inputData.items.length || inputData.items.some((item) => !item.ok), repair],
    ])
    .commit()
}

export type StepEvent = { step: string; phase: 'start' | 'end'; ms: number }

/** Run the workflow on one sheet, reporting each step as it starts and ends. */
export async function write(ask: Ask, sheet: string, onStep: (step: StepEvent) => void = () => {}): Promise<Listing> {
  const run = await build(ask).createRun()
  const t0 = Date.now()
  const stream = run.stream({ inputData: { sheet } })
  for await (const event of stream.fullStream) {
    const id = (event as { payload?: { id?: string } }).payload?.id
    if (!id) continue
    if (event.type === 'workflow-step-start') onStep({ step: id, phase: 'start', ms: Date.now() - t0 })
    if (event.type === 'workflow-step-result') onStep({ step: id, phase: 'end', ms: Date.now() - t0 })
  }
  const result = await stream.result
  if (result.status !== 'success') throw new Error(`workflow ${result.status}`)
  // A branch's output is keyed by the step that ran, { publish: ... } or { repair: ... }.
  const out = result.result as unknown as Record<string, Listing>
  return Listing.parse(out.publish ?? out.repair)
}
