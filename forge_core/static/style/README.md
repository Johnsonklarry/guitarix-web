# Forge Shared Styles

## Shared Stylesheet Load Order
To ensure proper cascading and theme token resolution, load stylesheets in the following order:
1. `theme-kit/themes.css`
2. `forge_core/static/layout.css`
3. `forge_core/static/designer.css`

## Component Scoping
Every shared component class lives under the `.forge-ui` scope.
