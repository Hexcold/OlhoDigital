import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Escuta em todas as interfaces para o celular na mesma rede alcançar o
    // cliente. O proxy abaixo continua apontando para 127.0.0.1 de propósito:
    // quem fala com a API é o servidor do Vite, que roda nesta máquina — o
    // telefone só precisa chegar ao Vite.
    host: true,
    // O cliente chama sempre `/api/...`, e quem resolve é o proxy. Assim o
    // mesmo caminho funciona no desenvolvimento (Vite em 5173, API em 8000) e
    // em produção self-hosted, onde os dois ficam na mesma origem atrás de um
    // servidor só — sem `if (dev)` espalhado pelo código nem URL absoluta
    // compilada dentro do bundle.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (caminho) => caminho.replace(/^\/api/, ""),
      },
    },
  },
});
