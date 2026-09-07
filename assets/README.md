# assets

Static images used by the documentation, not by the running system. Today that is only
`hivemind-banner.svg`, the banner the root `README.md` shows at the top. Nothing here is loaded by
any package at runtime: the Observation Hive (the live web UI) keeps its own assets under
`packages/observation-web/`, and Virtual Cell images live under `images/`. Add a file here only
when a document in this repository embeds it.
