import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import AdminApp from "./AdminApp";
import "reactflow/dist/style.css";
import "./index.css";

const isAdmin = window.location.hash.replace(/^#\/?/, "") === "admin";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{isAdmin ? <AdminApp /> : <App />}</React.StrictMode>
);

