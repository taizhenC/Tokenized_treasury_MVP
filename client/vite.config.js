import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // In development, Vite forwards /api requests to the FastAPI server, so the
    // browser talks to one origin and we need no CORS setup. In production,
    // the API serves the built frontend itself, so the same paths keep working.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
