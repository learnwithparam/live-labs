// The lab's promise, against a real model: across Northbeck's 12 supplier sheets, the workflow puts more
// listings live with nothing to edit than one prompt does, and fewer live with an error.
import { expect, it } from 'vitest'
import { load } from '../src/catalogue.js'
import { settings } from '../src/config.js'
import { mastraAsk } from '../src/llm.js'
import { review, summary } from '../src/review.js'

const MIN_CLEAN = 8 // of 12 workflow listings live with nothing for the merchant to edit
const MAX_WRONG = 3 // of 12 workflow listings live with something the merchant would take back

it('publishes more listings without an edit than one prompt, and fewer wrong ones', async () => {
  const rows = await review(mastraAsk(settings()), load())
  const flow = summary(rows, 'workflow')
  const one = summary(rows, 'one-prompt')
  expect(flow.clean, JSON.stringify({ flow, one })).toBeGreaterThanOrEqual(MIN_CLEAN)
  expect(flow.clean, JSON.stringify({ flow, one })).toBeGreaterThan(one.clean)
  expect(flow.wrong, JSON.stringify({ flow, one })).toBeLessThanOrEqual(MAX_WRONG)
  expect(flow.wrong, JSON.stringify({ flow, one })).toBeLessThan(one.wrong)
}, 600_000)
