# hivemind.entrance.images

Images in at the Landing Board (roadmap step 12.12, ADR-0047, codingrules 8.15: "images a client
sends are deposited as Nectar with clearance C2"). An enrolled device, an Android share sheet
handing over a photo or a glasses companion app, sends the Hive a picture, and the Hive keeps it
as raw material. Nothing in the Entrance opens, decodes or returns the image.

## One route, one door

```
POST /v1/nectar/images?caption=…&task_id=…
 raw body + Content-Type: image/png | image/jpeg | image/webp
   │ body limit: the whole body, up to the manifest ceiling (32 MiB), before the signature
   │ gate: signed, APPROVED, entrance:submit (no c2: the answer holds no content)
   └──────────► deposit_image(services, caller, Upload)
                 1. Content-Type is one of the three              415
                 2. size within [entrance] max_image_bytes        413
                 3. the body begins as its format does, non-empty  422
                 4. the device's image bytes for the minute,
                    charged the image's whole size                 429
                 5. deposit as KeptImage: C2, the device, when, the caption, the task,
                    expires_at = now + [entrance] image_keep_hours
                  │
         202 ImageDepositView  (nectar id, size, media type, deposited and expiry times)
```

Every check precedes the deposit and the charge, so a refused image is neither kept nor paid for.
The body limit has already buffered the body when the door runs (the signature covers its SHA-256,
as for the audio route), bounded by the manifest ceiling of 32 MiB; `max_image_bytes` (8 MiB by
default) is the operator's own tighter limit, answered with the usual `ErrorBody`.

## Limits

- **Size.** `[entrance] max_image_bytes`, at most 32 MiB (`IMAGE_BYTES_CEILING`, the route's
  raw-body allowance, so a larger value is refused when the manifest loads).
- **Bytes a minute.** A token bucket per device (`ByteBudget` in `auth/limits`), beside the
  request and audio buckets: `[entrance] image_bytes_per_minute` bytes of capacity refilling at
  that rate, charged each image's whole size. The manifest refuses a budget below
  `max_image_bytes`. An image also costs one request token, like every request.
- **Shape.** Only the first bytes are checked against the claimed format's signature, so a
  mislabelled or empty body is not filed as an image. No decoder runs.

## Images are C2

An image and its caption come from the human. They are kept as `KeptImage` (always C2, with
`repr=False` on the bytes and the caption) through the `MediaNectar` seam that `entrance/voice`
defines and keeps clips in: the composition root hands voice and images the same instance, so one
sweep and one set of memory bounds (256 items, 128 MiB) cover both. The seam today is in memory,
so a restart forgets every image; a durable implementation takes the same seam once the Honey
Store's Nectar intake keeps binary items. The one log line (`entrance.image_deposited`) carries the
device, the size and the format; the bytes and the caption reach no log and no trail event.

## Public API

`deposit_image`, `Upload` (the door); `ImageMediaType`, `media_type_of`, `looks_like` (formats);
`ImageDepositView`, `DepositParams` (the Landing Board's shapes); `IMAGE_ROUTES` (the route);
`ImageServices`, `ImageRules` (what `hive serve` builds); and the refusals. The face lists every
name.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/images
```

The tests run a real Entrance and Queen (`builders.entrance.serving`, which serves the image
intake over an in-memory seam read back as `rig.nectar`); `builders.entrance.images` makes bodies
that begin as each format does and sends them signed as sent.
