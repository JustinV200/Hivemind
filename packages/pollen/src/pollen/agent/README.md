# pollen.agent

The agent package is the gateway loop: connect out to the Queen, send heartbeats, and hand a
session to the device's Warden. It persists the Queen's address and honours a signed QueenMoved
message if Supersedure relocates the Hive Stand.
