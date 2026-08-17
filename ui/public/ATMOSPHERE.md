# Optional ambient backdrop

Drop a file in this folder and the UI picks it up on load. Nothing else to wire.

    atmosphere.mp4    preferred, played muted and looped
    atmosphere.jpg    still fallback (atmosphere.png also works)

With neither present the UI stays flat void, which is also a valid reading of
the style: the void is the brand.

## What to look for

Ambient, not illustrative. The image is blurred to 50px, so it reads as tone
rather than subject matter.

- dark and low key, sitting in the same tonal register as #0b0b0b
- warm ember notes only: rust, amber, dull orange. No blue or green casts
- abstract texture: smoke, ink in water, liquid marble, embers, molten metal
- no people, no products, no screenshots, no text

Good search terms: `dark smoke abstract`, `ink in water black`, `embers macro`,
`molten metal dark`, `liquid marble dark`, `black abstract texture`.

Sources with licences that permit commercial use without attribution:
Unsplash, Pexels, Pixabay. For video: Pexels Video, Coverr, Mixkit.

## Sizing

Blur destroys detail, so resolution barely matters and small files are better.

- image: 1600px wide, JPEG quality 70, aim for under 300 KB
- video: 720p, 5 to 10 seconds, silent, H.264, aim for under 3 MB

A large video is the one real risk: it competes with a screen recording for CPU.
If the demo stutters, use the still.

## Treatment

Set in `src/styles.css`, and it belongs to the footage rather than being fixed.
Measure before adjusting: screenshot an empty strip of the page and read its
luma range.

- smooth colour fields take a heavy blur but need darkening, since a bright
  wash reads as grey rather than void
- thin bright marks need almost no blur, because blur spreads them over ten
  times their width and destroys the peak that made them visible
- saturation stays low either way, so the backdrop never introduces a second
  accent colour alongside ember rust

The shipped clip is soft colour waves at 26px blur, 0.4 saturation, 0.5
brightness and 0.5 opacity, behind a 55% void veil.
