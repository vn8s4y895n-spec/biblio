<script>
  import { onMount } from "svelte";
  import Ask from "./Ask.svelte";
  import Library from "./Library.svelte";
  import Settings from "./Settings.svelte";
  import Watch from "./Watch.svelte";
  import { api, token } from "./lib/api.js";
  import { lang, systemLang, t } from "./lib/i18n.js";
  import { toast, toasts } from "./lib/toast.js";

  // Routes : #/ (question), #/search/<id> (recherche enregistrée), #/library[/<notice>], #/settings
  let route = $state(location.hash.slice(1) || "/");
  let settings = $state(null);
  let dragging = $state(false);
  let libraryVersion = $state(0); // incrémenté après un import, pour rafraîchir la bibliothèque
  let incoming = $state(null); // paquet .biblio déposé : { file, preview }
  let importing = $state(false);

  const view = $derived(
    route.startsWith("/library") ? "library" : route.startsWith("/settings") ? "settings" : route.startsWith("/watch") ? "watch" : "ask",
  );
  let unseen = $state(0); // alertes de veille non lues
  let update = $state(null); // nouvelle version publiée sur GitHub
  let installing = $state(false);

  // Mise à jour en un clic (app installée) ; sinon, page de téléchargement.
  async function installUpdate() {
    if (!update.installable) {
      open(update.download ?? update.url, "_blank");
      return;
    }
    if (!confirm($t("app.update_confirm", { version: update.latest }))) return;
    installing = true;
    try {
      await api("/update/install", { method: "POST" });
      toast($t("app.update_restarting"), "ok", 60_000);
    } catch (e) {
      installing = false;
      toast(e.message, "error", 10_000);
    }
  }

  async function refreshAlerts() {
    try { unseen = (await api("/alerts", { params: { unseen_only: true } })).unseen; } catch {}
  }

  function applyPrefs(s) {
    settings = s;
    lang.set(s.language || systemLang);
    document.documentElement.dataset.theme = s.theme === "system" ? "" : s.theme;
  }

  onMount(() => {
    const onHash = () => (route = location.hash.slice(1) || "/");
    addEventListener("hashchange", onHash);
    if (token) api("/settings").then(applyPrefs).catch((e) => toast(e.message, "error"));
    refreshAlerts();
    api("/update").then((u) => (update = u)).catch(() => {}); // hors ligne : rien à signaler
    // Paquet .biblio ouvert par double-clic : la fenêtre (app.py) appelle biblioOpen(chemin).
    window.biblioOpen = async (path) => {
      try {
        const blob = await api("/packages/file", { method: "POST", body: { path } });
        const file = new File([blob], path.split(/[\\/]/).pop());
        incoming = { file, preview: await api("/packages/preview", { method: "POST", body: file }) };
      } catch (e) {
        toast(e.message, "error", 8000);
      }
    };
    const timer = setInterval(refreshAlerts, 60_000);
    return () => { removeEventListener("hashchange", onHash); clearInterval(timer); };
  });

  $effect(() => {
    document.documentElement.lang = $lang;
  });

  async function setLang(value) {
    applyPrefs(await api("/settings", { method: "PUT", body: { language: value } }));
  }

  // Glisser-déposer : PDF → bibliothèque ; .bib, .ris, .nbib, .json → import de références.
  async function drop(event) {
    event.preventDefault();
    dragging = false;
    for (const file of event.dataTransfer?.files ?? []) {
      try {
        if (file.name.toLowerCase().endsWith(".biblio")) {
          incoming = { file, preview: await api("/packages/preview", { method: "POST", body: file }) };
          continue;
        }
        if (file.name.toLowerCase().endsWith(".pdf")) {
          const out = await api("/files", { method: "POST", body: file, params: { filename: file.name } });
          toast($t(`drop.pdf.${out.status}`, { name: file.name, title: out.work?.title ?? "" }), out.status === "needs_metadata" ? "warn" : "ok");
        } else {
          const out = await api("/import", { method: "POST", body: file });
          toast($t("drop.refs", { name: file.name, created: out.created.length, updated: out.completed.length }), "ok");
        }
        libraryVersion++;
      } catch (e) {
        toast(`${file.name} : ${e.message}`, "error", 8000);
      }
    }
  }

  async function importPackage() {
    importing = true;
    try {
      const out = await api("/packages/import", { method: "POST", body: incoming.file });
      toast($t("package.imported", { created: out.created.length, completed: out.completed.length, pdfs: out.pdfs, notes: out.notes }), "ok", 7000);
      for (const err of out.errors) toast(err, "warn", 8000);
      incoming = null;
      libraryVersion++;
    } catch (e) {
      toast(e.message, "error");
    } finally {
      importing = false;
    }
  }
