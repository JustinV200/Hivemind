// Flat ESLint configuration for the Observation Hive front end.
//
// Combines typescript-eslint's recommended rules with the react-hooks plugin, then layers
// eslint-config-prettier last so formatting rules never fight with Prettier (Prettier owns
// formatting; ESLint owns correctness). codingrules section 2 forbids `any`, so that rule is
// promoted from typescript-eslint's default "warn" to a hard "error" here.
//
// Fits into the Hive:
//   Layer 7 (observation-web). Read by `eslint` via the `pnpm lint` script. Nothing in the Hive
//   imports this file; it is tooling configuration, not shipped code.
//
// Key invariants:
//   - eslint-config-prettier stays last in the array so it can turn off every conflicting
//     stylistic rule the configs before it turned on.
//   - node_modules and dist are ignored so generated and vendored code is never linted.
//
// See Also:
//   - .prettierrc for the formatting rules this config defers to.
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  // Generated and vendored trees are never hand-edited, so linting them is pure noise.
  { ignores: ['dist/**', 'node_modules/**'] },
  ...tseslint.configs.recommended,
  {
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // codingrules section 2: "No any." A warning is easy to ignore; make it a build failure.
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
  prettier,
);
