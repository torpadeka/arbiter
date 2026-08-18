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
- video: 540p to 720p, silent, H.264, aim for under 1 MB. Length matters less than
  the seam: check that the last frame matches the first, or the loop will jump

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

The shipped clip is soft colour waves at 8px blur, 0.55 saturation, 0.9
brightness and 0.7 opacity, behind a 40% void veil. It runs the full 30 seconds
rather than a trimmed excerpt, because the footage returns to its opening frame
and so repeats without a visible jump. Check that before trimming anything: an
excerpt that ends mid-motion cuts back to the start every loop, and once you
have noticed it you cannot unsee it.
