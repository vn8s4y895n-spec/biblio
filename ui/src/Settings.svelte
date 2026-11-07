<script>
  import { onDestroy, onMount } from "svelte";
  import { api } from "./lib/api.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let { settings, onchange } = $props();

  // svelte-ignore state_referenced_locally
  let form = $state({ ...settings }); // copie modifiable ; chaque champ est enregistré à part
  let semantic = $state(null);
  let journals = $state(null);
  let checking = $state(false);
  let restartNeeded = $state(false);
  let styles = $state([]);
  let zotero = $state(null);
  let poll;

  const KEYS = [
    ["ncbi_api_key", "https://account.ncbi.nlm.nih.gov/settings/"],
    ["openalex_api_key", "https://openalex.org/settings/api"],
    ["semanticscholar_api_key", "https://www.semanticscholar.org/product/api#api-key-form"],
  ];

  onMount(async () => {
    [semantic, journals, styles, zotero] = await Promise.all([api("/semantic"), api("/journals"), api("/styles"), api("/zotero")]);
    poll = setInterval(async () => {
      if (semantic?.status === "downloading" || (semantic?.status === "ready" && semantic.progress < 1)) semantic = await api("/semantic");
    }, 2000);
  });
  onDestroy(() => clearInterval(poll));

  async function save(field) {
    if (form[field] === settings[field]) return;
    try {
      const out = await api("/settings", { method: "PUT", body: { [field]: form[field] } });
      restartNeeded = out.restart_needed;
      onchange(out);
      toast($t("settings.saved"), "ok", 2000);
    } catch (e) {
      form[field] = settings[field];
      toast(e.message, "error");
    }
  }

  async function download() {
    semantic = await api("/semantic/download", { method: "POST" });
  }

  async function checkJournals() {
    checking = true;
    try {
      const out = await api("/journals/update", { method: "POST" });
      journals = { ...journals, ...out };
      toast(out.updated ? $t("settings.journals_new") : $t("settings.journals_same"), "ok");
    } catch (e) {
      toast(e.message, "error", 8000);
    } finally {
      checking = false;
    }
  }

  async function addStyle(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const added = await api("/styles", { method: "POST", body: file, params: { filename: file.name } });
      styles = await api("/styles");
      form.citation_style = added.id;
      save("citation_style");
    } catch (err) {
      toast(err.message, "error", 8000);
    }
  }

  const gb = (bytes) => (bytes / 1e9).toFixed(1).replace(".", $t("common.decimal"));
</script>

