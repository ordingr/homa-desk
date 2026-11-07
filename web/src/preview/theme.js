import { useEffect, useState } from "react";

const current = () => document.documentElement.dataset.theme || "dark";

export function useTheme() {
  const [theme, setTheme] = useState(current);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(current()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}
