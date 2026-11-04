#!/usr/bin/env bash
# Cria um link temporário para o sistema, protegido por login.
#
#   ./compartilhar.sh
#
# Cada pessoa entra com a própria conta, e a conta de impressão só alcança a tela de
# impressão. O link funciona enquanto este terminal estiver aberto e o computador
# ligado, e muda a cada vez.
set -euo pipefail
cd "$(dirname "$0")"

command -v cloudflared >/dev/null || { echo "instale: sudo pacman -S cloudflared"; exit 1; }

./.venv/bin/python -m api.usuarios listar | grep -q . || {
    echo "crie uma conta antes: ./.venv/bin/python -m api.usuarios adicionar SEU_NOME --papel admin"
    exit 1
}

[ -f web/dist/index.html ] || (cd web && npm ci && npm run build)

PORTA="${OLHODIGITAL_PORTA:-8020}"
./.venv/bin/uvicorn api.servir:aplicacao --host 127.0.0.1 --port "$PORTA" --log-level warning &
SERVIDOR=$!
trap 'kill "$SERVIDOR" 2>/dev/null' EXIT

# O link só abre depois de confirmar que quem responde nesta porta é este sistema. Com
# a porta ocupada, o servidor não sobe, e o túnel publicaria o que estivesse nela.
PRONTO=""
for _ in $(seq 1 120); do
    kill -0 "$SERVIDOR" 2>/dev/null || break
    if curl -fs "http://127.0.0.1:$PORTA/api/saude" | grep -q '"pipeline"'; then
        PRONTO=1
        break
    fi
    sleep 0.5
done
if [ -z "$PRONTO" ] || ! kill -0 "$SERVIDOR" 2>/dev/null; then
    echo "o sistema não subiu na porta $PORTA, então o link não foi criado."
    echo "se a porta estiver ocupada, use outra: OLHODIGITAL_PORTA=8021 ./compartilhar.sh"
    exit 1
fi

echo "Copie o endereço https://...trycloudflare.com que vai aparecer abaixo."
cloudflared tunnel --url "http://127.0.0.1:$PORTA"
