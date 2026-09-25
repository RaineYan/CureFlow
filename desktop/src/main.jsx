import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./native.css";
import "./trace.css";
import "./brand-theme.css";
import "./workspace-refresh.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode><App /></React.StrictMode>,
);
