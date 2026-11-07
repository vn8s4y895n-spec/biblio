<script>
  import { onMount } from "svelte";
  import Ask from "./Ask.svelte";
  import Library from "./Library.svelte";
  import Settings from "./Settings.svelte";
  import { api, token } from "./lib/api.js";
  import { lang, systemLang, t } from "./lib/i18n.js";
  import { toast, toasts } from "./lib/toast.js";

  // Routes : #/ (question), #/search/<id> (recherche enregistrée), #/library[/<notice>], #/settings
  let route = $state(location.hash.slice(1) || "/");
  let settings = $state(null);
  let dragging = $state(false);
  let libraryVersion = $state(0); // incrémenté après un import, pour rafraîchir la bibliothèque

  const view = $derived(route.startsWith("/library") ? "library" : route.startsWith("/settings") ? "settings" : "ask");

  function applyPrefs(s) {
    settings = s;
    lang.set(s.language || systemLang);
    document.documentElement.dataset.theme = s.theme === "system" ? "" : s.theme;
  }

  onMount(() => {
    const onHash = () => (route = location.hash.slice(1) || "/");
    addEventListener("hashchange", onHash);
    if (token) api("/settings").then(applyPrefs).catch((e) => toast(e.message, "error"));
    return () => removeEventListener("hashchange", onHash);
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
      </nav>
      <div class="grow"></div>
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
        <Ask {route} />
      {:else if view === "library"}
        <Library {route} version={libraryVersion} />
      {:else if settings}
        <Settings {settings} onchange={applyPrefs} />
      {/if}
    </div>
  </div>

  {#if dragging}
    <div class="dropzone"><div>{$t("drop.hint")}</div></div>
  {/if}
{/if}

<div class="toasts" aria-live="polite">
  {#each $toasts as item (item.id)}
    <div class="toast {item.kind}">{item.text}</div>
  {/each}
</div>

<style>
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

  .toasts { position: fixed; right: 18px; bottom: 18px; display: flex; flex-direction: column; gap: 8px; z-index: 60; max-width: 420px; }
  .toast {
    background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--accent);
    border-radius: 8px; padding: 9px 14px; box-shadow: var(--shadow);
  }
  .toast.ok { border-left-color: var(--ok); }
  .toast.warn { border-left-color: var(--warn); }
  .toast.error { border-left-color: var(--danger); }
</style>
