import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import App from "./App";
import Eventos from "./paginas/Eventos";
import Folhas from "./paginas/Folhas";
import Impressao from "./paginas/Impressao";
import Login from "./paginas/Login";
import Revisao from "./paginas/Revisao";

// Rota por folha, e não uma tela só com estado interno: o revisor precisa
// poder voltar a uma folha, mandar o link dela para outra pessoa e recarregar
// a página sem perder onde estava.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route path="/" element={<Folhas />} />
          <Route path="/eventos" element={<Eventos />} />
          <Route path="/impressao" element={<Impressao />} />
          <Route path="/folhas/:id" element={<Revisao />} />
        </Route>
        <Route path="/login" element={<Login />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
