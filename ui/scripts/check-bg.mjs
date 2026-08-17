import { chromium } from 'playwright'
const b = await chromium.launch()
const p = await (await b.newContext({ viewport: { width: 1280, height: 860 } })).newPage()
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await p.waitForTimeout(3500)
await p.evaluate(() => window.scrollBy({ top: 520 }))
await p.waitForTimeout(1200)
await p.screenshot({ path: '../recordings/ui-check.png' })
console.log(await p.evaluate(() => {
  const v = document.querySelector('video.atmosphere')
  return { videoPresent: !!v, playing: v && !v.paused, t: v?.currentTime?.toFixed(1),
           rootClass: document.querySelector('#root > div')?.className }
}))
await b.close()