</script>

<svelte:window
  ondragover={(e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); dragging = true; } }}
  ondragleave={(e) => { if (!e.relatedTarget) dragging = false; }}
  ondrop={drop}
/>

{#if !token}
  <main class="locked">
    <h1>Biblio</h1>
    <p>{$t("app.no_token")}</p>
    <code>biblio app</code>
  </main>
{:else}
  <div class="shell">
    <header>
      <a class="brand" href="#/"><span class="logo">B</span>Biblio</a>
      <nav>
        <a href="#/" class:active={view === "ask"}>{$t("nav.ask")}</a>
        <a href="#/library" class:active={view === "library"}>{$t("nav.library")}</a>
        <a href="#/watch" class:active={view === "watch"}>{$t("nav.watch")}{#if unseen}<span class="count">{unseen}</span>{/if}</a>
      </nav>
      <div class="grow"></div>
      {#if update?.available}
        <button class="update" disabled={installing} onclick={installUpdate} title={$t(update.installable ? "app.update_install_help" : "app.update_help")}>
          {installing ? $t("app.update_downloading") : $t("app.update", { version: update.latest })}
        </button>
      {/if}
      <div class="lang" role="group" aria-label={$t("settings.language")}>
        {#each ["fr", "en"] as code}
          <button class="ghost small" class:on={$lang === code} onclick={() => setLang(code)}>{code.toUpperCase()}</button>
        {/each}
      </div>
      <a class="gear" href="#/settings" class:active={view === "settings"} title={$t("nav.settings")} aria-label={$t("nav.settings")}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>
      </a>
    </header>

    <div class="body">
      {#if view === "ask"}
        <Ask {route} aiEnabled={!!settings?.llm_enabled} />
      {:else if view === "library"}
        <Library {route} version={libraryVersion} aiEnabled={!!settings?.llm_enabled} />
      {:else if view === "watch"}
        <Watch onchange={(n) => (unseen = n)} />
      {:else if settings}
        <Settings {settings} onchange={applyPrefs} />
      {/if}
    </div>
  </div>

  {#if dragging}
    <div class="dropzone"><div>{$t("drop.hint")}</div></div>
  {/if}
{/if}

{#if incoming}
  <div class="backdrop" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) incoming = null; }}>
    <div class="modal card" role="dialog" aria-modal="true" aria-labelledby="pkg-title">
      <h2 id="pkg-title">{incoming.preview.manifest.title || incoming.file.name}</h2>
      <p class="small muted">
        {$t("package.preview", { n: incoming.preview.works.length, date: incoming.preview.manifest.created_at?.slice(0, 10) ?? "" })}
        {#if incoming.preview.searches.length} · {$t("package.searches", { n: incoming.preview.searches.length })}{/if}
      </p>
      <ul class="scroll">
        {#each incoming.preview.works as w}
          <li>
            <span class="title-serif">{w.title}</span>
            <span class="small muted">{[w.first_author, w.year, w.container_title].filter(Boolean).join(" · ")}</span>
            <span class="row small">
              <span class="chip" class:new={w.status === "new"}>{$t(`package.${w.status}`)}</span>
              {#if w.pdf}<span class="chip">PDF</span>{/if}
              {#if w.note}<span class="chip">{$t("detail.note")}</span>{/if}
              {#each w.labels as l}<span class="chip">{l.kind === "tag" ? "#" : ""}{l.name}</span>{/each}
            </span>
          </li>
        {/each}
      </ul>
      <p class="small faint">{$t("package.rules")}</p>
      <div class="row">
        <div class="grow"></div>
        <button onclick={() => (incoming = null)}>{$t("common.cancel")}</button>
        <button class="primary" disabled={importing} onclick={importPackage}>{importing ? "…" : $t("package.import")}</button>
      </div>
    </div>
  </div>
{/if}

<div class="toasts" aria-live="polite">
  {#each $toasts as item (item.id)}
    <div class="toast {item.kind}">{item.text}</div>
  {/each}
</div>

<style>
  .update { border: none; cursor: pointer; font-size: 12.5px; font-weight: 600; color: var(--accent); background: var(--accent-soft); padding: 4px 10px; border-radius: 999px; text-decoration: none; }
  .shell { display: flex; flex-direction: column; height: 100%; }
  header {
    display: flex; align-items: center; gap: 18px; padding: 0 18px; height: 50px; flex: none;
    border-bottom: 1px solid var(--border); background: var(--surface);
    -webkit-app-region: drag;
  }
  header a, header button { -webkit-app-region: no-drag; }
  .brand { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 15px; color: var(--text); }
  .brand:hover { text-decoration: none; }
  .logo {
    width: 24px; height: 24px; border-radius: 6px; display: grid; place-items: center;
    background: var(--accent); color: var(--accent-text); font-family: var(--serif); font-size: 15px;
  }
  nav { display: flex; gap: 2px; }
  nav a, .gear {
    color: var(--muted); padding: 5px 11px; border-radius: 7px; font-weight: 520;
  }
  nav a:hover, .gear:hover { background: var(--surface-2); color: var(--text); text-decoration: none; }
  nav a.active, .gear.active { color: var(--text); background: var(--surface-2); }
  .gear { display: grid; place-items: center; padding: 6px; }
  .count {
    margin-left: 6px; background: var(--danger); color: white; border-radius: 99px; font-size: 11px; font-weight: 700;
    padding: 0 6px; line-height: 17px; display: inline-block;
  }
  .lang { display: flex; }
  .lang .on { color: var(--text); font-weight: 650; }
  .body { flex: 1; min-height: 0; }

  .locked { max-width: 420px; margin: 15vh auto; text-align: center; }
  .locked code { font: 14px var(--mono); background: var(--surface-2); padding: 4px 8px; border-radius: 6px; }

  .dropzone {
    position: fixed; inset: 0; display: grid; place-items: center; pointer-events: none;
    background: color-mix(in srgb, var(--accent) 12%, transparent); border: 3px dashed var(--accent); z-index: 50;
  }
  .dropzone div { background: var(--surface); padding: 14px 22px; border-radius: 12px; box-shadow: var(--shadow); font-weight: 600; }

  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.35); display: grid; place-items: center; z-index: 55; }
  .modal { width: min(640px, 92vw); max-height: 80vh; display: flex; flex-direction: column; gap: 10px; padding: 20px 22px; }
  .modal h2 { margin: 0; font-size: 18px; }
  .modal p { margin: 0; }
  .modal ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
  .modal li { display: grid; gap: 2px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
  .modal .new { background: var(--accent-soft); color: var(--accent); }
  .toasts { position: fixed; right: 18px; bottom: 18px; display: flex; flex-direction: column; gap: 8px; z-index: 60; max-width: 420px; }
  .toast {
    background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--accent);
    border-radius: 8px; padding: 9px 14px; box-shadow: var(--shadow);
  }
  .toast.ok { border-left-color: var(--ok); }
  .toast.warn { border-left-color: var(--warn); }
  .toast.error { border-left-color: var(--danger); }
</style>
