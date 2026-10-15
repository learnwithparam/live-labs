// Settings from the environment, with a .env file read first when there is one.
import { existsSync, readFileSync } from 'node:fs'

export type Settings = { apiKey: string; baseUrl: string; model: string }

export function loadDotenv(path = '.env'): void {
  if (!existsSync(path)) return
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const [key, ...rest] = line.split('=')
    if (!key.trim() || key.trim().startsWith('#') || !rest.length) continue
    process.env[key.trim()] ??= rest.join('=').trim().replace(/^['"]|['"]$/g, '')
  }
}

export function settings(): Settings {
  loadDotenv()
  const apiKey = process.env.LLM_API_KEY ?? ''
  if (!apiKey) throw new Error('Set LLM_API_KEY in .env (copy env.example).')
  return {
    apiKey,
    baseUrl: process.env.LLM_BASE_URL ?? 'https://openrouter.ai/api/v1',
    model: process.env.LLM_MODEL ?? 'openai/gpt-6-luna',
  }
}
