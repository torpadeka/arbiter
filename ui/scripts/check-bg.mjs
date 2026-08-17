import { chromium } from 'playwright'
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1280, height: 820 } })
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await p.waitForTimeout(3500)
// Simulate the failure the user is seeing: block playback entirely.
await p.evaluate(() => { const v = document.querySelector('video.atmosphere'); v.pause(); v.currentTime = 0 })
await p.waitForTimeout(800)
await p.screenshot({ path: '../recordings/bg-paused.png', clip: { x: 0, y: 0, width: 1280, height: 400 } })
console.log('paused-state check written')
await b.close()
