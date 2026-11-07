<script>
  import { onMount, tick, untrack } from "svelte";
  import SourceCard from "./SourceCard.svelte";
  import { addRecord, copy, copyCitation, makePackage, sendToZotero } from "./lib/actions.js";
  import Synthesis from "./lib/Synthesis.svelte";
  import Trace from "./lib/Trace.svelte";
  import { api, apiStream } from "./lib/api.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let { route, aiEnabled = false } = $props();
  let synthesis = $state(null); // réponse rédigée par l'IA : { text, sentences?, model?, streaming, error? }

  let question = $state("");
  let scope = $state("online");
  let recommendedOnly = $state(true);
  let yearFrom = $state("");
  let openAccess = $state(false);
  let editedQuery = $state("");
  let result = $state(null);
  let loading = $state(false);
  let origin = null; // question de départ quand on suit des questions liées (l'IA n'en dérive pas)
  let trace = $state(null); // déroulé de la dernière recherche : { steps, running, ms }
  let error = $state(null);
  let selected = $state(0);
  let highlight = $state(null); // { source, text }
  let adding = $state({}); // numéro de source → en cours
  let saved = $state([]); // recherches enregistrées
  let history = $state([]); // questions posées récemment
  let savedInfo = $state(null); // recherche enregistrée affichée
  let input;
  let runs = 0; // seule la dernière recherche lancée s'affiche

  const hiddenCount = $derived(Object.values(result?.hidden ?? {}).reduce((a, b) => a + b, 0));

  onMount(() => {
    refreshSaved();
    input?.focus();
  });

  // #/search/<id> : relire une recherche enregistrée ; #/q/<question> : poser cette question (depuis une fiche).
  $effect(() => {
    const id = route.match(/^\/search\/(\d+)/)?.[1];
    const asked = route.match(/^\/q\/(.+)/)?.[1];
    untrack(() => {
      if (id) openSaved(Number(id));
      if (asked) {
        question = decodeURIComponent(asked);
        location.replace("#/"); // la même question pourra être reposée depuis une autre fiche
        run();
      }
    });
  });

  async function refreshSaved() {
    [saved, history] = await Promise.all([api("/searches").catch(() => []), api("/history").catch(() => [])]);
  }

  function askAgain(h) {
    question = h.question;
    scope = h.scope;
    run();
  }

  async function forget(h) {
    await api("/history/forget", { method: "POST", body: h ? { question: h.question, scope: h.scope } : {} });
    refreshSaved();
  }

  async function run(query = null, related = false) {
    if (!question.trim()) return;
    if (!related) origin = null;
    const ticket = ++runs;
    loading = true;
    error = null;
    highlight = null;
    savedInfo = null;
    const started = performance.now();
    trace = { steps: [], running: true, ms: 0 };
    try {
      let out = null;
      const body = {
        question: question.trim(), scope, query, all_journals: !recommendedOnly,
        year_from: yearFrom ? Number(yearFrom) : null, open_access: openAccess, origin,
      };
      await apiStream("/ask/stream", body, (event) => {
        if (ticket !== runs) return;
        if (event.step) trace.steps.push(event.step);
        if (event.result) out = event.result;
        if (event.error) throw new Error(event.error);
      });
      if (ticket !== runs) return;
      if (!out) throw new Error($t("trace.interrupted"));
      result = out;
      editedQuery = result.query ?? "";
      for (const w of out.warnings ?? []) toast(w, "warn", 7000);
      synthesis = null;
      if (aiEnabled && out.sources.length) write(ticket);
      selected = 0;
      if (location.hash.startsWith("#/search/")) window.history.replaceState(null, "", "#/");
    } catch (e) {
      if (ticket === runs) error = e.message;
    } finally {
      if (ticket === runs) {
        loading = false;
        trace = { ...trace, running: false, ms: performance.now() - started };
      }
    }
  }

  // Réponse rédigée par l'IA, affichée au fil de l'eau puis vérifiée phrase par phrase.
  async function write(ticket) {
    synthesis = { text: "", streaming: true };
    try {
      await apiStream("/ask/answer", { question: question.trim(), sources: result.sources }, (event) => {
        if (ticket !== runs) return;
        if (event.delta) synthesis.text += event.delta;
        if (event.error) synthesis = { ...synthesis, error: event.error, streaming: false };
        if (event.done) synthesis = { text: event.text, sentences: event.sentences, model: event.model, streaming: false };
      });
    } catch (e) {
      if (ticket === runs) synthesis = { ...synthesis, error: e.message, streaming: false };
    }
  }

  async function openSaved(id) {
    try {
      const s = await api(`/searches/${id}`);
      question = s.question;
      scope = s.scope;
      editedQuery = s.query ?? "";
      result = { scope: s.scope, query: s.query, answer: s.answer, sources: s.sources, hidden: {}, total: s.sources.length };
      synthesis = s.synthesis;
      savedInfo = s;
      trace = null;
      selected = 0;
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function save() {
    const s = await api("/searches", {
      method: "POST",
      body: {
        question: question.trim(), scope: result.scope, query: result.query, answer: result.answer, sources: result.sources,
        synthesis: synthesis?.sentences ? { text: synthesis.text, sentences: synthesis.sentences, model: synthesis.model } : null,
      },
    });
    savedInfo = s;
    window.history.replaceState(null, "", `#/search/${s.id}`);
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
      result.sources[i] = { ...rec, in_library: await addRecord(rec, question.trim(), result.scope) };
    } catch (e) {
      toast(e.message, "error");
    } finally {
      adding[i] = false;
    }
  }

  // z : ajoute la source si besoin, puis l'envoie dans Zotero.
  async function toZotero(i) {
    if (!result.sources[i].in_library) await add(i);
    if (result.sources[i].in_library) sendToZotero({ works: [result.sources[i].in_library] });
  }

  // Veille : la recherche est enregistrée au besoin, puis relancée chaque semaine (nouveaux articles dans Veille).
  async function follow(on) {
    if (!savedInfo) await save();
    savedInfo = await api(`/searches/${savedInfo.id}/watch`, { method: "PUT", body: { days: on ? 7 : null } });
    toast(on ? $t("watch.followed") : $t("watch.unfollowed"), "ok", 6000);
    refreshSaved();
  }

  function sendPackage() {
    const works = result.sources.map((s) => s.in_library).filter(Boolean);
    makePackage({ works, searches: [savedInfo.id], title: question.trim() }, question.trim().slice(0, 60));
  }

  // Prompt à coller dans Claude Desktop : il reprend les sources par leur identifiant (outils MCP de Biblio).
  function continueInClaude() {
    const ids = result.sources.slice(0, 10).map((s, i) => {
      const id = s.identifiers?.doi ? `doi:${s.identifiers.doi}` : s.identifiers?.pmid ? `pmid:${s.identifiers.pmid}` : s.citekey ?? s.title;
      return `[${i + 1}] ${id}`;
    });
    copy($t("ask.claude_prompt", { question: question.trim(), query: result.query ?? "", sources: ids.join("\n") }));
  }

  async function addAll() {
    for (let i = 0; i < result.sources.length; i++) await add(i);
  }

  async function citeNumber(n) {
    selected = n - 1;
    highlight = null;
    await tick();
    document.getElementById(`source-${n}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
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
      else if (e.key === "c") copyCitation(result.sources[selected].in_library ?? result.sources[selected]);
      else if (e.key === "z") toZotero(selected);
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
          {loading ? $t(aiEnabled && scope === "online" ? "ai.planning" : "ask.searching") : $t("ask.submit")} <span class="kbd">⏎</span>
        </button>
      </div>
    </form>

    {#if trace && (trace.running || trace.steps.length)}
      <Trace {trace} />
    {/if}

    {#if error}
      <p class="error">{error}</p>
    {/if}

    {#if result}
      {#if result.query}
        <details class="query">
          <summary class="small muted">{$t(result.query_by === "ai" ? "ask.query_ai" : "ask.query")}</summary>
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

      {#if synthesis}
        <div class="answer">
          <div class="row">
            <span class="eyebrow grow">{$t("ai.answer")}</span>
            {#if synthesis.model}<span class="small faint">{synthesis.model}</span>{/if}
          </div>
          {#if synthesis.error}
            <p class="error small">{synthesis.error}</p>
          {:else if !synthesis.text}
            <p class="muted small">{$t("ai.writing")}</p>
          {:else}
            <Synthesis {synthesis} oncite={citeNumber} />
          {/if}
          {#if synthesis.sentences}
            {@const flagged = synthesis.sentences.filter((x) => !["ok", "pending"].includes(x.status)).length}
            <p class="small faint">
              {flagged ? `⚠ ${$t("ai.flagged", { n: flagged })}` : `✓ ${$t("ai.all_checked")}`} · {$t("ai.disclaimer")}
            </p>
          {/if}
        </div>
      {/if}

      {#if result.related?.length}
        <div class="row wrap related">
          <span class="small muted">{$t("ai.related")}</span>
          {#each result.related as q}
            <button class="chip" onclick={() => { origin ??= question.trim(); question = q; run(null, true); }}>{q}</button>
          {/each}
        </div>
      {/if}

      <details class="answer" open={!synthesis}>
        <summary class="eyebrow">{$t(synthesis ? "ai.extracts" : "ask.answer")}</summary>
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
      </details>

      <div class="row wrap">
        <button onclick={save} disabled={!!savedInfo || !result.sources.length}>{savedInfo ? `✓ ${$t("ask.saved_short")}` : $t("ask.save")}</button>
        {#if result.scope === "online" && (!savedInfo || savedInfo.scope === "online")}
          <button class:primary={!savedInfo?.watch_days} disabled={!result.sources.length && !savedInfo}
            title={$t("watch.follow_help")} onclick={() => follow(!savedInfo?.watch_days)}>
            {savedInfo?.watch_days ? `🔔 ${$t("watch.following")}` : `🔔 ${$t("watch.follow_button")}`}
          </button>
        {/if}
        {#if savedInfo}
          <button onclick={sendPackage} title={$t("package.search_help")}>{$t("package.send_search")}</button>
        {/if}
        <button onclick={continueInClaude} disabled={!result.sources.length} title={$t("ask.continue_help")}>{$t("ask.continue_claude")}</button>
      </div>
    {:else}
      <div class="intro">
        {#if history.length}
          <div class="row">
            <span class="eyebrow grow">{$t("ask.history")}</span>
            <button class="ghost small" onclick={() => forget(null)}>{$t("ask.clear_history")}</button>
          </div>
          <ul class="saved">
            {#each history as h (h.question + h.scope)}
              <li class="row">
                <button class="link grow" onclick={() => askAgain(h)}>{h.question}</button>
                <span class="small faint">{h.scope === "library" ? $t("ask.scope_library") + " · " : ""}{$fmt.date(h.asked_at)}</span>
                <button class="ghost small" title={$t("ask.forget")} onclick={() => forget(h)}>✕</button>
              </li>
            {/each}
          </ul>
        {/if}
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
        {/if}
        <p class="small faint keys">
          <span class="kbd">/</span> {$t("keys.question")} · <span class="kbd">j</span> <span class="kbd">k</span> {$t("keys.sources")} ·
          <span class="kbd">a</span> {$t("keys.add")} · <span class="kbd">c</span> {$t("keys.cite")} ·
          <span class="kbd">z</span> Zotero
        </p>
      </div>
    {/if}
  </section>

  {#if result}
    <aside class="sources">
      <div class="sources-head">
        <div class="eyebrow">{$t("ask.sources", { n: result.sources.length })}</div>
        {#if result.off_topic}<span class="small faint" title={$t("ask.off_topic_help")}>{$t("ask.off_topic", { n: result.off_topic })}</span>{/if}
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
            {aiEnabled}
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
  .answer > summary { cursor: pointer; }
  .related { gap: 6px; }
  .related .chip { border: none; cursor: pointer; text-align: left; white-space: normal; }
  .related .chip:hover { background: var(--accent-soft); color: var(--accent); }
  .cite {
    font: 650 11px var(--font); padding: 0 6px; border-radius: 5px; vertical-align: 2px; margin-left: 3px;
    background: var(--accent-soft); color: var(--accent); border: none;
  }
  .cite:hover { background: var(--accent); color: var(--accent-text); }
  .error { color: var(--danger); }
  .intro { display: grid; gap: 10px; }
  .saved { list-style: none; padding: 0; margin: 0; }
  .saved li { padding: 7px 0; border-bottom: 1px solid var(--border); }
  .link { border: none; background: none; padding: 0; color: var(--accent); text-align: left; white-space: normal; justify-content: flex-start; }
  .link:hover { text-decoration: underline; }
  .intro .eyebrow { margin-top: 8px; }

  .sources { border-left: 1px solid var(--border); background: var(--surface); display: flex; flex-direction: column; min-height: 0; }
  .sources-head, .sources-foot { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; flex: none; }
  .sources-head { border-bottom: 1px solid var(--border); }
  .sources-foot { border-top: 1px solid var(--border); }
  .list { flex: 1; }
  .pad { padding: 16px; }
</style>
