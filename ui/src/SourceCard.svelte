<script>
  import Badges from "./lib/Badges.svelte";
  import { authorsShort, doiUrl, sourceLine, splitAround } from "./lib/format.js";
  import { t } from "./lib/i18n.js";

  let { rec, n, selected = false, highlight = null, adding = false, onadd, onselect } = $props();
  let open = $state(false); // résumé complet
  let showSummary = $state(true);
  let el;

  const cited = $derived(highlight ? splitAround(rec.abstract, highlight) : null);

  $effect(() => {
    if (highlight) open = true;
  });
  $effect(() => {
    if (selected) el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<article bind:this={el} class:selected class:flash={!!highlight} id="source-{n}" onclick={onselect}>
  <div class="head">
    <span class="num">{n}</span>
    <div class="grow">
      <h3 class="title-serif">
        {#if doiUrl(rec)}<a href={doiUrl(rec)} target="_blank" rel="noreferrer">{rec.title}</a>{:else}{rec.title}{/if}
      </h3>
      <div class="meta small muted">{authorsShort(rec)}{#if sourceLine(rec)} — <em>{sourceLine(rec)}</em>{/if}</div>
    </div>
  </div>
  <div class="badges"><Badges {rec} /></div>

  {#if rec.passages?.length}
    <ul class="passages">
      {#each rec.passages as p}
        <li>
          <span class="where small faint">{$t(`origin.${p.origin}`, {}, p.origin)}{p.page ? ` · p. ${p.page}` : ""}{p.match === "semantic" ? " · ≈" : ""}</span>
          <!-- Extraits échappés par le serveur ; seuls les <mark> sont du HTML. -->
          <span class="snippet">{@html p.snippet}</span>
        </li>
      {/each}
    </ul>
  {/if}

  {#if rec.summary}
    <button class="ghost small toggle" onclick={(e) => { e.stopPropagation(); showSummary = !showSummary; }}>
      {$t("source.summary")} {showSummary ? "▾" : "▸"}
    </button>
    {#if showSummary}<p class="summary">{rec.summary}</p>{/if}
  {/if}

  {#if rec.abstract}
    <button class="ghost small toggle" onclick={(e) => { e.stopPropagation(); open = !open; }}>
      {$t("source.abstract")} {open ? "▾" : "▸"}
    </button>
    {#if open}
      <p class="abstract">
        {#if cited}{cited[0]}<mark>{cited[1]}</mark>{cited[2]}{:else}{rec.abstract}{/if}
      </p>
    {/if}
  {/if}

  <div class="actions row">
    {#if rec.in_library}
      <a class="chip lib" href="#/library/{rec.in_library}">✓ {$t("source.open_in_library")}</a>
    {:else}
      <button class="small" disabled={adding} onclick={(e) => { e.stopPropagation(); onadd(); }}>
        {adding ? $t("source.adding") : `＋ ${$t("source.add")}`}
      </button>
    {/if}
    {#if rec.oa_pdf_url}
      <a class="small" href={rec.oa_pdf_url} target="_blank" rel="noreferrer">{$t("source.free_pdf")} ↗</a>
    {/if}
    {#if rec.identifiers?.pmid}
      <a class="small" href="https://pubmed.ncbi.nlm.nih.gov/{rec.identifiers.pmid}/" target="_blank" rel="noreferrer">PubMed ↗</a>
    {/if}
    {#if rec.cited_by_count}<span class="small faint">{$t("source.cited_by", { n: rec.cited_by_count })}</span>{/if}
  </div>
</article>

<style>
  article {
    padding: 14px 16px; border-bottom: 1px solid var(--border); cursor: default;
    border-left: 3px solid transparent; transition: background 0.2s;
  }
  article.selected { border-left-color: var(--accent); background: color-mix(in srgb, var(--accent-soft) 45%, transparent); }
  article.flash { animation: flash 1.2s ease-out; }
  @keyframes flash { from { background: var(--mark); } }
  .head { display: flex; gap: 10px; }
  .num {
    flex: none; width: 22px; height: 22px; border-radius: 6px; display: grid; place-items: center;
    background: var(--surface-2); font-size: 12px; font-weight: 650; color: var(--muted); margin-top: 2px;
  }
  h3 { margin: 0; font-size: 15.5px; line-height: 1.35; font-weight: 600; }
  h3 a { color: var(--text); }
  .meta { margin-top: 2px; }
  .badges { margin: 8px 0 2px 32px; }
  .toggle { margin: 6px 0 0 24px; }
  .summary, .abstract, .passages { margin: 4px 0 0 32px; }
  .summary { font-size: 13.5px; }
  .abstract { font-size: 13px; color: var(--muted); white-space: pre-line; }
  .passages { padding: 0; list-style: none; display: grid; gap: 6px; }
  .passages li { font-size: 13px; background: var(--surface-2); border-radius: 7px; padding: 6px 9px; }
  .where { display: block; }
  .actions { margin: 10px 0 0 32px; flex-wrap: wrap; }
  .lib { background: var(--accent-soft); color: var(--accent); }
</style>
