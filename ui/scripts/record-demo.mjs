/**
 * Record the demo footage of the live UI, under 3 minutes.
 *
 *   cd ui && node scripts/record-demo.mjs
 *
 * Two acts, because they prove different things:
 *
 *   Act one, a small corpus with the curated vocabulary. Twenty-eight documents
 *   whose contradictions, aliases and gaps are known, so the mechanisms are
 *   visible: a disagreement settled, the same question answered as of an
 *   earlier date, and a refusal.
 *
 *   Act two, Salesforce HERB. A published dataset nobody tailored for this, read
 *   by its own adapter, to show the thing works at real scale and still refuses
 *   when the answer is absent.
 *
 * Silent by design: this is footage, narration goes on top. Beats are timed
 * against the narration script and every dwell is a reading pause. Requires the
 * stack up, the API on 8000 and Vite on 5173.
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const outDir = resolve(root, 'recordings')
mkdirSync(outDir, { recursive: true })

const UI = process.env.ARBITER_UI || 'http://127.0.0.1:5173'
// One product keeps the build near 35s. The narration states the full corpus size.
const HERB_PRODUCTS = Number(process.env.HERB_PRODUCTS || 1)
const beat = (ms) => new Promise((r) => setTimeout(r, ms))
const t0 = Date.now()
const mark = (label) => console.log(`  ${((Date.now() - t0) / 1000).toFixed(1).padStart(6)}s  ${label}`)

async function waitForStep(page, label, timeout = 300_000) {
  const begin = Date.now()
  while (Date.now() - begin < timeout) {
    if (await page.locator('.failed').count()) throw new Error(`${label} failed`)
    if (!(await page.locator('.working').count())) return
    await beat(400)
  }
  throw new Error(`${label} timed out`)
}

async function ask(page, question, read = 6000) {
  const box = page.locator('input.ask')
  await box.click()
  await box.fill('')
  await box.type(question, { delay: 34 })
  await beat(300)
  await page.getByRole('button', { name: 'send' }).click()
  await page.locator('.turn').last().waitFor({ state: 'visible', timeout: 60_000 })
  await beat(1000)
  await page.mouse.wheel(0, 700)
  await beat(read)
}

const browser = await chromium.launch()
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: outDir, size: { width: 1440, height: 900 } },
})
const page = await context.newPage()

try {
  // --- act one: the mechanisms, on a corpus with known ground truth --------

  await page.goto(`${UI}/?products=${HERB_PRODUCTS}`, { waitUntil: 'networkidle' })
  await beat(5000)
  mark('open')

  await page.mouse.wheel(0, 300)
  await beat(3000)
  await page.getByRole('button', { name: 'fields and written text' }).click()
  await beat(1500)
  await page.getByRole('button', { name: 'use the one shipped here' }).click()
  await beat(2000)
  mark('choices')

  await page.getByRole('button', { name: 'clear', exact: true }).click()
  await waitForStep(page, 'clear')
  await beat(1800)
  mark('cleared')

  await page.getByRole('button', { name: 'build', exact: true }).click()
  await waitForStep(page, 'build')
  await beat(1200)
  await page.mouse.wheel(0, 520)
  await beat(4000)
  mark('seed built')

  await page.getByRole('button', { name: 'start asking questions' }).click()
  await beat(2200)

  await ask(page, 'who owns atlas migration?', 8000)          // disagreement settled
  mark('conflict')

  await page.locator('input[type=date]').fill('2026-03-15')
  await beat(1000)
  await ask(page, 'who owns atlas migration?', 6500)           // the same question, in March
  mark('as of')

  await page.getByRole('button', { name: 'clear date' }).click()
  await beat(600)
  await ask(page, 'what is the budget for atlas migration?', 6500)   // refusal
  mark('refusal')

  // --- act two: a real published dataset ----------------------------------

  await page.mouse.wheel(0, -6000)
  await beat(500)
  await page.getByRole('button', { name: 'load data' }).click()
  await beat(1500)
  await page.mouse.wheel(0, 1400)
  await beat(3500)
  mark('herb card')

  await page.getByRole('button', { name: 'use the example dataset' }).click()
  await waitForStep(page, 'herb build')
  await beat(1500)
  await page.mouse.wheel(0, 400)
  await beat(4500)
  mark('herb built')

  await page.getByRole('button', { name: 'ask', exact: true }).click()
  await beat(2500)

  await ask(page, 'who authored the actiongenie market research report?', 7500)
  mark('herb answer')

  await ask(page, 'what is the budget for actiongenie?', 6500)
  mark('herb refusal')

  // --- close on the explanation page --------------------------------------

  await page.mouse.wheel(0, -6000)
  await beat(500)
  await page.getByRole('button', { name: 'how it works' }).click()
  await beat(3200)
  for (const scroll of [620, 660, 660]) {
    await page.mouse.wheel(0, scroll)
    await beat(2400)
  }
  await beat(1200)
  mark('explainer')
} finally {
  await context.close()
  await browser.close()
}

console.log(`\nfootage written to ${outDir}`)
