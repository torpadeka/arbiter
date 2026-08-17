import { chromium } from 'playwright'
const CANDIDATES = [
  { id: 'a', blur: 26, sat: 0.4,  bright: 0.5, op: 0.5,  veil: 0.55 },  // current, invisible
  { id: 'b', blur: 12, sat: 0.5,  bright: 0.8, op: 0.6,  veil: 0.45 },
  { id: 'c', blur: 8,  sat: 0.55, bright: 0.9, op: 0.7,  veil: 0.4 },
  { id: 'd', blur: 5,  sat: 0.6,  bright: 1.0, op: 0.8,  veil: 0.3 },
]
const b = await chromium.launch()
const p = await (await b.newContext({ viewport: { width: 1280, height: 820 } })).newPage()
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await p.waitForTimeout(3000)
for (const c of CANDIDATES) {
  await p.evaluate((c) => {
    const v = document.querySelector('video.atmosphere')
    v.style.filter = `blur(${c.blur}px) saturate(${c.sat}) brightness(${c.bright})`
    v.style.opacity = String(c.op)
    document.querySelector('.veil').style.opacity = String(c.veil)
  }, c)
  await p.waitForTimeout(600)
  // A wide empty band across the page: this is where the eye reads the wash.
  await p.screenshot({ path: `../recordings/tune-${c.id}.png`, clip: { x: 300, y: 170, width: 900, height: 170 } })
}
await b.close()
console.log('screens written')
