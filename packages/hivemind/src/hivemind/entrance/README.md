# hivemind.entrance

The entrance package is the Hive Entrance: two listeners, loopback (always on) and remote (only
when explicitly exposed), serving the Landing Board (the versioned public API contract), device
enrolment, auth, push delivery, exposure control and the human inbox. Approval routes never
exist on the remote listener.
