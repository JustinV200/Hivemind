# hivemind.wardens

The wardens package is the Warden: the per-Cell supervisor that spawns and supervises Workers on
exactly one Cell. It covers Warden state, its inbox, its Autopilot (which never awaits a model)
and Awake modes, spawning, its local model pool, offline handling and read-only Watch mode; a
Warden never provisions Cells itself.
