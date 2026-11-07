<script>
  import { onMount, tick } from "svelte";
  import SourceCard from "./SourceCard.svelte";
  import { api, identifierOf } from "./lib/api.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let { route } = $props();

  let question = $state("");
  let scope = $state("online");
  let recommendedOnly = $state(true);
  let yearFrom = $state("");
  let openAccess = $state(false);
  let editedQuery = $state("");
  let result = $state(null);
  let loading = $state(false);
  let error = $state(null);
  let selected = $state(0);
  let highlight = $state(null); // { source, text }
  let adding = $state({}); // numéro de source → en cours
  let saved = $state([]); // recherches enregistrées
  let savedInfo = $state(null); // recherche enregistrée affichée
  let input;
  let runs = 0; // seule la dernière recherche lancée s'affiche

  const hiddenCount = $derived(Object.values(result?.hidden ?? {}).reduce((a, b) => a + b, 0));

  onMount(() => {
    refreshSaved();
    input?.focus();
  });

  // #/search/<id> : relire une recherche enregistrée.
  $effect(() => {
    const id = route.match(/^\/search\/(\d+)/)?.[1];
    if (id) openSaved(Number(id));
  });

  async function refreshSaved() {
    saved = await api("/searches").catch(() => []);
  }

  async function run(query = null) {
    if (!question.trim()) return;
    const ticket = ++runs;
    loading = true;
    error = null;
    highlight = null;
    savedInfo = null;
    try {
      const out = await api("/ask", {
        method: "POST",
        body: {
          question: question.trim(), scope, query, all_journals: !recommendedOnly,
          year_from: yearFrom ? Number(yearFrom) : null, open_access: openAccess,
        },
      });
      if (ticket !== runs) return;
      result = out;
      editedQuery = result.query ?? "";
      selected = 0;
      if (location.hash.startsWith("#/search/")) history.replaceState(null, "", "#/");
    } catch (e) {
      if (ticket === runs) error = e.message;
    } finally {
      if (ticket === runs) loading = false;
    }
  }

  async function openSaved(id) {
    try {
      const s = await api(`/searches/${id}`);
      question = s.question;
      scope = s.scope;
      editedQuery = s.query ?? "";
      result = { scope: s.scope, query: s.query, answer: s.answer, sources: s.sources, hidden: {}, total: s.sources.length };
      savedInfo = s;
      selected = 0;
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function save() {
    const s = await api("/searches", {
      method: "POST",
      body: { question: question.trim(), scope: result.scope, query: result.query, answer: result.answer, sources: result.sources },
    });
    savedInfo = s;
    history.replaceState(null, "", `#/search/${s.id}`);
    toast($t("ask.saved"), "ok");
    refreshSaved();
  }

  async function removeSaved(id) {
    await api(`/searches/${id}`, { method: "DELETE" });
    if (savedInfo?.id === id) savedInfo = null;
    refreshSaved();
  }

  async function add(i) {
    const rec = result.sources[i];
    if (rec.in_library || adding[i]) return;
    adding[i] = true;
    try {
      const id = identifierOf(rec);
      const out = id
        ? await api("/works", { method: "POST", body: { identifier: id } })
        : await api("/works/manual", {
            method: "POST",
            body: {
              title: rec.title, year: rec.year, container_title: rec.container_title, abstract: rec.abstract,
              authors: (rec.authors ?? []).map((a) => [a.family, a.given].filter(Boolean).join(", ")),
            },
          });
      result.sources[i] = { ...rec, in_library: out.work.citekey };
      const warn = out.warnings?.length ? ` (${out.warnings.join(" ; ")})` : "";
      toast(`${$t("ask.added")} ${out.work.citekey}${warn}`, warn ? "warn" : "ok");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      adding[i] = false;
    }
  }

  async function addAll() {
    for (let i = 0; i < result.sources.length; i++) await add(i);
  }

  async function cite(sentence) {
    selected = sentence.source - 1;
    highlight = { source: sentence.source, text: sentence.text };
    await tick();
    document.getElementById(`source-${sentence.source}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function onKey(e) {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
    if (e.key === "/" && !typing) {
      e.preventDefault();
      input?.focus();
      input?.select();
    } else if (e.key === "Escape" && typing) {
      e.target.blur();
    } else if (!typing && result?.sources?.length && !e.metaKey && !e.ctrlKey) {
      if (e.key === "j") selected = Math.min(selected + 1, result.sources.length - 1);
      else if (e.key === "k") selected = Math.max(selected - 1, 0);
      else if (e.key === "a") add(selected);
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="ask" class:empty={!result} class:stale={loading}>
  <section class="main">
    <form class="question card" onsubmit={(e) => { e.preventDefault(); run(); }}>
      <textarea
        bind:this={input}
        bind:value={question}
        rows={result ? 2 : 3}
        placeholder={$t("ask.placeholder")}
        onkeydown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(); } }}
      ></textarea>
      <div class="controls row wrap">
        <div class="segmented" role="radiogroup" aria-label={$t("ask.scope")}>
          <button type="button" class:on={scope === "online"} onclick={() => (scope = "online")}>{$t("ask.scope_online")}</button>
          <button type="button" class:on={scope === "library"} onclick={() => (scope = "library")}>{$t("ask.scope_library")}</button>
        </div>
        {#if scope === "online"}
          <label class="row small"><input type="checkbox" bind:checked={recommendedOnly} /> {$t("ask.recommended_only")}</label>
          <label class="row small"><input type="checkbox" bind:checked={openAccess} /> {$t("ask.open_access")}</label>
          <label class="row small">{$t("ask.since")} <input class="year" type="number" min="1900" max="2100" bind:value={yearFrom} placeholder="—" /></label>
        {/if}
        <div class="grow"></div>
        <button class="primary" type="submit" disabled={loading || !question.trim()}>
          {loading ? $t("ask.searching") : $t("ask.submit")} <span class="kbd">⏎</span>
        </button>
      </div>
    </form>

    {#if error}
      <p class="error">{error}</p>
    {/if}

    {#if result}
      {#if result.query}
        <details class="query">
          <summary class="small muted">{$t("ask.query")}</summary>
          <form class="row" onsubmit={(e) => { e.preventDefault(); run(editedQuery); }}>
            <input class="grow mono" type="text" bind:value={editedQuery} />
            <button type="submit" disabled={loading}>{$t("ask.rerun")}</button>
          </form>
        </details>
      {/if}

      {#if savedInfo}
        <p class="saved-banner small">
          {$t("ask.saved_on", { date: $fmt.date(savedInfo.created_at) })}
          {#if savedInfo.origin !== "biblio"} · {$t("ask.origin", { origin: savedInfo.origin })}{/if}
          <button class="ghost small" onclick={() => run()}>{$t("ask.refresh")}</button>
        </p>
      {/if}

      <div class="answer">
        <div class="eyebrow">{$t("ask.answer")}</div>
        {#if result.answer.length}
          <ul>
            {#each result.answer as sentence}
              <li>
                <span class="sentence">{sentence.text}</span>
                <button class="cite" onclick={() => cite(sentence)} title={result.sources[sentence.source - 1]?.title}>
                  {sentence.source}
                </button>
                {#if sentence.page}<span class="small faint">p. {sentence.page}</span>{/if}
              </li>
            {/each}
          </ul>
          <p class="small faint">{$t(result.scope === "library" ? "ask.extracted_library" : "ask.extracted")}</p>
        {:else}
          <p class="muted">{$t("ask.no_answer")}</p>
        {/if}
      </div>

      <div class="row wrap">
        <button onclick={save} disabled={!!savedInfo || !result.sources.length}>{savedInfo ? `✓ ${$t("ask.saved_short")}` : $t("ask.save")}</button>
      </div>
    {:else}
      <div class="intro">
        {#if saved.length}
          <div class="eyebrow">{$t("ask.saved_searches")}</div>
          <ul class="saved">
            {#each saved as s}
              <li class="row">
                <a class="grow" href="#/search/{s.id}">{s.question}</a>
                <span class="small faint">{$t("ask.sources_count", { n: s.count })} · {$fmt.date(s.created_at)}</span>
                <button class="ghost small" title={$t("common.delete")} onclick={() => removeSaved(s.id)}>✕</button>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="muted tips">{$t("ask.tips")}</p>
        {/if}
        <p class="small faint keys">
          <span class="kbd">/</span> {$t("keys.question")} · <span class="kbd">j</span> <span class="kbd">k</span> {$t("keys.sources")} ·
          <span class="kbd">a</span> {$t("keys.add")}
        </p>
      </div>
    {/if}
  </section>

  {#if result}
    <aside class="sources">
      <div class="sources-head">
        <div class="eyebrow">{$t("ask.sources", { n: result.sources.length })}</div>
        {#if hiddenCount}
          <button class="ghost small" onclick={() => { recommendedOnly = false; run(editedQuery || null); }}>
            {$t("ask.hidden", { n: hiddenCount })}
          </button>
        {/if}
      </div>
      <div class="list scroll">
        {#each result.sources as rec, i (i)}
          <SourceCard
            {rec}
            n={i + 1}
            selected={selected === i}
            highlight={highlight?.source === i + 1 ? highlight.text : null}
            adding={adding[i]}
            onadd={() => add(i)}
            onselect={() => (selected = i)}
          />
        {:else}
          <p class="muted pad">{$t("ask.no_sources")}</p>
        {/each}
      </div>
      {#if result.sources.some((s) => !s.in_library)}
        <div class="sources-foot">
          <button onclick={addAll}>＋ {$t("ask.add_all")}</button>
        </div>
      {/if}
    </aside>
  {/if}
</div>

<style>
  .ask { display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, 42%); height: 100%; }
  .ask.empty { grid-template-columns: 1fr; }
  .stale .answer, .stale .sources .list { opacity: 0.45; transition: opacity 0.2s; }
  .main { overflow: auto; padding: 28px clamp(18px, 4vw, 56px); display: flex; flex-direction: column; gap: 18px; }
  .empty .main { max-width: 760px; width: 100%; margin: 0 auto; padding-top: 14vh; }
  .question { padding: 12px 14px 10px; }
  .question textarea {
    width: 100%; border: none; background: transparent; padding: 2px 0; resize: none;
    font-size: 17px; line-height: 1.45; font-family: var(--serif);
  }
  .question textarea:focus-visible { outline: none; }
  .question:focus-within { border-color: var(--accent); }
  .controls { margin-top: 8px; gap: 14px; }
  .year { width: 72px; padding: 2px 6px; }
  .segmented { display: inline-flex; background: var(--surface-2); border-radius: 8px; padding: 2px; }
  .segmented button { border: none; background: transparent; padding: 3px 10px; border-radius: 6px; color: var(--muted); }
  .segmented button.on { background: var(--surface); color: var(--text); box-shadow: var(--shadow); font-weight: 550; }

  .query summary { cursor: pointer; }
  .query form { margin-top: 6px; }
  .mono { font: 12.5px var(--mono); }
  .saved-banner { background: var(--accent-soft); color: var(--accent); padding: 6px 12px; border-radius: 8px; margin: 0; }
  .answer ul { margin: 8px 0 6px; padding: 0; list-style: none; display: grid; gap: 12px; }
  .answer li { font-family: var(--serif); font-size: 16px; line-height: 1.55; padding-left: 14px; border-left: 2px solid var(--border); }
  .cite {
    font: 650 11px var(--font); padding: 0 6px; border-radius: 5px; vertical-align: 2px; margin-left: 3px;
    background: var(--accent-soft); color: var(--accent); border: none;
  }
  .cite:hover { background: var(--accent); color: var(--accent-text); }
  .error { color: var(--danger); }
  .intro { display: grid; gap: 10px; }
  .saved { list-style: none; padding: 0; margin: 0; }
  .saved li { padding: 7px 0; border-bottom: 1px solid var(--border); }
  .tips { font-size: 15px; }

  .sources { border-left: 1px solid var(--border); background: var(--surface); display: flex; flex-direction: column; min-height: 0; }
  .sources-head, .sources-foot { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; flex: none; }
  .sources-head { border-bottom: 1px solid var(--border); }
  .sources-foot { border-top: 1px solid var(--border); }
  .list { flex: 1; }
  .pad { padding: 16px; }
</style>
