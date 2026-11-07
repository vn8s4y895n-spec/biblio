# Installer Biblio · Installing Biblio

[Français](#français) · [English](#english)

## Français

### Mac (Apple Silicon, macOS 12 ou plus récent)

1. Ouvre `Biblio-<version>.dmg`, puis fais glisser **Biblio** sur le dossier **Applications**.
2. Premier lancement : Biblio n'est pas signé par un compte Apple Developer, donc macOS demande une confirmation.
   - Double-clique sur Biblio : macOS refuse de l'ouvrir. Clique sur **OK**.
   - Ouvre **Réglages Système → Confidentialité et sécurité**, descends jusqu'au message sur Biblio, clique sur **Ouvrir quand même**, puis confirme.
   - Les lancements suivants se font normalement, d'un double-clic.
3. Un fichier `.biblio` reçu d'un collègue s'ouvre d'un double-clic : Biblio montre ce qu'il contient avant de l'importer.

### Windows 10 ou 11

1. Lance `Biblio-<version>-setup.exe`. Si Windows affiche « Windows a protégé votre ordinateur », clique sur **Informations complémentaires → Exécuter quand même** (l'installeur n'est pas signé).
2. Biblio s'installe pour ton compte seulement (pas besoin d'être administrateur), avec un raccourci sur le Bureau. Les fichiers `.biblio` s'ouvrent d'un double-clic.

### Premiers réglages (Réglages, dans Biblio)

- **E-mail de contact** : recommandé (Crossref, OpenAlex et PubMed le demandent ; indispensable pour trouver les PDF libres).
- **Recherche par le sens** : le modèle (2,3 Go) se télécharge une fois, depuis les Réglages.
- **IA** (facultative) : installe [Ollama](https://ollama.com), puis active-la dans les Réglages.
- **Claude** (facultatif, avec un abonnement Claude) : Réglages → Connecteurs → « Extension .mcpb », puis double-clic sur le fichier.

### Mettre à jour

Remplace l'application par la nouvelle version (Mac : glisser la nouvelle dans Applications ; Windows : lancer le nouvel installeur). Ta bibliothèque n'est pas dans l'application : elle est gardée telle quelle et mise à niveau automatiquement au premier lancement. Une sauvegarde est faite chaque jour (Réglages → Connecteurs → Sauvegardes).

### Où sont mes données ?

Mac : `~/Library/Application Support/Biblio` ; Windows : `%APPDATA%\Biblio`. Ne les place pas dans iCloud Drive ou OneDrive.

## English

### Mac (Apple Silicon, macOS 12 or later)

1. Open `Biblio-<version>.dmg` and drag **Biblio** onto **Applications**.
2. First launch: Biblio is not signed with an Apple Developer account, so macOS asks for confirmation.
   - Double-click Biblio: macOS refuses to open it. Click **OK**.
   - Open **System Settings → Privacy & Security**, scroll to the message about Biblio, click **Open Anyway**, then confirm.
   - Later launches work normally with a double-click.
3. A `.biblio` file received from a colleague opens with a double-click: Biblio shows what it contains before importing it.

### Windows 10 or 11

1. Run `Biblio-<version>-setup.exe`. If Windows shows “Windows protected your PC”, click **More info → Run anyway** (the installer is not signed).
2. Biblio installs for your account only (no administrator rights needed), with a desktop shortcut. `.biblio` files open with a double-click.

### First settings (Settings, in Biblio)

- **Contact e-mail**: recommended (Crossref, OpenAlex and PubMed ask for it; required to find free PDFs).
- **Semantic search**: the model (2.3 GB) downloads once, from Settings.
- **AI** (optional): install [Ollama](https://ollama.com), then turn it on in Settings.
- **Claude** (optional, with a Claude subscription): Settings → Connectors → “.mcpb extension”, then double-click the file.

### Updating

Replace the application with the new version (Mac: drag the new one into Applications; Windows: run the new installer). Your library is not inside the application: it is kept as is and upgraded automatically on first launch. A backup is made every day (Settings → Connectors → Backups).

### Where is my data?

Mac: `~/Library/Application Support/Biblio`; Windows: `%APPDATA%\Biblio`. Do not put it in iCloud Drive or OneDrive.
