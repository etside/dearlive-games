module.exports = {
  root: true,
  env: { browser: true, es2021: true, node: true },
  parserOptions: { ecmaVersion: 2021, sourceType: 'script' },
  extends: ['eslint:recommended'],
  overrides: [{
    files: ['packages/api/src/**/*.ts'],
    parser: '@typescript-eslint/parser',
    parserOptions: { ecmaVersion: 2021, sourceType: 'module' },
    rules: {
      // TypeScript declarations and interface parameters are not runtime values.
      'no-unused-vars': 'off',
      // TypeScript overload and declaration merging is valid in this package.
      'no-redeclare': 'off',
    },
  }],
  ignorePatterns: ['node_modules/', 'packages/api/dist/'],
};
