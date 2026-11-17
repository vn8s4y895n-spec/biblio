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
  let historyInfo = $state(null); // résultat gardé d'une question de l'historique, affiché sans relancer
  let thread = $state([]); // échanges précédents quand on approfondit : { question, result, synthesis }
  let followUp = $state("");
  let report = $state(null); // signalement en cours : { sources, answer, bad: { n: true }, comment, saved }
  let input;
  let runs = 0; // seule la dernière recherche lancée s'affiche

  // Échange précédent envoyé au serveur : la nouvelle recherche et la réponse tiennent compte de tout le fil.
  const context = $derived(thread.map((x) => ({
    question: x.question,
    answer: x.synthesis?.text ?? x.result.answer.map((s) => s.text).join(" "),
  })));
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

  // Question de l'historique : son dernier résultat s'affiche tel quel ; « Relancer » refait la recherche.
  async function askAgain(h) {
    question = h.question;
    scope = h.scope;
    thread = [];
    if (!h.has_result) return run();
    try {
      const kept = await api("/history/result", { params: { question: h.question, scope: h.scope } });
      result = kept.result;
      editedQuery = result.query ?? "";
      synthesis = kept.synthesis;
      savedInfo = null;
      historyInfo = { asked_at: kept.asked_at };
      trace = null;
      error = null;
      selected = 0;
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function forget(h) {
    await api("/history/forget", { method: "POST", body: h ? { question: h.question, scope: h.scope } : {} });
    refreshSaved();
  }

  async function run(query = null, related = false, followingUp = false) {
    if (!question.trim()) return;
    if (!followingUp) thread = [];
    if (!related && !followingUp) origin = null;
    const ticket = ++runs;
    loading = true;
    error = null;
    highlight = null;
    savedInfo = null;
    historyInfo = null;
    report = null;
    const started = performance.now();
    trace = { steps: [], running: true, ms: 0 };
    try {
      let out = null;
      const body = {
        question: question.trim(), scope, query, all_journals: !recommendedOnly,
        year_from: yearFrom ? Number(yearFrom) : null, open_access: openAccess, origin, context,
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
      await apiStream("/ask/answer", { question: question.trim(), sources: result.sources, context }, (event) => {
        if (ticket !== runs) return;
        if (event.delta) synthesis.text += event.delta;
        if (event.error) synthesis = { ...synthesis, error: event.error, streaming: false };
        if (event.done) {
          synthesis = { text: event.text, sentences: event.sentences, model: event.model, streaming: false };
          api("/history/synthesis", { method: "PUT", body: { question: question.trim(), scope: result.scope, synthesis } }).catch(() => {});
        }
      });
    } catch (e) {
      if (ticket === runs) synthesis = { ...synthesis, error: e.message, streaming: false };
    }
  }

  // Approfondir : l'échange affiché rejoint le fil, la question de suivi lance une recherche avec tout le contexte.
  function askFollowUp(next = followUp) {
    if (!next.trim() || loading) return;
    thread = [...thread, { question: question.trim(), result, synthesis }];
    origin ??= thread[0].question;
    question = next.trim();
    followUp = "";
    run(null, true, true);
  }

  async function sendReport() {
    if (report.sending) return;
    report.sending = true;
    const reasons = ["sources", "answer"].filter((r) => report[r]);
    try {
      report.saved = await api("/reports", {
        method: "POST",
        body: {
          question: question.trim(), reasons, comment: report.comment.trim(),
          bad_sources: Object.keys(report.bad).filter((n) => report.bad[n]).map(Number),
          scope: result.scope, query: result.query, query_by: result.query_by, sources: result.sources,
          synthesis: synthesis?.text ? { text: synthesis.text, model: synthesis.model } : null, context,
        },
      });
    } catch (e) {
      toast(e.message, "error");
    } finally {
      if (report) report.sending = false;
    }
  }

  async function openSaved(id) {
    try {
      const s = await api(`/searches/${id}`);
      thread = [];
      question = s.question;
      scope = s.scope;
      editedQuery = s.query ?? "";
      result = { scope: s.scope, query: s.query, answer: s.answer, sources: s.sources, hidden: {}, total: s.sources.length };
      synthesis = s.synthesis;
      savedInfo = s;
      historyInfo = null;
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

      {#if historyInfo}
        <p class="saved-banner small">
          {$t("ask.history_on", { date: $fmt.date(historyInfo.asked_at) })}
          <button class="ghost small" onclick={() => run()}>{$t("ask.rerun")}</button>
        </p>
      {/if}
      {#if savedInfo}
        <p class="saved-banner small">
          {$t("ask.saved_on", { date: $fmt.date(savedInfo.created_at) })}
          {#if savedInfo.origin !== "biblio"} · {$t("ask.origin", { origin: savedInfo.origin })}{/if}
          <button class="ghost small" onclick={() => run()}>{$t("ask.refresh")}</button>
        </p>
      {/if}

      {#each thread as turn}
        <div class="turn">
          <p class="turn-question">{turn.question}</p>
          {#if turn.synthesis?.text}<Synthesis synthesis={turn.synthesis} oncite={() => {}} />{/if}
          <details>
            <summary class="small muted">{$t("ask.turn_sources", { n: turn.result.sources.length })}</summary>
            <ol class="small">
              {#each turn.result.sources as rec}<li>{rec.title}{rec.year ? ` (${rec.year})` : ""}</li>{/each}
            </ol>
          </details>
        </div>
      {/each}
      {#if thread.length}<p class="turn-question">{question}</p>{/if}

      {#if result.off_topic && result.concepts?.length}
        <p class="off-topic small">
          ⚠ {$t("ask.off_topic_concepts", { n: result.off_topic, concepts: result.concepts.join(", ") })}
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

      {#if aiEnabled && !synthesis?.streaming}
        <form class="follow-up card" onsubmit={(e) => { e.preventDefault(); askFollowUp(); }}>
          <span class="eyebrow">{$t("ask.follow_up")}</span>
          {#if result.related?.length}
            <div class="row wrap related">
              {#each result.related as q}
                <button type="button" class="chip" disabled={loading} onclick={() => askFollowUp(q)}>{q}</button>
              {/each}
            </div>
          {/if}
          <textarea rows="2" bind:value={followUp} placeholder={$t("ask.follow_up_placeholder")}
            onkeydown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askFollowUp(); } }}></textarea>
          <div class="row"><div class="grow"></div>
            <button class="primary" type="submit" disabled={loading || !followUp.trim()}>{$t("ask.follow_up_submit")} <span class="kbd">⏎</span></button>
          </div>
        </form>
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
        <button class="ghost" title={$t("report.help")} onclick={() => (report = report ? null : { sources: false, answer: false, bad: {}, comment: "", saved: null })}>
          ⚑ {$t("report.button")}
        </button>
      </div>

      {#if report}
        <div class="report card">
          {#if report.saved?.sent}
            <p>✓ {$t("report.sent")}</p>
            <div class="row"><button class="ghost" onclick={() => (report = null)}>{$t("report.close")}</button></div>
          {:else if report.saved}
            <p>{$t("report.not_sent")}</p>
            <p class="small muted">{$t("report.send_help")}</p>
            <div class="row">
              <a class="send" href={report.saved.issue_url} target="_blank" rel="noreferrer">{$t("report.send")} ↗</a>
              <button class="ghost" onclick={() => (report = null)}>{$t("report.close")}</button>
            </div>
          {:else}
            <span class="eyebrow">{$t("report.title")}</span>
            <label class="row small"><input type="checkbox" bind:checked={report.sources} /> {$t("report.reason_sources")}</label>
            {#if report.sources && result.sources.length}
              <div class="bad small">
                <span class="muted">{$t("report.which")}</span>
                {#each result.sources as rec, i}
                  <label class="row"><input type="checkbox" bind:checked={report.bad[i + 1]} /> {i + 1}. {rec.title}</label>
                {/each}
              </div>
            {/if}
            {#if synthesis?.text}
              <label class="row small"><input type="checkbox" bind:checked={report.answer} /> {$t("report.reason_answer")}</label>
            {/if}
            <textarea rows="3" bind:value={report.comment} placeholder={$t("report.comment")}></textarea>
            <div class="row">
              <div class="grow"></div>
              <button class="ghost" onclick={() => (report = null)}>{$t("common.cancel")}</button>
              <button class="primary" disabled={report.sending || (!report.sources && !report.answer && !report.comment.trim())} onclick={sendReport}>{$t("report.submit")}</button>
            </div>
          {/if}
        </div>
      {/if}
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
  .turn { border-left: 2px solid var(--border); padding-left: 14px; opacity: 0.85; }
  .turn ol { margin: 6px 0 0; padding-left: 20px; }
  .turn-question { font: 600 16px var(--serif); margin: 0; }
  .off-topic { background: var(--warn-soft); color: var(--warn); padding: 6px 12px; border-radius: 8px; margin: 0; }
  .report { padding: 12px 14px; display: grid; gap: 8px; }
  .report p { margin: 0; }
  .report .send { background: var(--accent); color: var(--accent-text); border-radius: 7px; padding: 5px 11px; font-weight: 550; text-decoration: none; }
  .report textarea { width: 100%; }
  .report .bad { display: grid; gap: 4px; max-height: 220px; overflow: auto; padding-left: 22px; }
  .report .bad label { align-items: flex-start; }
  .follow-up { padding: 10px 14px; display: grid; gap: 6px; }
  .follow-up textarea { width: 100%; border: none; background: transparent; resize: none; font: 15px/1.45 var(--serif); padding: 0; }
  .follow-up textarea:focus-visible { outline: none; }
  .follow-up:focus-within { border-color: var(--accent); }
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
