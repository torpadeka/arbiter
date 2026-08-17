import { chromium } from 'playwright'
const b = await chromium.launch()
const p = await (await b.newContext({ viewport: { width: 1280, height: 820 } })).newPage()
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await p.waitForTimeout(4000)
await p.screenshot({ path: '../recordings/bg-full.png' })
await p.screenshot({ path: '../recordings/bg-region.png', clip: { x: 1050, y: 180, width: 220, height: 200 } })
console.log(await p.evaluate(() => {
  const v = document.querySelector('video.atmosphere')
  return { playing: v && !v.paused, opacity: getComputedStyle(v).opacity,
           filter: getComputedStyle(v).filter, veil: getComputedStyle(document.querySelector('.veil')).opacity }
}))
await b.close()
