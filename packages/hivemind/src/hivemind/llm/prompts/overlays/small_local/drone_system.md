<!--
Overlay: small_local, for the drone_system prompt. Why: a small local model reads the Drone's long
brief and tends to plan several steps in prose or to call more than one tool in a single reply,
which the tool loop then has to reject. This restates the Drone's one decision per turn in the
form a small model follows best. Portable: plain text only.
-->
## Drone reminder

- Look at the brief's acceptance criteria. Pick the one next step that moves toward them.
- Make that single tool call now. Do not explain it first.
- After the result, decide again. When every criterion is met, report in one or two sentences what
  you did and observed.
