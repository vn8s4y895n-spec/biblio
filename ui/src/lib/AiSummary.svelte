<script>
  // Résumé rédigé par l'IA, à la demande (gardé en cache côté serveur).
  import { api } from "./api.js";
  import { lang, t } from "./i18n.js";

  let { rec } = $props();
  let out = $state(null);
  let loading = $state(false);
  let error = $state(null);

  async function load(e) {
    e?.stopPropagation();
    loading = true;
    error = null;
    try {
      out = await api("/summary", { method: "POST", body: { record: { title: rec.title, abstract: rec.abstract }, lang: $lang } });
    } catch (err) {
      error = err.message;
    } finally {
      loading = false;
    }
  }
</script>

{#if out}
  <p class="ai"><span class="tag">IA</span> {out.text}</p>
{:else if rec.abstract}
  <button class="ghost small" disabled={loading} onclick={load}>✦ {loading ? $t("ai.summarizing") : $t("ai.summary")}</button>
  {#if error}<p class="small error">{error}</p>{/if}
{/if}

<style>
  .ai { margin: 4px 0 0; font-size: 13.5px; background: var(--accent-soft); border-radius: 7px; padding: 6px 10px; }
  .tag { font: 700 10px var(--font); letter-spacing: 0.06em; color: var(--accent); margin-right: 4px; }
  .error { color: var(--danger); margin: 2px 0 0; }
</style>
