# hivemind.exoskeleton.compound_eye

The Exoskeleton's vision: see a Cell's display.

- `base.py`: the `CompoundEye` protocol (`screen`, `capture`, `region_digest`).
- `x11.py`: `X11CompoundEye`, ImageMagick's `import` run through the Cell's session: PNG for a
  frame, raw 8-bit RGB for a region digest, so comparing a region before and after an action
  never decodes or keeps an image.
- `fake.py`: `FakeScreen` (a background and painted rectangles) and `FakeCompoundEye`, whose region
  digest depends only on the region's pixels.
