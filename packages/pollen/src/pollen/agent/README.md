# pollen.agent

The agent package is the gateway loop: connect out to the Queen, prove identity with a signed first
frame, send heartbeats and capacity reports, admit only frames a trusted node signed for this
device, and hand each request to the executor that serves its payload type. It persists the
Queen's address and honours a signed QueenMoved message if Supersedure relocates the Hive Stand. It
also keeps the packet's two watchdogs: the dead-man switch that releases every lease when the Queen
goes silent, and the status document `pollen status` reads.
