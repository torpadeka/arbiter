import { chromium } from 'playwright'
const b = await chromium.launch()
const p = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage()
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await p.waitForTimeout(4000)
await p.screenshot({ path: '../recordings/bg-check.png' })
console.log(await p.evaluate(() => {
  const v = document.querySelector('video.atmosphere')
  return { playing: v && !v.paused, t: v?.currentTime?.toFixed(1), opacity: getComputedStyle(v).opacity }
}))
await b.close()
