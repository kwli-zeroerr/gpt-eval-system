import React from "react";
import ReactDOM from "react-dom/client";
import Index from "./Index";
import { ModuleStateProvider } from "./contexts/ModuleStateContext";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ModuleStateProvider>
      <Index />
    </ModuleStateProvider>
  </React.StrictMode>
);

