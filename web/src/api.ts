import type {
  CabecalhoAjuste,
  CabecalhoFolha,
  CabecalhoResposta,
  Cadastro,
  ChaveSugerida,
  Categoria,
  Evento,
  Fila,
  Folha,
  Grupo,
  ItemLote,
  Linha,
  LinhaDoGrupo,
  Lote,
  ModeloGrupo,
  ModeloPedido,
  PacoteAberto,
  Participante,
  PreviaApagar,
  ResumoRevisao,
  Sessao,
  SugestaoEventos,
  TipoEvento,
} from "./tipos";

/** O que o formulário manda. Sem `id`, `situacao` nem `criado_em`: esses são
 *  do servidor, e mandá-los daria a impressão de que o cliente os escolhe. */
export interface EventoEntrada {
  chave: string;
  tipo: TipoEvento;
  nome: string;
  responsavel?: string | null;
  local?: string | null;
  inicio?: string | null;
  fim?: string | null;
  disciplina?: string | null;
  turma?: string | null;
  semestre?: string | null;
  categoria?: Categoria | null;
  horas_complementares?: number | null;
  orgao_promotor?: string | null;
  /** Sem o campo, a edição não mexe no grupo (D24). */
  grupo?: string | null;
}

/** Erro que carrega o que a API respondeu.
 *
 *  A API devolve mensagem útil no corpo (`detail`) — "matrícula não está na
 *  base", "arquivo vazio". Jogar isso fora e mostrar "erro 422" transfere para
 *  o usuário o trabalho de adivinhar. */
export class ErroApi extends Error {
  // campo declarado à parte, e não como parâmetro do construtor: o tsconfig do
  // Vite liga `erasableSyntaxOnly`, que proíbe sintaxe que não some ao apagar
  // os tipos
  status: number;

  constructor(status: number, mensagem: string) {
    super(mensagem);
    this.status = status;
  }
}

