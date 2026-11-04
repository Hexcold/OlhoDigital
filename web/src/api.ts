import type {
  CabecalhoFolha,
  CabecalhoResposta,
  Cadastro,
  Categoria,
  Evento,
  Folha,
  ItemLote,
  Linha,
  Lote,
  Participante,
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

  listarEventos: () => pedir<Evento[]>("/eventos"),

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

  listarFolhas: () => pedir<Folha[]>("/folhas"),
  obterFolha: (id: number) => pedir<Folha>(`/folhas/${id}`),
  linhasDaFolha: (id: number) => pedir<Linha[]>(`/folhas/${id}/linhas`),

  /** Devolve uma LISTA: um PDF de escâner traz uma folha por página, que é o
   *  formato real das 500. O contrato não muda conforme o que foi enviado. */
  enviarFolha: (arquivo: File, modeloFolha?: string) => {
    const corpo = new FormData();
    corpo.append("arquivo", arquivo);
    if (modeloFolha) corpo.append("modelo_folha", modeloFolha);
    return pedir<Folha[]>("/folhas", { method: "POST", body: corpo });
  },

  revisar: (linhaId: number, escolha: string | null, ms?: number) =>
    pedir(`/linhas/${linhaId}/revisao`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `null` é decisão legítima — "não identificado" —, não ausência de
      // decisão. Por isso vai explícito no corpo.
      body: JSON.stringify({ matricula_escolhida: escolha, ms }),
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
  gerarFolhas: (chave: string, folhas: number) =>
    pedirArquivo("/impressao/folhas", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ chave, folhas }),
    }),

  enviarPlanilha: (arquivo: File) => {
    const corpo = new FormData();
    corpo.append("arquivo", arquivo);
    return pedir<Lote>("/impressao/planilha", { method: "POST", body: corpo });
  },

  /** Recusa com 409 enquanto houver alerta sem `aceitarAlertas`. */
  gerarPdf: (itens: ItemLote[], aceitarAlertas: boolean) =>
    pedirArquivo("/impressao/pdf", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ itens, aceitar_alertas: aceitarAlertas }),
    }),

  cadastrarChaves: (itens: ItemLote[]) =>
    pedir<Cadastro>("/impressao/cadastrar", {
      method: "POST",
      headers: JSON_CORPO,
      body: JSON.stringify({ itens }),
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
