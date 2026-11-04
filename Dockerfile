# Uma imagem só: o cliente compilado e a API, servidos na mesma porta (api/servir.py).
# Quem sobe é o docker-compose.yml, que traz o Postgres junto.

# --- cliente: o Node só existe neste estágio, não vai para a imagem final
FROM node:24-slim AS cliente
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- servidor
FROM python:3.14-slim-trixie

# rsvg-convert desenha a folha; o poppler lê o PDF do escâner e junta o lote.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      librsvg2-bin poppler-utils fontconfig fonts-noto-core libglib2.0-0t64 \
 && rm -rf /var/lib/apt/lists/*

# A chave impressa foi medida em Noto Sans. O build falha se a folha for sair em
# outra fonte, porque isso mudaria a leitura sem nenhum erro aparente.
COPY docker/fontes.conf /etc/fonts/conf.d/00-olhodigital.conf
RUN fc-cache -f && fc-match "DejaVu Sans:bold" | grep -q "Noto Sans"

ENV PYTHONUNBUFFERED=1 KERAS_BACKEND=torch HOME=/tmp \
    OLHODIGITAL_CABECALHO=/estado/cabecalho.json

WORKDIR /app
COPY requirements.txt ./
COPY api/requirements.txt api/
# torch sem CUDA: a leitura roda em CPU, e o pacote padrão traz gigabytes de
# bibliotecas da NVIDIA que aqui nunca seriam usadas.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
      torch==2.13.0 \
 && pip install --no-cache-dir -r requirements.txt -r api/requirements.txt \
 && python -c "import cv2, keras, torch, rapidfuzz, psycopg, fastapi"

COPY api/ api/
COPY experimento-fuzzy/*.py experimento-fuzzy/
COPY experimento-imagem/*.py experimento-imagem/cabecalho.json experimento-imagem/
COPY experimento-imagem/modelo/cnn_emnist_maiusculas_28x28.keras \
     experimento-imagem/modelo/cnn_mnist_28x28.keras experimento-imagem/modelo/
COPY --from=cliente /web/dist web/dist

# Sem root. O pipeline escreve o cache de células em experimento-imagem/.
RUN useradd --uid 1000 --no-create-home olho \
 && mkdir -p experimento-imagem/dados/enviadas /estado \
 && chown -R olho experimento-imagem/dados /estado \
 && chown olho experimento-imagem
USER olho

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/saude', timeout=4)"

# Um worker só: o pipeline não é reentrante, e a trava dele vale por processo
# (ver api/pipeline.py). Mais workers leriam folhas por cima umas das outras.
CMD ["uvicorn", "api.servir:aplicacao", "--host", "0.0.0.0", "--port", "8000"]
