# hivemind.forage

The forage package models capacity as data: HostCapacity, a Seat, RoleFootprint, ForageGrant and
ForageRequest, the Forage map of every source that can serve a model, and the ModelSlot and
Tempo types llm depends on. It never imports llm, so a grant or a routing input can name a model
slot without pulling in the provider machinery.