async function erroDe(resposta: Response): Promise<ErroApi> {
  // Sessão vencida no meio do uso: volta para o login, e não para uma mensagem que
  // a pessoa não tem como resolver.
  if (resposta.status === 401 && window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
  let detalhe = resposta.statusText;
  try {
    const corpo = await resposta.json();
    detalhe = corpo.detail ?? detalhe;
  } catch {
    // resposta sem JSON (a API fora do ar, um proxy no meio) — fica o status
  }
  return new ErroApi(resposta.status, String(detalhe));
}

async function pedir<T>(caminho: string, init?: RequestInit): Promise<T> {
  const resposta = await fetch(`/api${caminho}`, init);
  if (!resposta.ok) throw await erroDe(resposta);
  return resposta.json() as Promise<T>;
}

async function pedirArquivo(caminho: string, init?: RequestInit): Promise<Blob> {
  const resposta = await fetch(`/api${caminho}`, init);
  if (!resposta.ok) throw await erroDe(resposta);
  return resposta.blob();
}

const JSON_CORPO = { "Content-Type": "application/json" };

export const api = {
  saude: () => pedir<Record<string, string>>("/saude"),

  eu: () => pedir<Sessao>("/eu"),

  entrar: (usuario: string, senha: string) =>
    pedir<Sessao>("/login", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ usuario, senha }),
    }),

  sair: () => fetch("/api/sair", { method: "POST" }),

  listarEventos: (grupo?: string) =>
    pedir<Evento[]>(grupo ? `/eventos?grupo=${encodeURIComponent(grupo)}` : "/eventos"),

  criarEvento: (dados: EventoEntrada) =>
    pedir<Evento>("/eventos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dados),
    }),

  /** Encerrar tira a chave do contradomínio contra o qual a leitura casa; não
   *  apaga nada. É o estado normal de um evento que já aconteceu. */
  mudarSituacaoEvento: (id: number, situacao: string) =>
    pedir<Evento>(
      `/eventos/${id}/situacao?situacao=${encodeURIComponent(situacao)}`,
      { method: "POST" },
    ),

  apagarEvento: async (id: number) => {
    const r = await fetch(`/api/eventos/${id}`, { method: "DELETE" });
    if (!r.ok) {
      let detalhe = r.statusText;
      try {
        detalhe = (await r.json()).detail ?? detalhe;
      } catch {
        /* sem corpo JSON */
      }
      throw new ErroApi(r.status, String(detalhe));
    }
  },

  listarFolhas: (grupo?: string) =>
    pedir<Folha[]>(grupo ? `/folhas?grupo=${encodeURIComponent(grupo)}` : "/folhas"),
  obterFolha: (id: number) => pedir<Folha>(`/folhas/${id}`),
  linhasDaFolha: (id: number) => pedir<Linha[]>(`/folhas/${id}/linhas`),

  /** O progresso da leitura: uma pergunta só, em vez de uma por folha na fila. */
  fila: () => pedir<Fila>("/folhas/fila"),

  /** O mesmo envio, informando o progresso do upload. `fetch` não dá esse número, por isso
   *  XMLHttpRequest. `aoProgresso` recebe de 0 a 1; em 1 o arquivo chegou e o servidor está
   *  convertendo o PDF em páginas, o que a requisição não tem como medir por dentro. */
  enviarFolhaComProgresso: (
    arquivo: File,
    aoProgresso: (fracao: number) => void,
    grupo?: string,
  ) =>
    new Promise<Folha[]>((resolve, reject) => {
      const corpo = new FormData();
      corpo.append("arquivo", arquivo);
      // D35, fase 5: pela aba do grupo, a chave casa só com os eventos dele
      if (grupo) corpo.append("grupo", grupo);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/folhas");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) aoProgresso(e.loaded / e.total);
      };
      xhr.upload.onload = () => aoProgresso(1);
      xhr.onerror = () => reject(new ErroApi(0, "sem conexão com o servidor"));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText) as Folha[]);
          return;
        }
        if (xhr.status === 401 && window.location.pathname !== "/login") {
          window.location.assign("/login");
        }
        let detalhe = xhr.statusText;
        try {
          detalhe = JSON.parse(xhr.responseText).detail ?? detalhe;
        } catch {
          // corpo sem JSON: fica o status
        }
        reject(new ErroApi(xhr.status, String(detalhe)));
      };
      xhr.send(corpo);
    }),

  /** Devolve uma LISTA: um PDF de escâner traz uma folha por página, que é o
   *  formato real das 500. O contrato não muda conforme o que foi enviado. */
  enviarFolha: (arquivo: File, modeloFolha?: string) => {
    const corpo = new FormData();
    corpo.append("arquivo", arquivo);
    if (modeloFolha) corpo.append("modelo_folha", modeloFolha);
    return pedir<Folha[]>("/folhas", { method: "POST", body: corpo });
  },

  /** Troca a imagem da folha por outro escaneamento do MESMO papel e relê (A61). Uma
   *  imagem ou um PDF de uma página. `forcar` quando já houver decisão humana, que a
   *  releitura descarta. */
  substituirEscaneamento: (folhaId: number, arquivo: File, forcar = false) => {
    const corpo = new FormData();
    corpo.append("arquivo", arquivo);
    if (forcar) corpo.append("forcar", "true");
    return pedir<Folha>(`/folhas/${folhaId}/substituir`, { method: "POST", body: corpo });
  },

  /** D30: esta cópia passa a valer, e a que valia vira cópia dela. */
  usarCopia: (folhaId: number) =>
    pedir<Folha>(`/folhas/${folhaId}/usar-copia`, { method: "POST" }),

  /** D30: a detecção errou; as duas folhas contam, e esta não é remarcada. */
  naoECopia: (folhaId: number) =>
    pedir<Folha>(`/folhas/${folhaId}/nao-e-copia`, { method: "POST" }),

  revisar: (
    linhaId: number,
    escolha: string | null,
    ms?: number,
    fora?: { matricula: string; nome: string },
  ) =>
    pedir(`/linhas/${linhaId}/revisao`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `null` é decisão legítima — "não identificado" —, não ausência de
      // decisão. Por isso vai explícito no corpo. `fora` é o D32: a pessoa
      // estava lá e não está na base.
      body: JSON.stringify({
        matricula_escolhida: escolha,
        ms,
        matricula_informada: fora?.matricula ?? null,
        nome_informado: fora?.nome ?? null,
      }),
    }),

  buscarParticipantes: (q: string) =>
    pedir<Participante[]>(`/participantes?q=${encodeURIComponent(q)}`),

  resumo: (folhaId: number) =>
    pedir<ResumoRevisao>(`/folhas/${folhaId}/resumo`),

  /** Fecha a revisão da folha. Recusa com 409 enquanto houver quarentena sem
   *  decisão — carimbar como revisada uma folha com linha em aberto perderia
   *  a linha em silêncio. */
  concluir: (folhaId: number, revisor?: string) =>
    pedir<ResumoRevisao>(
      `/folhas/${folhaId}/concluir${revisor ? `?revisor=${encodeURIComponent(revisor)}` : ""}`,
      { method: "POST" },
    ),

  /** Tira o carimbo para a folha voltar a ser editável. Não apaga decisão
   *  nenhuma: a tabela de revisão é append-only e a nova empilha por cima. */
  reabrir: (folhaId: number) =>
    pedir<Folha>(`/folhas/${folhaId}/reabrir`, { method: "POST" }),

  apagar: async (folhaId: number, forcar = false) => {
    const r = await fetch(`/api/folhas/${folhaId}${forcar ? "?forcar=true" : ""}`, {
      method: "DELETE",
    });
    if (!r.ok) {
      let detalhe = r.statusText;
      try {
        detalhe = (await r.json()).detail ?? detalhe;
      } catch {
        /* sem corpo JSON */
      }
      throw new ErroApi(r.status, String(detalhe));
    }
  },

  modelosFolha: () => pedir<string[]>("/modelos-folha"),

  eventosSugeridos: (folhaId: number) =>
    pedir<SugestaoEventos>(`/folhas/${folhaId}/eventos-sugeridos`),

  /** A decisão humana. Uma releitura nunca desfaz o que vier daqui. */
  vincularEvento: (folhaId: number, eventoId: number | null) =>
    pedir<Folha>(`/folhas/${folhaId}/evento`, {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ evento_id: eventoId }),
    }),

  urlTiraChave: (folhaId: number) => `/api/folhas/${folhaId}/chave.jpg`,

  cabecalho: () => pedir<CabecalhoResposta>("/impressao/cabecalho"),

  salvarCabecalho: (dados: CabecalhoFolha) =>
    pedir<CabecalhoResposta>("/impressao/cabecalho", {
      method: "PUT",
      headers: JSON_CORPO,
      body: JSON.stringify(dados),
    }),

  /** Não salva nada: desenha o topo da folha com os textos propostos. */
  previaCabecalho: (dados: CabecalhoFolha, chave: string, sinal?: AbortSignal) =>
    pedirArquivo("/impressao/cabecalho/previa", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ cabecalho: dados, chave }),
      signal: sinal,
    }),

  /** Uma ou várias folhas sem planilha. Chave vazia sai para rotular à mão. */
  gerarFolhas: (chave: string, folhas: number, cabecalho?: CabecalhoFolha) =>
    pedirArquivo("/impressao/folhas", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ chave, folhas, cabecalho: cabecalho ?? null }),
    }),

  /** `ateLinha`: a última linha da planilha que entra no lote, como no Excel. */
  enviarPlanilha: (arquivo: File, ateLinha?: number) => {
    const corpo = new FormData();
    corpo.append("arquivo", arquivo);
    if (ateLinha !== undefined) corpo.append("ate_linha", String(ateLinha));
    return pedir<Lote>("/impressao/planilha", { method: "POST", body: corpo });
  },

  /** Recusa com 409 enquanto houver alerta sem `aceitarAlertas`. */
  /** `modelo` (D35, fase 3): imprime com o modelo de um grupo e os ajustes por chave. */
  gerarPdf: (itens: ItemLote[], aceitarAlertas: boolean, modelo?: ModeloPedido) =>
    pedirArquivo("/impressao/pdf", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({
        itens,
        aceitar_alertas: aceitarAlertas,
        cabecalho: modelo?.cabecalho ?? null,
        ajustes: modelo?.ajustes ?? null,
      }),
    }),

  // --- D35, fase 3: o modelo da folha por grupo -------------------------------------
  modeloDoGrupo: (nome: string) => pedir<ModeloGrupo>(`/grupos/${encodeURIComponent(nome)}/modelo`),
  salvarModeloDoGrupo: (nome: string, cabecalho: CabecalhoFolha | null) =>
    pedir<ModeloGrupo>(`/grupos/${encodeURIComponent(nome)}/modelo`, {
      method: "PUT",
      headers: JSON_CORPO,
      body: JSON.stringify({ cabecalho }),
    }),
  ajustarEvento: (eventoId: number, ajuste: CabecalhoAjuste) =>
    pedir<Evento>(`/eventos/${eventoId}/ajuste`, {
      method: "PUT",
      headers: JSON_CORPO,
      body: JSON.stringify(ajuste),
    }),
  chavesSugeridas: (nome: string, n = 8) =>
    pedir<ChaveSugerida[]>(`/grupos/${encodeURIComponent(nome)}/chaves-sugeridas?n=${n}`),
  /** Todas as folhas do grupo num PDF só, para imprimir. Pode levar alguns minutos. */
  pdfDoGrupo: (nome: string) => pedirArquivo(`/grupos/${encodeURIComponent(nome)}/pdf`),
  urlPacote: (nome: string) => `/api/grupos/${encodeURIComponent(nome)}/pacote`,
  /** Abre o pacote de um grupo no PC de impressão: não grava nada, devolve o lote. */
  abrirPacote: (arquivo: File) => {
    const corpo = new FormData();
    corpo.append("arquivo", arquivo);
    return pedir<PacoteAberto>("/impressao/pacote", { method: "POST", body: corpo });
  },

  /** `grupo`: o grupo dos eventos CRIADOS agora; os que já existiam não mudam. */
  cadastrarChaves: (itens: ItemLote[], grupo?: string) =>
    pedir<Cadastro>("/impressao/cadastrar", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ itens, grupo: grupo || null }),
    }),

  urlModeloPlanilha: () => "/api/impressao/planilha/modelo",

  /** Lê a folha de novo, opcionalmente com outra geometria. Descarta as linhas
   *  — e com elas as decisões humanas —, por isso `forcar` quando já houver
   *  revisão. */
  reprocessar: (folhaId: number, modeloFolha?: string, forcar = false) => {
    const q = new URLSearchParams();
    if (modeloFolha) q.set("modelo_folha", modeloFolha);
    if (forcar) q.set("forcar", "true");
    const cauda = q.toString() ? `?${q}` : "";
    return pedir<Folha>(`/folhas/${folhaId}/reprocessar${cauda}`, {
      method: "POST",
    });
  },

  /** O navegador baixa direto do servidor: o JSON de uma folha grande não
   *  precisa passar pela memória do cliente só para virar um arquivo. */
  urlJson: (folhaId: number) => `/api/folhas/${folhaId}/json`,

  // --- grupos de eventos e apagar em massa (D24) ---------------------------

  listarGrupos: () => pedir<Grupo[]>("/grupos"),

  // --- D35: o grupo como entidade ---------------------------------------------------
  obterGrupo: (nome: string) => pedir<Grupo>(`/grupos/${encodeURIComponent(nome)}`),
  criarGrupo: (nome: string, descricao?: string) =>
    pedir<Grupo>("/grupos", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ nome, descricao: descricao || null }),
    }),
  renomearGrupo: (nome: string, novo: string, descricao?: string | null) =>
    pedir<Grupo>(`/grupos/${encodeURIComponent(nome)}`, {
      method: "PATCH",
      headers: JSON_CORPO,
      body: JSON.stringify({ nome: novo, descricao: descricao || null }),
    }),
  apagarGrupo: (nome: string) =>
    fetch(`/api/grupos/${encodeURIComponent(nome)}`, { method: "DELETE" }).then(async (r) => {
      if (!r.ok) throw await erroDe(r);
    }),

  /** D33: encerra (libera as chaves) ou reabre todos os eventos do grupo. */
  mudarSituacaoGrupo: (
    nome: string,
    situacao: "encerrado" | "em andamento",
    confirmacao?: string,
  ) => {
    // D34: encerrar pede o nome do grupo digitado; o servidor confere
    const q = new URLSearchParams({ situacao });
    if (confirmacao !== undefined) q.set("confirmacao", confirmacao);
    return pedir<{ grupo: string; situacao: string; eventos: number; folhas_religadas: number }>(
      `/grupos/${encodeURIComponent(nome)}/situacao?${q}`,
      { method: "POST" },
    );
  },

  /** Todos ou nenhum. Grupo vazio tira os eventos do grupo. */
  porNoGrupo: (eventoIds: number[], grupo: string | null) =>
    pedir<{ alterados: number; grupo: string | null }>("/grupos/eventos", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ evento_ids: eventoIds, grupo }),
    }),

  quarentenaDoGrupo: (nome: string) =>
    pedir<LinhaDoGrupo[]>(`/grupos/${encodeURIComponent(nome)}/quarentena`),

  urlFrequencia: (nome: string, formato: "xlsx" | "json") =>
    `/api/grupos/${encodeURIComponent(nome)}/frequencia.${formato}`,

  /** Não apaga nada: diz quantas sairiam e quantas ficam. */
  previaApagarTodas: (incluirRevisadas: boolean) =>
    pedir<PreviaApagar>(`/folhas/apagar-todas?incluir_revisadas=${incluirRevisadas}`),

  /** `confirmacao` é o número que a pessoa digitou. Se a conta do servidor der
   *  outro (chegou folha entre a prévia e o clique), nada sai e volta 409. */
  apagarTodas: (confirmacao: number, incluirRevisadas: boolean) =>
    pedir<PreviaApagar>("/folhas/apagar-todas", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ confirmacao, incluir_revisadas: incluirRevisadas }),
    }),

  previaApagarDoGrupo: (nome: string, incluirRevisadas: boolean) =>
    pedir<PreviaApagar>(
      `/grupos/${encodeURIComponent(nome)}/apagar-folhas?incluir_revisadas=${incluirRevisadas}`,
    ),

  apagarDoGrupo: (nome: string, confirmacao: number, incluirRevisadas: boolean) =>
    pedir<PreviaApagar>(`/grupos/${encodeURIComponent(nome)}/apagar-folhas`, {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ confirmacao, incluir_revisadas: incluirRevisadas }),
    }),
};

/** Uma folha leva ~9 s para ser lida, então o cliente pergunta em vez de
 *  esperar. Um segundo é folgado para algo dessa ordem e não castiga o
 *  servidor; o `sinal` existe para a espera morrer junto com o componente. */
export async function esperarProcessamento(
  id: number,
  aoAtualizar?: (f: Folha) => void,
  sinal?: AbortSignal,
): Promise<Folha> {
  for (;;) {
    if (sinal?.aborted) throw new DOMException("cancelado", "AbortError");
    const folha = await api.obterFolha(id);
    aoAtualizar?.(folha);
    // Terminal é o que NÃO é fila. Escrito por exclusão de propósito: listar
    // os estados finais fazia a espera girar para sempre assim que um estado
    // novo aparecia — foi o que aconteceu com `suspeita` e `nao_e_folha`.
    if (folha.estado !== "recebida" && folha.estado !== "processando") {
      return folha;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}
