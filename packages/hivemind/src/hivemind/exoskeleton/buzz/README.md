# hivemind.exoskeleton.buzz

The Exoskeleton's hearing and voice.

- `base.py`: the `Buzz` protocol (`listen`, `say`) and `Recording` (WAV bytes, never logged).
- `clips.py`: `load_clip`, the one check every backend applies before `say` plays a clip: inside
  scratch, a PCM WAV, at most `MAX_SAY_S` long.
- `pulseaudio.py`: `PulseAudioBuzz` over the lease's own PulseAudio server (`PulseServer`):
  `parec` from the speaker sink's monitor, stopped on schedule by `timeout`, and `paplay` into the
  microphone sink.
- `fake.py`: `FakeBuzz`, scripted recordings and a record of what was said.
