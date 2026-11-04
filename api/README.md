# API do OlhoDigital

FastAPI sobre o pipeline que já existe em `experimento-imagem/`. A API **não
reimplementa nada**: ela envolve `identificar_folha.processar_folha`, que é o
código medido e registrado no vault.

Com Docker, `docker compose up --build` na raiz sobe tudo, e o restante deste
arquivo não é necessário. As instruções abaixo são para rodar direto na máquina.

## Banco de dados

A API usa Postgres. No Arch:

```sh
sudo pacman -S postgresql
sudo -u postgres initdb -D /var/lib/postgres/data
sudo systemctl enable --now postgresql

sudo -u postgres createuser --createdb olhodigital
sudo -u postgres createdb olhodigital -O olhodigital
```

Sem senha, o Postgres aceita conexão local por `peer`/`trust` conforme o
`pg_hba.conf`. Se pedir senha, crie com `createuser -P` e ponha a URL completa
na variável de ambiente abaixo.

> **Custo declarado.** A pergunta de pesquisa promete ferramenta *aberta e de
> execução local*, e cada serviço no caminho de reprodução é uma barreira a mais
> para quem clonar o repositório. A alternativa avaliada foi SQLite: um arquivo,
> sem serviço. Com SQLAlchemy, trocar é mudar a URL.

## Rodar

```sh
# banco e API
./.venv/bin/uvicorn api.main:app --reload            # http://127.0.0.1:8000
                                                     # documentação em /docs

# cliente
cd web && npm run dev                                # http://localhost:5173
```

Sem Node em execução, com o cliente compilado na mesma porta da API. Esta é a forma
com login, e a única que pode sair desta máquina; `api.main:app` não tem login e é só
para desenvolvimento:

```sh
cd web && npm ci && npm run build && cd ..
./.venv/bin/uvicorn api.servir:aplicacao --port 8000
```

O cliente chama sempre `/api/...` e o Vite faz o proxy para a porta 8000, então
o mesmo caminho serve em produção, onde os dois ficam na mesma origem.

Antes do primeiro uso, espelhe a base de participantes no banco:

```sh
curl -X POST http://127.0.0.1:8000/participantes/importar
```

## Configuração

Tudo por variável de ambiente com o prefixo `OLHODIGITAL_`:

| variável | padrão | para quê |
|---|---|---|
| `OLHODIGITAL_DATABASE_URL` | `postgresql+psycopg://olhodigital@localhost/olhodigital` | conexão |
| `OLHODIGITAL_PASTA_FOLHAS` | `experimento-imagem/dados/enviadas` | onde as imagens ficam |
| `OLHODIGITAL_MODELO_FOLHA_PADRAO` | `gerada_v2` | geometria de recorte (v2, v3 e v4 dividem a mesma) |
| `OLHODIGITAL_MODELO_LETRAS` | *(vazio = CNN base)* | ver abaixo |
| `OLHODIGITAL_CABECALHO` | `experimento-imagem/cabecalho.json` | onde os textos editados da folha ficam salvos |
| `OLHODIGITAL_ARQUIVO_USUARIOS` | `experimento-imagem/dados/usuarios.json` | as contas, com a senha em hash |
| `OLHODIGITAL_SEGREDO_SESSAO` | *(vazio = gerado ao lado das contas)* | assina o cookie de sessão |

**θ, δ, α e a métrica do nome não estão aqui de propósito**: eles vivem em
`experimento-fuzzy/fuzzy.py`, que é o único lugar onde o ponto de operação mora.
Foi duplicar esses valores em três módulos que manteve θ=0,85 em uso depois de
o pipeline real medir 0,60.

**`MODELO_LETRAS` liga a CNN afinada, e o padrão é não ligar** (D12). O modelo
afinado final viu todas as 694 células das folhas de desenvolvimento; usá-lo
para ler essas mesmas folhas seria medir no treino, sem erro e sem aviso. Na
coleta ele vale — a folha é inédita por construção. Por isso é variável de
ambiente: ligar tem que ser ato explícito.

## Duas verrugas conhecidas, e por que elas estão aqui

**1. O processo roda com o `cwd` apontado para `experimento-imagem/`.** O
pipeline resolve caminho relativo ao próprio diretório: modelos em `modelo/`,
base em `../experimento-fuzzy/dados/`, imagem em `dados/`. Enquanto ele não
virar pacote instalável — refatoração adiada de propósito, porque ela quer
suíte de testes antes —, mudar o `cwd` uma vez no startup é mais honesto que
espalhar caminho absoluto por dentro dele. Todo o resto da API usa caminho
absoluto.

**2. Uma folha por vez, sempre.** `SheetCutter.__init__` apaga uma pasta de
cache **fixa e compartilhada**, então duas folhas em paralelo destroem as
células uma da outra — em silêncio, produzindo leitura errada em vez de erro.
Há uma trava em `pipeline._TRAVA`. A 9 s por folha, 500 folhas dão ~75 min de
fila; se um dia isso incomodar, a saída é dar a cada processamento a sua
própria pasta de cache, **não** paralelizar por cima da trava.

## LGPD

As imagens contêm matrícula e nome manuscritos e ficam em
`experimento-imagem/dados/`, que o git ignora. Não vão para o banco — arquivo é
mais fácil de apagar que linha de tabela. A tabela `revisao` é *append-only*, e
o histórico existe para responder "quem mudou esta presença e quando", que é a
pergunta do primeiro contencioso.

Retenção e expurgo ainda **não** estão implementados. É pendência, não decisão.
