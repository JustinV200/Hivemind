# Observation Hive design notes

Design notes the Observation Hive (the read-only web and Android UI onto a running Hive) follows:
its views, how it subscribes to live updates, and how it stays read-only apart from its one write
path, the chatbox into the Queen's inbox. First populated in phase 12.

The layout, colour tokens, components and views every Observation Hive screen follows are in
[`design.md`](design.md); the stack and stream handling behind them are in
[ADR-0046](../adr/0046-observation-hive-dashboard-stack.md).
