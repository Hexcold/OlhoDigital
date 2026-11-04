"""Item 0c-3: um HTR de prateleira resolve isto sozinho?

É a pergunta do [[Feedback - Rubens]] na banca — *"isso já não está resolvido?"* — e
ela merece medição, não opinião. O projeto monta um reconhecedor por célula (KNN, CNN);
um modelo de HTR moderno lê a **linha inteira** e devolve a string pronta. Se ele
resolvesse, boa parte do trabalho seria desnecessária.

Modelo: `microsoft/trocr-base-handwritten` — encoder ViT, decoder de linguagem,
treinado no IAM (linhas manuscritas em inglês). É o HTR de prateleira mais citado.

## As duas variantes, e por que as duas

  faixa   recorta a faixa da coluna de nome direto da folha alinhada, do jeito que o
          scanner viu — grade impressa, uma letra por quadradinho. É o teste honesto
          de "prateleira": zero engenharia nossa.
  limpa   passa cada célula pelo MESMO filtro do pipeline (binariza, inverte, descarta
          regiões pequenas e fora do centro) e repinta os glifos numa tela branca, na
          posição original. Tira a grade e mantém a geometria.

A diferença entre as duas separa duas causas possíveis de fracasso: **a grade impressa**
confunde o modelo, ou **a caligrafia em quadradinhos** não se parece com o que ele viu no
IAM. São remédios diferentes.

## A ressalva do D06, que é o ponto central

O HTR devolve uma **string pronta**, sem probabilidade por caractere. O
[[D02 - Wildcard de abstenção]] depende de um corte de confiança por posição para emitir
`?`, e [[A27 - Delta é o gate inteiro, theta é planalto]] mostrou que o gate do sistema
vive da relação entre 1º e 2º colocados. Um reconhecedor que não sabe dizer *onde* está
inseguro entrega menos ao matcher, mesmo que acerte mais caracteres.

Por isso a comparação decisiva aqui **não é CER**: é ponta a ponta. A string do HTR entra
no `identificar()` no lugar da leitura do nome, com o **canal da matrícula mantido igual**
ao do pipeline, e o que se compara é aceito/quarentena/errado.

Uso:
    python htr_prateleira.py
    python htr_prateleira.py --folha m3_2 --salvar-recortes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

from image import Image
from align import Align
from region import Decomposer
from sheet_cutter import SheetCutter
from process import folha, carregar_gabarito

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "experimento-fuzzy"))
from base_alunos import carregar_base                      # noqa: E402
from normalizacao import normalizar                        # noqa: E402
from fuzzy import (dist_levenshtein, identificar,          # noqa: E402
                   ALPHA, THETA, DELTA, SIM_NOME, METRICA_NOME)

MODELO = "microsoft/trocr-base-handwritten"
BASE_XLSX = "../experimento-fuzzy/dados/Lista de Alunos Campus Quixadá.xlsx"
REGISTROS = "dados/identificacao_folhas.json"
SAIDA = "dados/htr_prateleira.json"


def faixa_da_linha(alinhada: Image, modelo, i: int) -> np.ndarray:
    """A faixa crua da coluna de nome, na linha i. Mesma geometria do SheetCutter."""
    h, w = alinhada.get_h_w()
    coluna = modelo.coluna("nome")
    x1, x2 = int(coluna["x_begin"] * w), int(coluna["x_end"] * w)
    y1, _ = modelo.faixa_y(h, i)
    return alinhada.data[y1:y1 + int(modelo.passo_y(h)), x1:x2]


def faixa_limpa(celulas: list[Image]) -> np.ndarray:
    """Repinta os glifos filtrados numa tela branca, sem a grade.

    Cada célula passa pelo mesmo filtro do pipeline; o que sobra é colado na
    posição original. Tinta preta sobre branco, que é o formato que o TrOCR
    espera — o pipeline trabalha invertido (tinta branca sobre preto).
    """
    recortes = []
    for c in celulas:
        bin_ = c.binarize(gaussian=False).inversion().cut_borders(2)
        alvo = Decomposer(bin_).erase_small_regions(0.2).erase_regions_outside_bounds()
        img = alvo.get_image()
        recortes.append(None if img.is_empty() else img.data)

    alturas = [r.shape[0] for r in recortes if r is not None]
    larguras = [r.shape[1] for r in recortes if r is not None]
    if not alturas:
        return np.full((32, 32), 255, np.uint8)
    h_cel, w_cel = max(alturas), max(larguras)

    # margem de 20% em volta: o TrOCR foi treinado em linhas com respiro, e
    # texto colado na borda degrada o encoder
    m = int(0.2 * h_cel)
    tela = np.full((h_cel + 2 * m, w_cel * len(recortes) + 2 * m), 255, np.uint8)
    for j, r in enumerate(recortes):
        if r is None:
            continue
        x = m + j * w_cel
        # a filtrada vem invertida (tinta branca); volta para tinta preta
        tela[m:m + r.shape[0], x:x + r.shape[1]] = 255 - r
    return tela


def para_rgb(cinza: np.ndarray) -> "PIL.Image.Image":                # noqa: F821
    from PIL import Image as PILImage
    if cinza.ndim == 3:
        cinza = cv2.cvtColor(cinza, cv2.COLOR_BGR2GRAY)
    return PILImage.fromarray(cv2.cvtColor(cinza, cv2.COLOR_GRAY2RGB))


def cer(lido: str, verdade: str) -> float:
    """Taxa de erro de caractere. Levenshtein SEM wildcard: o HTR não emite '?',
    então usar a variante com curinga mediria outra coisa."""
    if not verdade:
        return 0.0 if not lido else 1.0
    return dist_levenshtein(lido, verdade) / len(verdade)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Item 0c-3: HTR de prateleira (TrOCR).")
    p.add_argument("--folha", default="m3_2")
    p.add_argument("--modelo", default=MODELO)
    p.add_argument("--salvar-recortes", action="store_true",
                   help="grava as faixas em dados/htr_recortes/ para inspeção")
    args = p.parse_args()

    caminho = f"dados/{args.folha}.png"
    modelo_folha = folha({"m3_2": 3, "m3_1": 3, "m2-1": 2}[args.folha])

    gabarito = carregar_gabarito(caminho)
    img = Image().load_from_file(caminho)
    alinhada = Image().set_data(Align(*modelo_folha.warp).aruco(img.data))
    linhas_cel, _ = SheetCutter("cache_temp").cut_info(
        alinhada, modelo_folha, "nome")
    print(f"{args.folha}: {len(gabarito)} linhas com gabarito")

    print(f"carregando {args.modelo} ...")
    t0 = time.perf_counter()
    import torch
    from transformers import VisionEncoderDecoderModel
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer, models, pre_tokenizers, decoders

    rede = VisionEncoderDecoderModel.from_pretrained(args.modelo).eval()

    # O `AutoImageProcessor` exige torchvision, que não está no ambiente. São quatro
    # operações, e os parâmetros vêm do `preprocessor_config.json` OFICIAL do modelo
    # (ViTImageProcessor: size 384, resample 2 = bilinear, mean e std 0,5), lido e
    # conferido — não chutado.
    LADO_VIT, MEDIA, DESVIO = 384, 0.5, 0.5

    def pixels(imagem: np.ndarray) -> "torch.Tensor":
        rgb = np.asarray(para_rgb(imagem), dtype=np.float32)
        rgb = cv2.resize(rgb, (LADO_VIT, LADO_VIT), interpolation=cv2.INTER_LINEAR)
        rgb = ((rgb / 255.0) - MEDIA) / DESVIO
        return torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0)

    # O `TrOCRProcessor` não carrega nesta versão: o repo oficial traz o vocabulário
    # como `vocab.json` + `merges.txt` e não como `tokenizer.json`, e o transformers 5
    # abandonou a conversão automática desse formato. O tokenizer é remontado à mão a
    # partir dos MESMOS arquivos oficiais — BPE byte-level, que é o do RoBERTa.
    # Conferido por round-trip antes de usar.
    tok = Tokenizer(models.BPE.from_file(hf_hub_download(args.modelo, "vocab.json"),
                                         hf_hub_download(args.modelo, "merges.txt")))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    assert tok.decode(tok.encode("HELLO WORLD").ids) == "HELLO WORLD", \
        "tokenizer remontado não faz round-trip"
    ESPECIAIS = {0, 1, 2}          # <s>, <pad>, </s> do RoBERTa
    print(f"  ({time.perf_counter() - t0:.0f} s)")

    @torch.no_grad()
    def ler(imagem: np.ndarray) -> str:
        ids = rede.generate(pixels(imagem), max_new_tokens=40)[0].tolist()
        return tok.decode([i for i in ids if i not in ESPECIAIS])

    if args.salvar_recortes:
        os.makedirs("dados/htr_recortes", exist_ok=True)

    resultados = []
    t0 = time.perf_counter()
    for num in sorted(gabarito):
        i = num - 1
        verdade = " ".join(gabarito[num][1].split())
        variantes = {
            "faixa": faixa_da_linha(alinhada, modelo_folha, i),
            "limpa": faixa_limpa(linhas_cel[i]),
        }
        registro = {"linha": num, "verdade": verdade}
        for nome_v, imagem in variantes.items():
            if args.salvar_recortes:
                cv2.imwrite(f"dados/htr_recortes/{num:02d}_{nome_v}.png", imagem)
            bruto = ler(imagem)
            lido = " ".join(normalizar(bruto).split())
            registro[nome_v] = {"bruto": bruto, "lido": lido,
                                "cer": cer(lido, verdade)}
        resultados.append(registro)
        print(f"  {num:>2} verdade {verdade!r}")
        for v in ("faixa", "limpa"):
            print(f"       {v:<6} {registro[v]['lido']!r:<40} "
                  f"CER {registro[v]['cer']:.2f}")
    print(f"({time.perf_counter() - t0:.0f} s de inferência)")

    # --- CER agregado -------------------------------------------------------
    print(f"\nCER médio por variante (n={len(resultados)}):")
    for v in ("faixa", "limpa"):
        cers = [r[v]["cer"] for r in resultados]
        exatas = sum(1 for r in resultados if r[v]["lido"] == r["verdade"])
        print(f"  {v:<6} CER {np.mean(cers):.3f}   linhas exatas {exatas}/{len(resultados)}")

    # --- ponta a ponta: a comparação que decide -----------------------------
    # o canal da MATRÍCULA fica igual ao do pipeline; só o nome é trocado pelo
    # HTR. Assim a diferença medida é do reconhecedor de nome, não do conjunto.
    base = carregar_base(BASE_XLSX)
    for a in base:
        a["nome"] = normalizar(a["nome"])
    with open(REGISTROS) as f:
        regs = json.load(f)
    pipeline = {r["linha"]: r for r in regs
                if r["folha"] == args.folha and r["alpha"] == ALPHA
                and r["modo"] == "chuta"}

    print(f"\nponta a ponta na {args.folha} "
          f"(alpha={ALPHA} theta={THETA} delta={DELTA}, matrícula = a do pipeline):")
    print(f"{'variante':<12} {'aceitos':>8} {'quarent':>8} {'ERRADOS':>8}")
    sim = SIM_NOME[METRICA_NOME]
    for v in ("pipeline", "faixa", "limpa"):
        ac = qu = er = 0
        for r in resultados:
            reg = pipeline.get(r["linha"])
            if reg is None:
                continue
            nome = reg["leitura_nome"] if v == "pipeline" else r[v]["lido"]
            res = identificar(reg["leitura"], nome, base, alpha=ALPHA,
                              theta=THETA, delta=DELTA, sim_nome=sim)
            if res["decisao"] == "aceito":
                ac += 1
                certo = (res["aluno"]["matricula"] == reg["verdade"]
                         and reg["verdade_na_base"])
                er += not certo
            else:
                qu += 1
        print(f"{v:<12} {ac:>8} {qu:>8} {er:>8}")
        for reg in resultados:
            reg.setdefault("e2e", {})[v] = None
    with open(SAIDA, "w") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=1)
    print(f"\n{len(resultados)} linhas em {SAIDA}")
    print("ressalva: o TrOCR foi treinado no IAM, texto CORRIDO em inglês. Letra de "
          "forma\nem quadradinhos é fora da distribuição dele — o resultado mede "
          "prateleira,\nnão o teto do HTR como família de método.")
