import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import AuroraBackground from "./AuroraBackground";
import "./styles.css";
import "./native.css";
import "./trace.css";
import "./brand-theme.css";
import "./workspace-refresh.css";
import "./aurora.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode><AuroraBackground /><App /></React.StrictMode>,
);
