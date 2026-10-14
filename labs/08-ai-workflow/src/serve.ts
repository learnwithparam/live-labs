// Northbeck's product import page: one supplier sheet through the workflow, live, and the whole catalogue.
import { appendFileSync, mkdirSync, readFileSync } from 'node:fs'
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { audit, cleanlyPublished } from './audit.js'
import type { Product } from './catalogue.js'
import type { Ask } from './llm.js'
import { onePrompt } from './onePrompt.js'
import { review, summary } from './review.js'
import { write } from './workflow.js'

const PAGE = join(dirname(fileURLToPath(import.meta.url)), 'page.html')

// The page drives a paid model, so only this machine may call it: no other host, no other site.
function local(req: IncomingMessage, port: number): boolean {
  const ok = [`127.0.0.1:${port}`, `localhost:${port}`]
  const origin = req.headers.origin
  return ok.includes(req.headers.host ?? '') && (!origin || ok.some((h) => origin === `http://${h}`))
}

function events(res: ServerResponse): (type: string, data: unknown) => void {
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' })
  return (type, data) => res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`)
}

export function serve(products: Product[], ask: Ask, port: number): Server {
  const server = createServer(async (req, res) => {
    if (!local(req, port)) {
      res.writeHead(403).end('local only')
      return
    }
    const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`)
    try {
      if (url.pathname === '/') {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }).end(readFileSync(PAGE))
      } else if (url.pathname === '/api/products') {
        const rows = products.map(({ id, product, price, sheet }) => ({ id, product, price, sheet }))
        res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(rows))
      } else if (url.pathname === '/api/write') {
        const product = products.find((p) => p.id === url.searchParams.get('id'))
        if (!product) return void res.writeHead(404).end('no such product')
        const send = events(res)
        const listing =
          url.searchParams.get('how') === 'one-prompt'
            ? await (async () => {
                send('step', { step: 'one-prompt', phase: 'start', ms: 0 })
                const t0 = Date.now()
                const l = await onePrompt(ask, product.sheet)
                send('step', { step: 'one-prompt', phase: 'end', ms: Date.now() - t0 })
                return l
              })()
            : await write(ask, product.sheet, (e) => send('step', e))
        const edits = audit(listing, product)
        send('done', { listing, edits, clean: cleanlyPublished(listing, edits) })
        res.end()
      } else if (url.pathname === '/api/review') {
        const send = events(res)
        const rows = await review(ask, products, (r) => send('row', r))
        send('done', { summaries: [summary(rows, 'one-prompt'), summary(rows, 'workflow')] })
        res.end()
      } else if (url.pathname === '/api/decide' && req.method === 'POST') {
        // A held listing waits for a merchandiser; what they chose is kept beside the review.
        let body = ''
        for await (const chunk of req) body += chunk
        const { id, decision } = JSON.parse(body)
        mkdirSync('out', { recursive: true })
        appendFileSync('out/merchant.jsonl', JSON.stringify({ id, decision, at: new Date().toISOString() }) + '\n')
        res.writeHead(200, { 'content-type': 'application/json' }).end('{"saved":true}')
      } else {
        res.writeHead(404).end('not found')
      }
    } catch (error) {
      if (!res.headersSent) res.writeHead(500)
      res.end(String(error))
    }
  })
  return server.listen(port, '127.0.0.1')
}
