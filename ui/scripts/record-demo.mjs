/**
 * Record the demo footage of the live UI, under 3 minutes.
 *
 *   cd ui && node scripts/record-demo.mjs
 *
 * Two acts, because they prove different things:
 *
 *   Act one, a small corpus with the curated vocabulary. Twenty-eight documents
 *   whose contradictions, aliases and gaps are known, so the mechanisms are
 *   visible: a disagreement settled, the same question answered as of an earlier
 *   date, and a refusal.
 *
 *   Act two, Salesforce HERB. A published dataset nobody tailored for this, to
 *   show it works on real data and still refuses when the answer is absent.
 *
 * Three things make the footage watchable rather than merely correct:
 *
 *   A visible cursor, injected into the page, because Playwright's recording does
 *   not draw one and clicks otherwise appear to happen by themselves.
 *
 *   Pointer movement in steps and scrolling with smooth behaviour, so the eye can
 *   follow what is being pointed at.
 *
 *   Each answer scrolled to the top of the viewport. Scrolling by a fixed amount
 *   pushed the verdict and the answer itself off screen, which is precisely the
 *   part a viewer needs to read.
 *
 * Silent by design: this is footage, narration goes on top.
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

/** A cursor the recording can see, and scrolling that eases instead of jumping. */
const CURSOR = `
  const dot = document.createElement('div')
  dot.style.cssText = [
    'position:fixed', 'width:20px', 'height:20px', 'left:-60px', 'top:-60px',
    'border:1px solid #cc6437', 'border-radius:50%',
    'background:rgba(204,100,55,0.14)', 'box-sizing:border-box',
    'pointer-events:none', 'z-index:2147483647',
    'transition:width .09s ease, height .09s ease',
  ].join(';')
  const attach = () => document.documentElement.appendChild(dot)
  if (document.documentElement) attach(); else addEventListener('DOMContentLoaded', attach)
  addEventListener('mousemove', (e) => {
    dot.style.left = (e.clientX - 10) + 'px'
    dot.style.top = (e.clientY - 10) + 'px'
  }, true)
  addEventListener('mousedown', () => { dot.style.width = '12px'; dot.style.height = '12px' }, true)
  addEventListener('mouseup', () => { dot.style.width = '20px'; dot.style.height = '20px' }, true)
`

let cursor = { x: 720, y: 470 }

async function waitForStep(page, label, timeout = 300_000) {
  const begin = Date.now()
  while (Date.now() - begin < timeout) {
    if (await page.locator('.failed').count()) throw new Error(`${label} failed`)
    if (!(await page.locator('.working').count())) return
    await beat(400)
  }
  throw new Error(`${label} timed out`)
}

/** Move the pointer the way a hand would, then settle before acting. */
async function glide(page, locator, pause = 240) {
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (!box) throw new Error('nothing to point at')
  const to = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  const distance = Math.hypot(to.x - cursor.x, to.y - cursor.y)
  await page.mouse.move(to.x, to.y, { steps: Math.max(12, Math.round(distance / 16)) })
  cursor = to
  await beat(pause)
}

async function press(page, locator, pause = 240) {
  await glide(page, locator, pause)
  await locator.click()
}

async function scrollBy(page, top, settle = 550) {
  await page.evaluate((y) => window.scrollBy({ top: y, behavior: 'smooth' }), top)
  await beat(settle)
}

async function toTop(page, settle = 650) {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
  await beat(settle)
}

/** Put an element at the top of the viewport so nothing important is cut off. */
async function bringToTop(page, locator, offset = 80, settle = 700) {
  await locator.evaluate((el, off) => {
    const y = el.getBoundingClientRect().top + window.scrollY - off
    window.scrollTo({ top: y, behavior: 'smooth' })
  }, offset)
  await beat(settle)
}

async function ask(page, question, read = 7000) {
  const box = page.locator('input.ask')
  await press(page, box, 300)
  await box.fill('')
  await box.type(question, { delay: 26 })
  await beat(250)
  await press(page, page.getByRole('button', { name: 'send' }), 250)

  const turn = page.locator('.turn').last()
  await turn.waitFor({ state: 'visible', timeout: 60_000 })
  await beat(400)
  await bringToTop(page, turn)      // the verdict and the answer, both readable
  await beat(read * 0.55)
  await scrollBy(page, 400)         // then the sources underneath it
  await beat(read * 0.45)
}

const browser = await chromium.launch()
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: outDir, size: { width: 1440, height: 900 } },
})
await context.addInitScript(CURSOR)
const page = await context.newPage()

try {
  // --- act one: the mechanisms, on a corpus with known ground truth --------

  await page.goto(`${UI}/?products=${HERB_PRODUCTS}`, { waitUntil: 'networkidle' })
  await page.mouse.move(cursor.x, cursor.y)
  await beat(2600)
  await page.mouse.move(420, 300, { steps: 26 })
  cursor = { x: 420, y: 300 }
  await beat(1100)
  mark('open')

  await scrollBy(page, 300)
  await press(page, page.getByRole('button', { name: 'fields and written text' }), 450)
  await beat(900)
  await press(page, page.getByRole('button', { name: 'use the one shipped here' }), 450)
  await beat(1100)
  mark('choices')

  await press(page, page.getByRole('button', { name: 'clear', exact: true }))
  await waitForStep(page, 'clear')
  await beat(1100)
  mark('cleared')

  await press(page, page.getByRole('button', { name: 'build', exact: true }))
  await waitForStep(page, 'build')
  await beat(900)
  await scrollBy(page, 460)
  await beat(2600)
  mark('seed built')

  await press(page, page.getByRole('button', { name: 'start asking questions' }))
  await beat(1300)

  // a question the data answers, where two sources disagreed
  await ask(page, 'who owns atlas migration?', 5200)
  mark('conflict')

  // the same question, as the data stood in March
  const date = page.locator('input[type=date]')
  await press(page, date, 300)
  await date.fill('2026-03-15')
  await beat(900)
  await ask(page, 'who owns atlas migration?', 4200)
  mark('as of')

  // a question the data cannot answer
  await press(page, page.getByRole('button', { name: 'clear date' }), 300)
  await beat(400)
  await ask(page, 'what is the budget for atlas migration?', 4400)
  mark('refusal')

  // --- act two: a real published dataset ----------------------------------

  await toTop(page)
  await press(page, page.getByRole('button', { name: 'load data' }))
  await beat(1100)
  const herb = page.getByRole('button', { name: 'use the example dataset' })
  await glide(page, herb, 1600)
  mark('herb card')

  await herb.click()
  await waitForStep(page, 'herb build')
  await beat(900)
  await scrollBy(page, 360)
  await beat(2800)
  mark('herb built')

  await press(page, page.getByRole('button', { name: 'ask', exact: true }))
  await beat(1300)

  await ask(page, 'who authored the actiongenie market research report?', 5200)
  mark('herb answer')

  // --- close on the explanation page --------------------------------------

  await toTop(page, 800)
  await press(page, page.getByRole('button', { name: 'how it works' }))
  await beat(1900)
  for (const step of [580, 620, 620]) {
    await scrollBy(page, step, 1900)
  }
  await beat(900)
  mark('explainer')
} finally {
  await context.close()
  await browser.close()
}

console.log(`\nfootage written to ${outDir}`)
