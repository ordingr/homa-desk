let token = null;

export class ApiError extends Error {
  constructor(status, message, code = "", field = "") {
    super(message);
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

export const hasToken = () => token !== null;

async function readBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorFrom(response, body, path) {
  const err = body && typeof body === "object" ? body.error : null;
  if (err && typeof err === "object") {
    return new ApiError(response.status, err.message || response.statusText, err.code, err.field);
  }
  if (typeof err === "string") return new ApiError(response.status, err);
  if (response.status === 404) {
    return new ApiError(404, `Not available in this build yet (404 ${path}).`, "not_found");
  }
  return new ApiError(response.status, `Request failed: ${response.status} ${response.statusText}`);
}

export async function exchangeNonce(nonce) {
  const response = await fetch("/v1/session/exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nonce }),
    cache: "no-store",
    referrerPolicy: "no-referrer",
  });
  const body = await readBody(response);
  if (!response.ok) throw errorFrom(response, body, "/v1/session/exchange");
  const value = body && (body.token || body.access_token);
  if (!value || typeof value !== "string") {
    throw new ApiError(502, "The session exchange did not return a token.");
  }
  token = value;
}

export async function request(method, path, { params, body } = {}) {
  if (token === null) throw new ApiError(401, "No session. Open this page from Assistant.");
  let url = path;
  if (params) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
      else if (value !== "" && value !== undefined && value !== null) query.set(key, String(value));
    }
    const text = query.toString();
    if (text) url += "?" + text;
  }
  const headers = { Authorization: `Bearer ${token}` };
  const init = { method, headers, cache: "no-store", referrerPolicy: "no-referrer" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(url, init);
  const data = await readBody(response);
  if (response.status === 401) token = null;
  if (!response.ok) throw errorFrom(response, data, path);
  return data;
}

export async function requestRaw(path, params) {
  if (token === null) throw new ApiError(401, "No session. Open this page from Assistant.");
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== "" && value !== undefined && value !== null) query.set(key, String(value));
  }
  const text = query.toString();
  const response = await fetch(text ? `${path}?${text}` : path, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
    referrerPolicy: "no-referrer",
  });
  if (response.status === 401) token = null;
  if (!response.ok) throw errorFrom(response, await readBody(response), path);
  return response;
}

export const get = (path, params) => request("GET", path, { params });
export const post = (path, body) => request("POST", path, { body: body ?? {} });
export const del = (path) => request("DELETE", path);

export function closeFullPage() {
  return request("POST", "/v1/fullpage/close", { body: {} }).catch(() => {});
}

function takeLaunchParams() {
  const url = new URL(window.location.href);
  const nonce = url.searchParams.get("launch");
  const q = url.searchParams.get("q") || "";
  if (url.searchParams.has("launch") || url.searchParams.has("q")) {
    url.searchParams.delete("launch");
    url.searchParams.delete("q");
    const rest = url.searchParams.toString();
    window.history.replaceState(null, "", url.pathname + (rest ? "?" + rest : "") + url.hash);
  }
  return { nonce, q };
}

let bootPromise = null;

export function boot() {
  if (!bootPromise) {
    const { nonce, q } = takeLaunchParams();
    bootPromise = (async () => {
      if (!nonce) return { status: "no-token", q };
      try {
        await exchangeNonce(nonce);
        return { status: "ready", q };
      } catch (error) {
        return { status: "failed", q, message: error.message };
      }
    })();
  }
  return bootPromise;
}
