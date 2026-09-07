# hivemind.cell

The cell package defines the Cell abstraction shared by every kind of machine the Hive runs work
on: Cell, CellKind (REAL for a borrowed device or VIRTUAL for a provisioned one), CellSession (a
terminal session on it), leases, TaskNeeds and the three security tier enums (AccessLevel,
CombShieldLevel, HoneyClearance). It knows what a Cell is, never how one is made.
