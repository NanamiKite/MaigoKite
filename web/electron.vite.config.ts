import { defineConfig } from 'vite'
import { resolve } from 'node:path'

export default defineConfig(({ mode }) => ({
  // Only the main process reads the host environment. Never inline its values.
  define:
    mode === 'preload'
      ? {}
      : {
          'process.env': 'process.env',
          'global.process.env': 'global.process.env',
          'globalThis.process.env': 'globalThis.process.env',
        },
  build: {
    // Electron 44 embeds Node 24 / Chromium 152. Preload still runs sandboxed.
    target: mode === 'preload' ? 'chrome152' : 'node24',
    outDir: 'dist-electron',
    emptyOutDir: mode !== 'preload',
    sourcemap: true,
    rollupOptions: {
      external: [
        'electron',
        'node:child_process',
        'node:crypto',
        'node:fs',
        'node:net',
        'node:path',
        'node:timers/promises',
        'node:url',
      ],
      input: resolve(__dirname, `src/electron/${mode === 'preload' ? 'preload' : 'main'}.ts`),
      // Sandboxed preload cannot require shared local chunks. Bundle it alone.
      output: {
        format: 'cjs',
        inlineDynamicImports: true,
        entryFileNames: mode === 'preload' ? 'preload.cjs' : 'main.cjs',
      },
    },
  },
}))
