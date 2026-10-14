/**
 * One structured call to the model, made through a Mastra Agent.
 *
 * Every step takes an `Ask` rather than an agent, so the tests can pass a fake that answers by
 * instructions and the workflow never knows the difference.
 */
import { Agent } from '@mastra/core/agent'
import type { z } from 'zod'
import type { Settings } from './config.js'

export type Ask = <T extends z.ZodType>(
  instructions: string,
  prompt: string,
  shape: T,
) => Promise<{ object: z.infer<T> | null; cost: number }>

export function mastraAsk(s: Settings): Ask {
  // Mastra's model router takes any OpenAI-compatible endpoint as provider/model plus a URL and key.
  const model = { id: `openrouter/${s.model}` as `${string}/${string}`, url: s.baseUrl, apiKey: s.apiKey }
  return async (instructions, prompt, shape) => {
    const agent = new Agent({ id: 'listing-step', name: 'listing-step', instructions, model })
    try {
      const reply = await agent.generate(prompt, { structuredOutput: { schema: shape } })
      // OpenRouter reports what each call cost; it rides through Mastra in the raw usage.
      const raw = (reply.usage as { raw?: { raw?: { cost?: number } } } | undefined)?.raw?.raw
      return { object: (reply.object as z.infer<typeof shape>) ?? null, cost: raw?.cost ?? 0 }
    } catch {
      // A failed call is an empty answer, and every step treats empty as "not checked".
      return { object: null, cost: 0 }
    }
  }
}