<div class="settings scroll">
  <div class="inner">
    <h1>{$t("nav.settings")}</h1>

    <section class="card">
      <h2>{$t("settings.display")}</h2>
      <div class="field">
        <span>{$t("settings.language")}</span>
        <select bind:value={form.language} onchange={() => save("language")}>
          <option value="">{$t("settings.system")}</option>
          <option value="fr">Français</option>
          <option value="en">English</option>
        </select>
      </div>
      <div class="field">
        <span>{$t("settings.theme")}</span>
        <select bind:value={form.theme} onchange={() => save("theme")}>
          <option value="system">{$t("settings.system")}</option>
          <option value="light">{$t("settings.light")}</option>
          <option value="dark">{$t("settings.dark")}</option>
        </select>
      </div>
    </section>

    <section class="card">
      <h2>{$t("settings.citations")}</h2>
      <div class="field">
        <span>{$t("cite.style")}<small class="faint">{$t("settings.style_help")}</small></span>
        <select bind:value={form.citation_style} onchange={() => save("citation_style")}>
          {#each styles as s}<option value={s.id}>{s.title}</option>{/each}
        </select>
      </div>
      <div class="field">
        <span>{$t("settings.add_style")}<small class="faint">{$t("settings.add_style_help")} <a href="https://www.zotero.org/styles" target="_blank" rel="noreferrer">zotero.org/styles ↗</a></small></span>
        <label class="file"><input type="file" accept=".csl" onchange={addStyle} />{$t("settings.choose_file")}</label>
      </div>
      <p class="small faint">{$t("settings.library_bib", { path: `${settings.home}/library.bib` })}</p>
    </section>

    <section class="card">
      <h2>{$t("settings.sources")}</h2>
      <p class="muted small">{$t("settings.sources_help")}</p>
      <label class="field">
        <span>{$t("settings.email")}<small class="faint">{$t("settings.email_help")}</small></span>
        <input type="email" bind:value={form.contact_email} onchange={() => save("contact_email")} placeholder="prenom.nom@exemple.fr" />
      </label>
      {#each KEYS as [field, url]}
        <label class="field">
          <span>{$t(`settings.${field}`)}<small class="faint">{$t(`settings.${field}_help`)} <a href={url} target="_blank" rel="noreferrer">↗</a></small></span>
          <input type="password" autocomplete="off" bind:value={form[field]} onchange={() => save(field)} placeholder={$t("settings.optional")} />
        </label>
      {/each}
      <p class="small faint">{$t("settings.stored_in", { path: `${settings.home}/config.toml` })}</p>
    </section>

    <section class="card">
      <h2>{$t("settings.import")}</h2>
      <label class="field toggle">
        <span>{$t("settings.watch_downloads")}<small class="faint">{$t("settings.watch_downloads_help")}</small></span>
        <input type="checkbox" bind:checked={form.watch_downloads} onchange={() => save("watch_downloads")} />
      </label>
      <label class="field">
        <span>{$t("settings.downloads_dir")}</span>
        <input type="text" bind:value={form.downloads_dir} onchange={() => save("downloads_dir")} disabled={!form.watch_downloads} />
      </label>
      <p class="small faint">{$t("settings.inbox_help", { path: `${settings.home}/inbox` })}</p>
    </section>

    <section class="card">
      <h2>{$t("settings.semantic")}</h2>
      <p class="muted small">{$t("settings.semantic_help")}</p>
      <label class="field toggle">
        <span>{$t("settings.semantic_on")}</span>
        <input type="checkbox" bind:checked={form.semantic} onchange={() => save("semantic")} />
      </label>
      {#if restartNeeded}<p class="small warn">{$t("settings.restart")}</p>{/if}
      {#if semantic}
        <div class="field">
          <span>{$t("settings.model")} <small class="faint mono">{semantic.model}</small></span>
          <span class="status">
            {#if semantic.status === "absent" || semantic.status === "error"}
              <button class="primary" onclick={download} disabled={!form.semantic}>{$t("settings.download", { size: gb(semantic.expected_bytes) })}</button>
            {:else if semantic.status === "downloading"}
              <progress max={semantic.expected_bytes} value={semantic.downloaded_bytes}></progress>
              <span class="small">{gb(semantic.downloaded_bytes)} / {gb(semantic.expected_bytes)} Go</span>
            {:else}
              <span class="chip ok">✓ {$t(`settings.model_${semantic.status}`)}</span>
            {/if}
          </span>
        </div>
        {#if semantic.error}<p class="small warn">{semantic.error}</p>{/if}
        {#if semantic.status === "ready"}
          <div class="field">
            <span>{$t("settings.indexing")}</span>
            <span class="status">
              <progress max="1" value={semantic.progress}></progress>
              <span class="small">{$t("settings.indexed", { done: $fmt.number(semantic.indexed), total: $fmt.number(semantic.passages) })}</span>
            </span>
          </div>
        {/if}
      {/if}
    </section>

    <section class="card">
      <h2>{$t("settings.journals")}</h2>
      <p class="muted small">{$t("settings.journals_help")} <a href={journals?.page_url} target="_blank" rel="noreferrer">↗</a></p>
      {#if journals?.current}
        <div class="field">
          <span>{$t("settings.journals_version")}</span>
          <span>{$fmt.date(journals.current.published_on)} · {$t("settings.journals_count", { n: $fmt.number(journals.current.row_count) })}</span>
        </div>
      {/if}
      <div class="field">
        <span>{$t("settings.journals_checked")}</span>
        <span class="status">
          <span class="small">{journals?.last_check ? $fmt.date(journals.last_check) : "—"}</span>
          <button onclick={checkJournals} disabled={checking}>{checking ? "…" : $t("settings.journals_check")}</button>
        </span>
      </div>
      {#if journals?.last_error}<p class="small warn">{journals.last_error}</p>{/if}
    </section>

    <section class="card">
      <h2>{$t("settings.connectors")}</h2>
      <div class="field">
        <span>Zotero<small class="faint">{$t("settings.zotero_help")}</small></span>
        <span class="status">
          {#if zotero?.running}
            <span class="chip ok">✓ {$t("settings.zotero_on", { where: zotero.collection ?? zotero.library })}</span>
          {:else}
            <span class="chip">{$t("settings.zotero_off")}</span>
          {/if}
          <button class="small" onclick={async () => (zotero = await api("/zotero"))}>{$t("settings.check")}</button>
        </span>
      </div>
      <p class="muted small">{$t("settings.connectors_soon")}</p>
    </section>

    <p class="small faint about">Biblio {settings.version} · {$t("settings.data", { path: settings.home })}</p>
  </div>
</div>

<style>
  .settings { height: 100%; }
  .inner { max-width: 720px; margin: 0 auto; padding: 28px 20px 60px; display: grid; gap: 16px; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  h2 { margin: 0 0 4px; font-size: 15px; }
  section { padding: 16px 18px; display: grid; gap: 10px; }
  section p { margin: 0; }
  .field { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .field > span:first-child { display: grid; flex: 1; }
  .field input[type="text"], .field input[type="email"], .field input[type="password"] { flex: 0 0 min(300px, 50%); }
  .field small { font-size: 12px; }
  .status { display: flex; align-items: center; gap: 10px; }
  progress { width: 160px; accent-color: var(--accent); }
  .ok { background: var(--ok-soft); color: var(--ok); }
  .warn { color: var(--warn); }
  .mono { font-family: var(--mono); }
  .about { text-align: center; }
  .file { border: 1px solid var(--border); border-radius: 7px; padding: 5px 11px; cursor: pointer; background: var(--surface); }
  .file input { display: none; }
</style>
