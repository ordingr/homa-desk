import { useEffect, useState } from "react";

export function useBlobUrl(blob) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!blob) return undefined;
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => {
      URL.revokeObjectURL(next);
      setUrl(null);
    };
  }, [blob]);
  return url;
}
