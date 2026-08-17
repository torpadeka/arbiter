import { chromium } from 'playwright'
const b = await chromium.launch()
const p = await (await b.newContext({ viewport: { width: 1280, height: 820 } })).newPage()
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await p.waitForTimeout(3500)
await p.screenshot({ path: '../recordings/bg-full.png' })
console.log(await p.evaluate(() => {
  const v = document.querySelector('video.atmosphere')
  return { playing: !v.paused, filter: getComputedStyle(v).filter, opacity: getComputedStyle(v).opacity,
           veil: getComputedStyle(document.querySelector('.veil')).opacity }
}))
await b.close()
