# pollen.lease

The lease package is the device-side half of a Real Cell lease: its scratch directory and quota,
the process groups it started, the record that lets a release put every changed path back, and the
silence clock behind the dead-man switch if the Queen goes quiet.
