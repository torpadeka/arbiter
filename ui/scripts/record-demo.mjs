/**
 * Record 3 minutes of footage of the live UI.
 *
 *   cd ui && node scripts/record-demo.mjs
 *
 * Drives the real interface in a real browser and records the viewport, so what
 * lands on screen is the running system rather than a mockup. Silent by design:
 * this is the footage, narration goes on top.
 *
 * Beats are timed against the narration script, and the total is held just under
 * 180 seconds because anything past the 3 minute mark may not be reviewed. Every
 * dwell is a deliberate reading pause, not padding.
 *
 * Builds with the curated vocabulary and prose reading on, because that is the
 * configuration in which two sources actually disagree, which is the thing worth
 * showing. Requires the stack up, the API on 8000 and Vite on 5173.
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const outDir = resolve(root, 'recordings')
mkdirSync(outDir, { recursive: true })

const UI = process.env.ARBITER_UI || 'http://127.0.0.1:5173'
const beat = (ms) => new Promise((r) => setTimeout(r, ms))
const started = Date.now()
const mark = (label) => console.log(`  ${String((Date.now() - started) / 1000).padStart(6)}s  ${label}`)

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
  await box.type(question, { delay: 38 })
  await beat(400)
  await page.getByRole('button', { name: 'send' }).click()
  await page.locator('.turn').last().waitFor({ state: 'visible', timeout: 60_000 })
  await beat(1200)
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
  // 0:00 the product, cold. Narration covers the problem over this.
  await page.goto(UI, { waitUntil: 'networkidle' })
  await beat(9000)
  mark('opening held')

  // 0:09 the two choices that define what it will read
  await page.mouse.wheel(0, 320)
  await beat(7000)
  await page.getByRole('button', { name: 'fields and written text' }).click()
  await beat(3500)
  mark('choices shown')

  // 0:20 clear, so nobody wonders whether this was pre-loaded
  await page.getByRole('button', { name: 'clear', exact: true }).click()
  await waitForStep(page, 'clear')
  await beat(3000)
  mark('graph cleared')

  // 0:26 build it live
  await page.getByRole('button', { name: 'use the one shipped here' }).click()
  await beat(1500)
  await page.getByRole('button', { name: 'build', exact: true }).click()
  await waitForStep(page, 'build')
  await beat(1500)
  await page.mouse.wheel(0, 520)
  await beat(7000)
  mark('graph built')

  // 0:45 questions
  await page.getByRole('button', { name: 'start asking questions' }).click()
  await beat(3000)

  // the disagreement: the centrepiece
  await ask(page, 'who owns atlas migration?', 11000)
  mark('conflict shown')

  // the same question, as it stood in March
  const date = page.locator('input[type=date]')
  await date.fill('2026-03-15')
  await beat(1200)
  await ask(page, 'who owns atlas migration?', 9000)
  mark('as of shown')

  // clear the date, then the alias hop
  await page.getByRole('button', { name: 'clear date' }).click()
  await beat(800)
  await ask(page, 'who does @soham report to?', 8000)
  mark('alias shown')

  // and the refusal
  await ask(page, 'what is the budget for atlas migration?', 9000)
  mark('refusal shown')

  // 2:15 how it works, for the HydraDB section of the narration
  await page.mouse.wheel(0, -6000)
  await beat(600)
  await page.getByRole('button', { name: 'how it works' }).click()
  await beat(4000)
  for (const scroll of [560, 560, 620, 620, 620, 620]) {
    await page.mouse.wheel(0, scroll)
    await beat(3200)
  }
  await beat(2000)
  mark('explainer scrolled')
} finally {
  await context.close()
  await browser.close()
}

console.log(`\nfootage written to ${outDir}`)
