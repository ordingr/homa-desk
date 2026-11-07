import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { boot } from "./api.js";
import "./styles.css";

boot().then((session) => {
  createRoot(document.getElementById("root")).render(
    <StrictMode>
      <App session={session} />
    </StrictMode>,
  );
});
