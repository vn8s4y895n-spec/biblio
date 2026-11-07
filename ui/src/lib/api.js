// Client de l'API REST. Le jeton arrive dans l'adresse (#token=…, jamais envoyé au serveur), puis il est gardé
// pour la session.
const KEY = "biblio.token";

function readToken() {
  const match = location.hash.match(/token=([^&]+)/);
  if (match) {
    try { sessionStorage.setItem(KEY, match[1]); } catch {}
    history.replaceState(null, "", location.pathname + "#/");
    return match[1];
  }
  try { return sessionStorage.getItem(KEY); } catch { return null; }
}

export const token = readToken();

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
  }
}

// `body` : objet (envoyé en JSON) ou Blob/File (envoyé tel quel).
export async function api(path, { method = "GET", body, params } = {}) {
  const url = new URL(`/api/v1${path}`, location.origin);
  for (const [k, v] of Object.entries(params ?? {})) {
    for (const item of [v].flat()) if (item !== undefined && item !== null && item !== "") url.searchParams.append(k, item);
  }
  const headers = { Authorization: `Bearer ${token}`, "X-Biblio-Client": "web" };
  const raw = body instanceof Blob;
  if (body !== undefined && !raw) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { method, headers, body: raw ? body : body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail; } catch {}
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return null;
  return response.headers.get("content-type")?.includes("json") ? response.json() : response.blob();
}

// Identifiant à envoyer à POST /works pour une notice trouvée en ligne.
export function identifierOf(rec) {
  const ids = rec.identifiers ?? {};
  return ids.doi ?? (ids.pmid && `PMID:${ids.pmid}`) ?? (ids.pmcid && ids.pmcid) ?? (ids.arxiv && `arXiv:${ids.arxiv}`) ?? null;
}
