// Reçoit un signalement de Biblio ({ title, body }) et crée un ticket dans le dépôt privé REPORTS_REPO.
// Variables d'environnement (portée Functions) : GITHUB_TOKEN (clé limitée aux tickets de ce dépôt), REPORTS_REPO.
import type { Config } from "@netlify/functions"

const MAX_TITLE = 200
const MAX_BODY = 20_000

export default async (req: Request) => {
  let data: { title?: unknown; body?: unknown }
  try {
    data = await req.json()
  } catch {
    return Response.json({ error: "JSON attendu" }, { status: 400 })
  }
  const { title, body } = data
  if (typeof title !== "string" || typeof body !== "string" || !title.trim() || title.length > MAX_TITLE || body.length > MAX_BODY) {
    return Response.json({ error: "Signalement invalide" }, { status: 400 })
  }
  const token = Netlify.env.get("GITHUB_TOKEN")
  const repo = Netlify.env.get("REPORTS_REPO")
  if (!token || !repo) return Response.json({ error: "Relais non configuré" }, { status: 503 })

  const res = await fetch(`https://api.github.com/repos/${repo}/issues`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "biblio-relay",
    },
    body: JSON.stringify({ title: title.trim(), body }),
  })
  if (!res.ok) {
    console.error("GitHub", res.status, await res.text())
    return Response.json({ error: "Ticket non créé" }, { status: 502 })
  }
  const issue = await res.json()
  return Response.json({ ok: true, number: issue.number }, { status: 201 })
}

export const config: Config = {
  path: "/api/report",
  method: "POST",
  // Contre les abus : 5 signalements par minute et par adresse IP.
  rateLimit: { windowLimit: 5, windowSize: 60, aggregateBy: ["ip", "domain"] },
}
