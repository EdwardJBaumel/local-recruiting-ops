import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import unicorn from 'eslint-plugin-unicorn'
import { defineConfig, globalIgnores } from 'eslint/config'
import { fileURLToPath } from 'node:url'

// Casing policy (see README.md § Naming):
// - PascalCase: React components/views (src/components, src/views)
// - camelCase: hooks, lib, api, stores, types
// - UPPER_CASE: module-level constants (not env vars)
// - snake_case: API wire fields on types and destructured payloads
// - shadcn primitives under components/ui/ keep lowercase filenames

const namingConvention = [
  'error',
  {
    selector: 'function',
    format: ['camelCase', 'PascalCase'],
  },
  {
    selector: 'variable',
    modifiers: ['const'],
    format: ['camelCase', 'UPPER_CASE', 'PascalCase'],
  },
  {
    selector: 'variable',
    format: ['camelCase', 'snake_case', 'UPPER_CASE', 'PascalCase'],
    leadingUnderscore: 'allow',
  },
  {
    selector: 'default',
    format: ['camelCase'],
    leadingUnderscore: 'allow',
    trailingUnderscore: 'allow',
  },
  {
    selector: 'typeLike',
    format: ['PascalCase'],
  },
  {
    selector: 'property',
    format: null,
    filter: {
      regex:
        '^(__html|Content-Type|ALLOWED_TAGS|ALLOWED_ATTR|@|/api|us-metro|us-state|p-\\d+)$',
      match: true,
    },
  },
  {
    selector: 'property',
    format: ['camelCase', 'snake_case', 'PascalCase', 'UPPER_CASE'],
    leadingUnderscore: 'allow',
  },
  {
    selector: 'typeProperty',
    format: ['camelCase', 'snake_case', 'PascalCase'],
    leadingUnderscore: 'allow',
  },
  {
    selector: 'enumMember',
    format: ['PascalCase', 'UPPER_CASE'],
  },
  {
    selector: 'import',
    format: null,
  },
  {
    selector: 'objectLiteralMethod',
    format: ['camelCase'],
  },
  {
    selector: 'parameter',
    format: ['camelCase', 'snake_case'],
    leadingUnderscore: 'allow',
  },
  {
    selector: 'class',
    format: ['PascalCase'],
  },
]

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    plugins: { unicorn },
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: fileURLToPath(new URL('.', import.meta.url)),
      },
    },
    rules: {
      '@typescript-eslint/naming-convention': namingConvention,
      'unicorn/filename-case': [
        'error',
        {
          cases: {
            camelCase: true,
            pascalCase: true,
          },
          ignore: [
            /^(main|App|setup)\.tsx?$/,
            /\.test\.tsx?$/,
            /\.config\.(ts|js)$/,
          ],
        },
      ],
      // Typed react-hooks rules need projectService; keep off until refactors land.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/incompatible-library': 'off',
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    rules: {
      '@typescript-eslint/naming-convention': 'off',
    },
  },
  {
    files: ['vite.config.ts', 'vitest.config.ts', 'eslint.config.js'],
    rules: {
      '@typescript-eslint/naming-convention': 'off',
    },
  },
  {
    files: ['src/components/**/*.{tsx,ts}', 'src/views/**/*.{tsx,ts}'],
    ignores: ['src/components/ui/**'],
    rules: {
      'unicorn/filename-case': [
        'error',
        {
          case: 'pascalCase',
          ignore: [/\.test\.tsx?$/],
        },
      ],
    },
  },
  {
    files: ['src/components/ui/**/*.{tsx,ts}'],
    rules: {
      'unicorn/filename-case': [
        'error',
        {
          case: 'camelCase',
          ignore: [/\.test\.tsx?$/],
        },
      ],
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    files: ['src/{hooks,lib,api,stores,types}/**/*.{ts,tsx}'],
    rules: {
      'unicorn/filename-case': [
        'error',
        {
          case: 'camelCase',
          ignore: [/\.test\.tsx?$/],
        },
      ],
    },
  },
])
