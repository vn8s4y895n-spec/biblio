<script>
  // Déroulé d'une recherche : ce que Biblio fait, étape par étape (traductions, requête de l'IA, requêtes envoyées
  // aux bases, fusion, filtres). Ouvert pendant la recherche, replié ensuite en une ligne.
  import { t } from "./i18n.js";

  let { trace } = $props(); // { steps: [...], running, ms }
  let open = $state(false);
  let list = $state();

  const sources = $derived(new Set(trace.steps.filter((s) => s.kind === "source").map((s) => s.name))); // une base relancée compte une fois
  const requests = $derived(trace.steps.filter((s) => s.kind === "request"));
  const shown = $derived(trace.running || open);
  const seconds = (ms) => (ms / 1000).toFixed(1).replace(".", $t("common.decimal"));

  $effect(() => {
    trace.steps.length; // suivre la dernière étape pendant la recherche
    if (trace.running && list) list.scrollTop = list.scrollHeight;
  });

  function host(url) {
    try { return new URL(url).host; } catch { return url; }
  }
  function target(url) {
    try { const u = new URL(url); return decodeURIComponent(u.pathname + u.search); } catch { return url; }
  }
</script>

<div class="trace" class:running={trace.running}>
  <button class="head ghost small" onclick={() => (open = !open)} aria-expanded={shown} disabled={trace.running}>
    {#if trace.running}<span class="spinner" aria-hidden="true"></span>{:else}<span>{open ? "▾" : "▸"}</span>{/if}
    <span class="grow">
      {trace.running ? $t("trace.running") : $t("trace.summary", { sources: sources.size, requests: requests.length, time: seconds(trace.ms) })}
    </span>
  </button>
  {#if shown}
    <ol bind:this={list}>
      {#each trace.steps as s, i (i)}
        <li class={s.kind} class:fail={s.error || s.status === "error" || (s.status >= 400)}>
          <span class="t">{seconds(s.t)} s</span>
          {#if s.kind === "keywords"}
            <span class="what">{$t("trace.keywords")}</span>
            <code>{s.terms.join(" · ")}</code>
            {#each s.translated as [from, to]}<span class="tr">{from} → {to}</span>{/each}
          {:else if s.kind === "syntax" || s.kind === "user_query"}
            <span class="what">{$t(`trace.${s.kind}`)}</span> <code>{s.query}</code>
          {:else if s.kind === "ai_plan_start"}
            <span class="what">{$t("trace.ai_start", { model: s.model })}</span>
          {:else if s.kind === "ai_plan"}
            <span class="what">{$t("trace.ai_plan")}</span> <code>{s.query}</code>
          {:else if s.kind === "ai_error"}
            <span class="what">{$t("trace.ai_error")}</span> <span class="err">{s.error}</span>
          {:else if s.kind === "query"}
            <span class="what">{$t("trace.query", { n: s.sources.length })}</span>
            <code>{s.boolean}</code>
            {#if s.plain !== s.boolean}<span class="tr">{$t("trace.plain")} <code>{s.plain}</code></span>{/if}
          {:else if s.kind === "request"}
            <span class="req">
              <span class="host" title={host(s.url)}>{s.source ?? host(s.url)}</span>
              <span class="url" title={s.url}>{target(s.url)}</span>
              <span class="res">{s.error ?? (s.cached ? $t("trace.cached") : `${s.status} · ${s.ms} ms`)}</span>
            </span>
          {:else if s.kind === "source"}
            <span class="what">{s.label}</span>
            {#if s.status === "ok"}{$t("trace.source", { n: s.count, time: seconds(s.ms) })}{:else}<span class="err">{s.error}</span>{/if}
          {:else if s.kind === "merge"}
            <span class="what">{$t("trace.merge", { found: s.found, merged: s.merged, kept: s.kept })}</span>
            {#each Object.entries(s.hidden) as [status, n]}<span class="tr">{$t("trace.hidden", { n, status: $t(`status.${status}.label`) })}</span>{/each}
          {:else if s.kind === "rerank"}
            <span class="what">{$t("trace.rerank", { n: s.count })}</span>
          {:else if s.kind === "fallback" || s.kind === "relax"}
            <span class="what">{$t(`trace.${s.kind}`, { found: s.found, minimum: s.minimum })}</span> <code>{s.query}</code>
          {:else if s.kind === "topic"}
            <span class="what">{$t(`trace.topic_${s.by}`, { removed: s.removed, kept: s.kept, need: Math.round(s.need * 100) })}</span>
          {:else if s.kind === "library"}
            <span class="what">{$t("trace.library", { n: s.total, engine: $t(`trace.engine.${s.engine}`) })}</span>
          {:else if s.kind === "answer"}
            <span class="what">{$t("trace.answer", { n: s.shown })}</span>
          {/if}
        </li>
      {/each}
    </ol>
  {/if}
</div>

<style>
  .trace { border: 1px solid var(--border); border-radius: 10px; background: var(--surface); font-size: 12.5px; }
  .head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 7px 12px; text-align: left; color: var(--muted); }
  .head:disabled { opacity: 1; cursor: default; }
  ol { list-style: none; margin: 0; padding: 4px 12px 10px; max-height: 260px; overflow: auto; display: grid; grid-template-columns: minmax(0, 1fr); gap: 3px;
    border-top: 1px solid var(--border); }
  li { line-height: 1.45; color: var(--text); padding-left: 46px; position: relative; overflow-wrap: anywhere; }
  li.request { color: var(--muted); }
  .t { position: absolute; left: 0; width: 40px; text-align: right; color: var(--faint); font-variant-numeric: tabular-nums; }
  .what { font-weight: 600; margin-right: 4px; }
  code { font-family: var(--mono); font-size: 11.5px; background: var(--accent-soft); padding: 1px 5px; border-radius: 4px; word-break: break-all; }
  .tr { margin-left: 8px; color: var(--muted); }
  .req { display: flex; gap: 8px; min-width: 0; }
  .host { flex: none; width: 110px; color: var(--accent); }
  .url { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--mono); font-size: 11px; }
  .res { flex: none; }
  .fail, .err { color: var(--danger); }
  .spinner { width: 11px; height: 11px; border: 2px solid var(--accent-soft); border-top-color: var(--accent); border-radius: 50%;
    animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
