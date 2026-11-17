# Relais des signalements

Fonction Netlify (`netlify/functions/report.mts`, servie à `/api/report`) : Biblio y envoie un signalement
`{ title, body }`, le relais crée un ticket dans un dépôt GitHub **privé**. La clé GitHub ne quitte pas Netlify.

Variables d'environnement du projet Netlify `biblio-signalements` (portée : Functions) :
- `GITHUB_TOKEN` : clé « fine-grained » limitée au dépôt des signalements, permission Issues en lecture/écriture.
- `REPORTS_REPO` : `propriétaire/dépôt` des signalements.

Limite : 5 signalements par minute et par adresse IP. Déploiement : `npx netlify-cli deploy --prod --site <id>`
depuis ce dossier (après `netlify login`).
