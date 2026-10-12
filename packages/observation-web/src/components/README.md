# observation-web/src/components

Shared components used across more than one view: the tier and mask badges, the status dot, the
gauge and the view title row, with the live SVG diagrams of Cells and Forage to follow in their own
steps. Each is one file, takes typed props from the generated Landing Board types and holds no
state. Their look is set by the tokens in `src/styles/` and `docs/observation/design.md`. The
summary strip is part of the shell and lives in `src/shell/`.
