# observation-web/src/styles

The Observation Hive's CSS. `tokens.css` holds every colour, size and space as custom properties
in both themes (`docs/observation/design.md` section 3: the wax and comb surfaces, the honey
accent, the propolis chrome of the bar and nav, the card radius, the one motion duration and the
head's height, which the shell measures); `base.css` the element defaults, the focus ring, the
buttons (and the outlined `.button--danger`), the empty state (a comb cell) and the
screen-reader-only block; `shell.css` the shell grid, the sticky head and the nav at phone, tablet
and desktop widths, the rail sized under the head's measured height, the nav's focus ring in the
chrome accent; `head.css` the head's parts, the top bar with its comb and its own chrome-accent
focus ring, the step-up banner, the summary strip as one row of comb cells sharing the band and
the notice bar; `components.css` the badges, status dot (with its pulse), gauge, notices, the
mark, the comb pattern, the shared `--hex-cut` clip, the `.comb-cell` every panel and form
carries (the cut with a stroked ring, `--hex-ring`, since a border would stop at each chamfer)
and the `.section-heading` every panel and top-level section heading carries (the one hairline
rhythm of design.md section 6); `thoughts.css` the Thoughts view. The login screen's card is
styled by `src/views/login/login.css`. Components use tokens only.
