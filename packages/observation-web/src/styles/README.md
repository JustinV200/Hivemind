# observation-web/src/styles

The Observation Hive's CSS. `tokens.css` holds every colour, size and space as custom properties
in both themes (`docs/observation/design.md` section 3: the wax and comb surfaces, the honey
accent, the propolis chrome of the bar and nav, the card radius and the one motion duration);
`base.css` the element defaults, the buttons (and the outlined `.button--danger`), the empty state
and the screen-reader-only block; `shell.css` the layout at phone, tablet and desktop widths with
the strip's hex cells, the notice bar and the login card on its comb; `components.css` the badges,
status dot (with its pulse), gauge, notices, the mark, the comb pattern and the shared `--hex-cut`
clip; `thoughts.css` the Thoughts view. Components use tokens only.
