# hivemind.pheromone

The pheromone package is the Pheromone Trail, the Hive's append-only audit log. Every mutation
anywhere in the Hive leaves a PheromoneEvent here, split into per-node segments that sync when a
Real Cell (an existing device the Hive borrows) reconnects.
