<!--
Overlay: small_local, for the decompose_goal prompt. Why: a small local model asked to plan tends
to produce one vague subtask, or a subtask with no way to tell it is done. The planner's output is
checked, and a subtask without acceptance criteria is rejected, so this names the two things the
model most often drops. Portable: plain text only.
-->
## Planning reminder

- Prefer few, small subtasks over one large one.
- Give every subtask an acceptance criterion a person could check by looking at one file or one
  command's output.
- Do not add subtasks the goal did not ask for.
