import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const target = new URL(process.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8893');
if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(target.hostname)
  || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
  throw new Error('VITE_BACKEND_TARGET 必须是本机 Flask 服务的 HTTP origin。');
}

export default defineConfig({
  base: '/desktop/',
  plugins: [vue()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: {
      '/desktop/api': {
        target: target.origin,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (proxyReq, req) => {
            const host = req.headers.host || '';
            if (/^(?:127\.0\.0\.1|localhost):\d+$/.test(host) && req.headers.origin === 'http://' + host) {
              proxyReq.setHeader('Origin', target.origin);
            }
          });
        },
      },
    },
  },
});
