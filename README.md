# OlhoDigital

Extração automática de frequência a partir de listas de presença manuscritas.

Quando o sistema acadêmico fica indisponível, ou em eventos sem plataforma própria, a folha
de assinaturas em papel vira o registro de frequência, e o custo está em transcrevê-la depois.
O OlhoDigital mantém o papel e automatiza só a transcrição: uma ferramenta aberta, de execução
local, que lê matrícula e nome escritos à mão e os reconcilia com a base de participantes.
O que ele não decide sozinho vai para revisão humana, com os candidatos ranqueados.

Trabalho de Conclusão de Curso em Engenharia de Software, UFC Campus Quixadá, sob orientação
do Prof. Dr. David Sena Oliveira.

## Como funciona

1. A folha é gerada por código, com marcadores ArUco e grade de células.
2. A imagem digitalizada é retificada pelos marcadores e cortada em células.
3. Duas CNNs derivadas da LeNet-5 leem um caractere por célula: dígitos na matrícula, letras no nome.
4. A leitura é comparada com a base por correspondência aproximada de strings.
5. O registro só é aceito se o escore for alto e abrir margem sobre o segundo colocado. Caso
   contrário, vai para a quarentena. Nada é descartado.

## Como rodar

Com Docker, o sistema inteiro sobe com um comando:

```sh
docker compose up --build
```

Abre em <http://127.0.0.1:8080>. Porta e outros ajustes ficam no `.env`, a partir do
`.env.exemplo`. Sem Docker, veja [`api/README.md`](api/README.md).

O sistema exige login. As contas são criadas no terminal, e no Docker o comando vai depois de
`docker compose exec app`:

```sh
./.venv/bin/python -m api.usuarios adicionar SEU_NOME --papel admin
./.venv/bin/python -m api.usuarios adicionar OUTRA_PESSOA --papel impressao
```

`admin` vê tudo. `impressao` alcança só a tela de impressão.

A tela **Impressão** edita os textos da folha, gera folhas avulsas e recebe a planilha de chaves
para gerar o lote em um PDF. A planilha precisa da coluna `chave` (ou `key`). Com a coluna
`folhas`, cada chave sai na quantidade pedida; sem ela, sai uma folha por chave. As colunas `nome`
(ou `tag`), `local`, `responsavel` e `dia` (ou `day`) são opcionais. O campo **Até a linha** gera
só até aquela linha da planilha, contada como no Excel. O carimbo da folha leva o dia do evento,
`v4 · DD/MM/AAAA`, ou só `v4` quando a linha não tem dia. Imprima em A4, a 100%, sem ajustar à
página.

Na tela **Folhas**, envie o PDF do escâner, mesmo com folhas de eventos diferentes. Cada folha é
ligada ao evento pela chave. O que não combina com segurança fica marcado como *sem evento*, e
uma pessoa decide vendo o recorte da chave.

Para identificar participantes, coloque a planilha da base em `experimento-fuzzy/dados/` e
importe uma vez:

```sh
curl -X POST http://127.0.0.1:8080/api/participantes/importar
```

## Compartilhar com outra pessoa

```sh
sudo pacman -S cloudflared      # uma vez
./compartilhar.sh
```

O comando mostra um endereço `https://...trycloudflare.com`. Cada pessoa entra com a própria conta. O
link funciona enquanto o terminal estiver aberto e o computador ligado, e muda a cada execução.

### Impressão em outro computador

Para quem só gera folhas e chaves existe um `.exe` para Windows: um arquivo só, que dispensa
instalação, login e internet e abre a tela de impressão no navegador. Ele atende apenas o próprio
computador e não leva banco, lista de alunos nem leitura de folhas.

O arquivo é gerado e testado pela ação `impressao-windows` do GitHub Actions, com a receita em
[`impressao-windows/`](impressao-windows/). As chaves impressas nele precisam ser enviadas também
ao sistema principal, em "Cadastrar como eventos", para a leitura ter contra o que casar.

## Testes

```sh
./.venv/bin/python -m unittest discover -s testes
```

A bateria cobre a geração de folhas e chaves: geometria, chave, textos, planilha, PDF, API, o
vínculo da folha com o evento e o `.exe` da impressão. Roda sem banco e sem dados reais, em cerca de 9 minutos. Com
`OLHODIGITAL_TESTES_LENTOS=1` inclui as cargas de 520 e 2.000 páginas.

## Estrutura

| pasta | conteúdo |
|---|---|
| `api/` | API em FastAPI |
| `web/` | cliente em React |
| `experimento-imagem/` | geração da folha, visão computacional, reconhecimento e identificação |
| `experimento-fuzzy/` | correspondência aproximada, normalização e simulações |
| `testes/` | bateria de testes da geração de folhas e chaves |
| `impressao-windows/` | receita, fontes e ensaio do `.exe` da impressão |
| `modelo-folha/` | modelo da folha para o Illustrator, gerado por `experimento-imagem/modelo_editavel.py`; um PDF que volta do editor só vai à impressora depois de `modelo_editavel.py conferir arquivo.pdf` |

## O que não está aqui

As folhas digitalizadas, a base de alunos e os gabaritos contêm dados pessoais e não são
versionados. Por isso os resultados não podem ser reproduzidos só com este repositório.

Comentários do código citam notas internas de projeto (`A19`, `D08`), que também ficam fora do
repositório porque mencionam casos reais das folhas.
