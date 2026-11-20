import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import App from "./App";
import Eventos from "./paginas/Eventos";
import Folhas from "./paginas/Folhas";
import Grupo from "./paginas/Grupo";
import Grupos from "./paginas/Grupos";
import Impressao from "./paginas/Impressao";
import Login from "./paginas/Login";
import QuarentenaGrupo from "./paginas/QuarentenaGrupo";
import Revisao from "./paginas/Revisao";

// Rota por folha, e não uma tela só com estado interno: o revisor precisa
// poder voltar a uma folha, mandar o link dela para outra pessoa e recarregar
// a página sem perder onde estava.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          {/* D35: o início é a lista de grupos; cada grupo tem a sua página com as etapas */}
          <Route path="/" element={<Grupos />} />
          <Route path="/grupos/:nome" element={<Grupo />} />
          <Route path="/folhas" element={<Folhas />} />
          <Route path="/eventos" element={<Eventos />} />
          <Route path="/impressao" element={<Impressao />} />
          <Route path="/folhas/:id" element={<Revisao />} />
          <Route path="/grupos/:nome/quarentena" element={<QuarentenaGrupo />} />
        </Route>
        <Route path="/login" element={<Login />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
