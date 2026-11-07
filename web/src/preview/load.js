import { requestRaw } from "../api.js";

export const MORE_BYTES = 1_048_576;

export async function loadPreview(path, { range, hydrate } = {}) {
  const response = await requestRaw("/v1/files/preview", {
    path,
    range,
    hydrate: hydrate ? 1 : "",
  });
  const type = (response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  if (type === "application/json") return { json: await response.json() };
  return { blob: await response.blob(), type };
}
