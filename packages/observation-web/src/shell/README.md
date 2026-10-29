# observation-web/src/shell

What the human sees after login: the head (`head/`: the top bar with the mark, the connection
word, the theme toggle and, from 720 px, the device line and log out; the step-up banner; the
summary strip as four comb cells; the notice bar fed by the push stream), the nav (a bottom bar
on a phone whose More panel holds the rest of the items, the device line and log out, a rail from
720 px) and the outlet that draws the view for the current route inside a `ViewBoundary`.
`theme/` keeps the viewer's light or dark choice on `<html>` and in storage; `not_found.tsx` is
the plain page with a way back. The shell also starts the live feeds when it mounts and stops them
when it unmounts, and mounts the Help view's guided tour (`src/views/help/tour/`) once, so the
tour outlives the route changes its steps make; the strip, the connection word, the nav and the
theme toggle carry the `data-tour` attributes its steps name.
