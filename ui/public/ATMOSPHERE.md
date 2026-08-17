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

## Treatment already applied

`filter: blur(50px) saturate(0.85)`, `opacity: 0.5`, and a flat void veil at 72%
over the top. Cards, log and composer shift to translucent backgrounds so type
keeps contrast. Nothing needs adjusting unless your source is unusually bright,
in which case lower `.atmosphere` opacity in `src/styles.css`.
