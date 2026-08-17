/**
 * Record a walkthrough of the live UI.
 *
 *   cd ui && node scripts/record-demo.mjs
 *
 * Drives the real interface in a real browser and records the viewport, so what
 * lands on screen is the running system rather than a mockup. Silent by design:
 * this produces the footage, narration goes on top afterwards.
 *
 * Requires the stack up (scripts\hydradb_up.ps1), the API on 8000 and Vite on
 * 5173. It clears and rebuilds the graph as part of the demonstration, which is
 * the point, so expect the graph to hold the induced seed corpus when it ends.
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

/** Wait for a job log to finish rather than guessing at a duration. */
async function waitForStep(page, label, timeout = 240_000) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    const working = await page.locator('.working').count()
    const failed = await page.locator('.failed').count()
    if (failed > 0) throw new Error(`${label} reported a failure`)
    if (working === 0) return
    await beat(500)
  }
  throw new Error(`${label} did not finish within ${timeout / 1000}s`)
}

async function ask(page, question, dwell = 5200) {
  const box = page.locator('input.ask')
  await box.click()
  await box.fill('')
  await box.type(question, { delay: 45 })
  await beat(500)
  await page.getByRole('button', { name: 'send' }).click()
  await page.locator('.turn').last().waitFor({ state: 'visible', timeout: 60_000 })
  await beat(dwell)
  await page.mouse.wheel(0, 900)
  await beat(1800)
}

const browser = await chromium.launch()
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  recordVideo: { dir: outDir, size: { width: 1440, height: 900 } },
})
const page = await context.newPage()

try {
  // --- the product, cold -------------------------------------------------
  await page.goto(UI, { waitUntil: 'networkidle' })
  await beat(3000)

  // --- load a corpus, live ------------------------------------------------
  await page.getByRole('button', { name: 'load data' }).click()
  await beat(1500)

  await page.getByRole('button', { name: 'clear', exact: true }).click()
  await waitForStep(page, 'clear')
  await beat(2200)

  await page.getByRole('button', { name: 'read the files' }).click()
  await waitForStep(page, 'read the files')
  await beat(1200)
  await page.mouse.wheel(0, 700)
  await beat(5000)          // the derived relationships, with counted cardinality

  await page.getByRole('button', { name: 'build', exact: true }).click()
  await waitForStep(page, 'build')
  await beat(1500)
  await page.mouse.wheel(0, 700)
  await beat(3500)

  // --- ask it things ------------------------------------------------------
  await page.getByRole('button', { name: 'start asking questions' }).click()
  await beat(2500)

  const suggested = await page.locator('.suggest button').allTextContents()
  console.log('suggestions offered:', suggested)

  // A disagreement first: the most revealing thing this system does.
  const conflict = page.locator('.suggest button').first()
  if (await conflict.count()) {
    await conflict.click()
    await page.locator('.turn').last().waitFor({ state: 'visible', timeout: 60_000 })
    await beat(6000)
    await page.mouse.wheel(0, 900)
    await beat(2500)
  }

  await ask(page, 'who is eng-4471 assigned to?')
  await ask(page, 'what does @soham work on?')
  await ask(page, 'what is the budget for beacon?', 6000)

  // --- how it works -------------------------------------------------------
  await page.mouse.wheel(0, -4000)
  await beat(800)
  await page.getByRole('button', { name: 'how it works' }).click()
  await beat(3000)
  for (let i = 0; i < 7; i++) {
    await page.mouse.wheel(0, 620)
    await beat(2100)
  }
  await beat(2500)
} finally {
  await context.close()   // finalises the video file
  await browser.close()
}

console.log(`\nfootage written to ${outDir}`)
