#!/usr/bin/env bash
# Copia para o pacote os programas que a impressão chama e as DLLs de que eles dependem.
# Roda no shell UCRT64 do MSYS2, na máquina Windows que gera o `.exe`.
set -euo pipefail

destino=$1
mkdir -p "$destino"

# Só o que o programa chama em uso. `pdfinfo` e `pdftotext` servem só aos testes.
for programa in rsvg-convert pdftoppm pdfunite; do
    caminho=$(command -v "$programa")
    cp "$caminho" "$destino/"
    # `ldd` já segue as dependências das dependências. As do Windows ficam de fora.
    ldd "$caminho" | awk '$3 ~ /^\/ucrt64\// {print $3}' | while read -r dll; do
        cp -u "$dll" "$destino/"
    done
done

echo "$(ls "$destino" | wc -l) arquivos, $(du -sh "$destino" | cut -f1)"
