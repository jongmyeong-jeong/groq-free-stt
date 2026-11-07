import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'

const webRoot = __dirname
const repoRoot = path.resolve(__dirname, '..')
const webNodeModules = path.resolve(webRoot, 'node_modules')

// Explicit alias map for packages imported from ../tests/frontend/. Vite/vitest
// resolves bare imports by walking up from the test file's directory; because
// tests live ABOVE web/, the default resolver misses web/node_modules. We map
// each dep used by frontend tests explicitly to its hoisted location in
// web/node_modules.
const testDepAliases = Object.fromEntries(
  [
    '@testing-library/react',
    '@testing-library/jest-dom',
    '@testing-library/user-event',
    'react',
    'react-dom',
    'react-dom/client',
    'react/jsx-runtime',
    'react/jsx-dev-runtime',
    'react-router-dom',
    '@tanstack/react-query',
    'vitest',
    'zustand',
  ].map((pkg) => [pkg, path.resolve(webNodeModules, pkg)]),
)

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(webRoot, './src'),
      ...testDepAliases,
    },
  },
  server: {
    fs: {
      allow: [repoRoot],
    },
  },
  test: {
    root: webRoot,
    environment: 'jsdom',
    globals: true,
    setupFiles: [path.resolve(webRoot, 'src/test/setup.js')],
    include: [
      path.resolve(repoRoot, 'tests/frontend/**/*.test.{js,jsx}').replace(/\\/g, '/'),
      path.resolve(webRoot, 'src/**/*.test.{js,jsx}').replace(/\\/g, '/'),
    ],
  },
})
