<script>
  import { onDestroy, onMount } from "svelte";
  import Badges from "./lib/Badges.svelte";
  import { api } from "./lib/api.js";
  import { doiUrl } from "./lib/format.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let { ref, labels, onlabels, onchange } = $props();

  let work = $state(null);
  let tab = $state("record");
  let files = $state([]);
  let pdfUrl = $state(null); // URL locale (blob) du PDF : le lecteur du système l'affiche
  let note = $state(null);
  let noteText = $state("");
  let noteSaved = $state(true);
  let fetching = $state(false);
  let tagInput = $state("");

  const collectionsOf = $derived(new Set(work?.labels.filter((l) => l.kind === "collection").map((l) => l.id)));
  const tagsOf = $derived(work?.labels.filter((l) => l.kind === "tag") ?? []);
  const allTags = $derived(labels.filter((l) => l.kind === "tag"));

  onMount(async () => {
    try {
      work = await api(`/works/${encodeURIComponent(ref)}`);
      [files, note] = await Promise.all([api(`/works/${work.id}/files`), api(`/works/${work.id}/note`)]);
      noteText = note.text;
    } catch (e) {
      toast(e.message, "error");
    }
  });

  onDestroy(() => {
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    if (!noteSaved) saveNote();
  });

  async function showPdf() {
    tab = "pdf";
    if (pdfUrl || !files.length) return;
    const blob = await api(`/files/${files[0].sha256}`);
    pdfUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
  }

  async function fetchPdf() {
    fetching = true;
    try {
      await api(`/works/${work.id}/pdf`, { method: "POST" });
      files = await api(`/works/${work.id}/files`);
      toast($t("detail.pdf_found"), "ok");
      onchange();
      showPdf();
    } catch (e) {
      toast(e.status === 404 ? $t("detail.pdf_none") : e.message, "warn", 7000);
    } finally {
      fetching = false;
    }
  }

  async function update(changes) {
    work = { ...(await api(`/works/${work.id}`, { method: "PATCH", body: changes })), labels: work.labels };
    onchange();
  }

  async function setLabel(id, on) {
    await api(`/labels/${id}/works`, { method: "POST", body: { works: [String(work.id)], remove: !on } });
    work = await api(`/works/${work.id}`);
    onlabels();
  }

  async function addTag(e) {
    e.preventDefault();
    const name = tagInput.trim().replace(/^#/, "");
    if (!name) return;
    const label = await api("/labels", { method: "POST", body: { kind: "tag", name } });
    tagInput = "";
    await setLabel(label.id, true);
  }

  async function saveNote() {
    note = await api(`/works/${work.id}/note`, { method: "PUT", body: { text: noteText } });
    noteSaved = true;
  }

  function copy(text) {
    navigator.clipboard.writeText(text).then(() => toast($t("detail.copied"), "ok"));
  }

  const authors = $derived(work?.authors.map((a) => [a.given, a.family].filter(Boolean).join(" ")).join(", "));
</script>

{#if work}
  <div class="detail">
    <header>
      <h2 class="title-serif">{work.title}</h2>
      <p class="muted small authors">{authors}</p>
      <p class="small">
        <em>{work.container_title ?? ""}</em>
        {[work.year, work.volume && `${work.volume}${work.issue ? `(${work.issue})` : ""}`, work.pages].filter(Boolean).join(" · ")}
      </p>
      <div class="row wrap">
        <Badges rec={{ ...work, has_pdf: files.length > 0 }} />
      </div>
      <div class="ids row wrap small">
        {#each Object.entries(work.identifiers) as [scheme, value]}
          <button class="chip id" title={$t("detail.copy")} onclick={() => copy(value)}>{scheme.toUpperCase()} {value}</button>
        {/each}
        {#if doiUrl(work)}<a href={doiUrl(work)} target="_blank" rel="noreferrer">{$t("detail.publisher")} ↗</a>{/if}
        <button class="chip id" title={$t("detail.copy")} onclick={() => copy(work.citekey)}>@{work.citekey}</button>
      </div>
    </header>

    <div class="tabs row">
      <button class:on={tab === "record"} onclick={() => (tab = "record")}>{$t("detail.record")}</button>
      <button class:on={tab === "pdf"} onclick={showPdf}>PDF{files.length ? "" : " —"}</button>
      <button class:on={tab === "note"} onclick={() => (tab = "note")}>{$t("detail.note")}{note?.text ? " ●" : ""}</button>
    </div>

    <div class="pane scroll" class:flush={tab === "pdf" && pdfUrl}>
      {#if tab === "record"}
        <div class="block row wrap">
          <div class="segmented" role="radiogroup" aria-label={$t("detail.read_status")}>
            {#each ["to_read", "read", "to_reread"] as s}
              <button class:on={work.read_status === s} onclick={() => update({ read_status: work.read_status === s ? null : s })}>{$t(`read.${s}`)}</button>
            {/each}
          </div>
          <div class="stars" role="radiogroup" aria-label={$t("detail.rating")}>
            {#each [1, 2, 3, 4, 5] as n}
              <button class="ghost star" class:on={(work.rating ?? 0) >= n} aria-label="{n}/5"
                onclick={() => update({ rating: work.rating === n ? null : n })}>★</button>
            {/each}
          </div>
        </div>

        <div class="block">
          <div class="eyebrow">{$t("library.tags")}</div>
          <div class="row wrap">
            {#each tagsOf as tag}
              <span class="chip">#{tag.name} <button class="x" aria-label={$t("common.delete")} onclick={() => setLabel(tag.id, false)}>✕</button></span>
            {/each}
            <form onsubmit={addTag}>
              <input type="text" class="taginput" list="all-tags" bind:value={tagInput} placeholder={$t("detail.add_tag")} />
              <datalist id="all-tags">{#each allTags as tag}<option value={tag.name}></option>{/each}</datalist>
            </form>
          </div>
        </div>

        {#if labels.some((l) => l.kind === "collection")}
          <div class="block">
            <div class="eyebrow">{$t("library.collections")}</div>
            <div class="row wrap">
              {#each labels.filter((l) => l.kind === "collection") as c}
                <label class="chip check"><input type="checkbox" checked={collectionsOf.has(c.id)} onchange={(e) => setLabel(c.id, e.target.checked)} /> {c.name}</label>
              {/each}
            </div>
          </div>
        {/if}

        {#if work.abstract}
          <div class="block">
            <div class="eyebrow">{$t("source.abstract")}</div>
            <p class="abstract">{work.abstract}</p>
          </div>
        {/if}

        {#if work.subjects.length}
          <div class="block">
            <div class="eyebrow">MeSH</div>
            <div class="row wrap">
              {#each work.subjects.filter((s) => s.scheme === "mesh") as s}<span class="chip" class:major={s.major}>{s.term}</span>{/each}
            </div>
          </div>
        {/if}
        <p class="small faint">{$t("detail.added_on", { date: $fmt.date(work.created_at) })}</p>
      {:else if tab === "pdf"}
        {#if pdfUrl}
          <iframe title="PDF" src={pdfUrl}></iframe>
        {:else if !files.length}
          <div class="nopdf">
            <p class="muted">{$t("detail.no_pdf")}</p>
            <button class="primary" disabled={fetching} onclick={fetchPdf}>{fetching ? $t("detail.searching_pdf") : $t("detail.find_pdf")}</button>
            <p class="small faint">{$t("detail.drop_pdf")}</p>
          </div>
        {/if}
      {:else}
        <div class="note">
          <textarea
            bind:value={noteText}
            oninput={() => (noteSaved = false)}
            onblur={() => !noteSaved && saveNote()}
            onkeydown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); saveNote(); } }}
            placeholder={$t("detail.note_placeholder")}
          ></textarea>
          <div class="row small faint">
            <span class="grow">{noteSaved ? $t("detail.note_saved") : $t("detail.note_unsaved")}</span>
            <span>Markdown · [[clé]]</span>
          </div>
          {#if note?.links?.length || note?.backlinks?.length}
            <div class="links small">
              {#if note.links.length}
                <div>{$t("detail.links")} : {#each note.links as l, i}{i ? ", " : ""}<a href="#/library/{l}">{l}</a>{/each}</div>
              {/if}
              {#if note.backlinks.length}
                <div>{$t("detail.backlinks")} : {#each note.backlinks as l, i}{i ? ", " : ""}<a href="#/library/{l}">{l}</a>{/each}</div>
              {/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .detail { display: flex; flex-direction: column; height: 100%; }
  header { padding: 18px 22px 10px; display: grid; gap: 6px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.3; }
  header p { margin: 0; }
  .authors { max-height: 3em; overflow: hidden; }
  .ids { gap: 6px; }
  .id { border: none; cursor: copy; font: 11.5px var(--mono); }
  .tabs { gap: 2px; padding: 0 18px; border-bottom: 1px solid var(--border); }
  .tabs button { border: none; background: transparent; border-radius: 0; padding: 8px 10px; color: var(--muted); border-bottom: 2px solid transparent; }
  .tabs button.on { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
  .pane { flex: 1; padding: 16px 22px; }
  .pane.flush { padding: 0; overflow: hidden; }
  .block { margin-bottom: 16px; display: grid; gap: 6px; }
  .segmented { display: inline-flex; background: var(--surface-2); border-radius: 8px; padding: 2px; }
  .segmented button { border: none; background: transparent; padding: 3px 10px; border-radius: 6px; color: var(--muted); }
  .segmented button.on { background: var(--surface); color: var(--text); box-shadow: var(--shadow); font-weight: 550; }
  .star { font-size: 17px; padding: 0 2px; color: var(--border); }
  .star.on { color: #e0a100; }
  .x { border: none; background: none; padding: 0 0 0 2px; font-size: 10px; color: var(--faint); }
  .taginput { width: 150px; padding: 2px 8px; font-size: 12.5px; }
  .check { cursor: pointer; }
  .major { font-weight: 650; }
  .abstract { margin: 0; white-space: pre-line; font-size: 13.5px; }
  iframe { width: 100%; height: 100%; border: none; background: var(--surface-2); }
  .nopdf { display: grid; justify-items: center; gap: 10px; padding-top: 12vh; text-align: center; }
  .note { display: flex; flex-direction: column; gap: 6px; height: 100%; }
  .note textarea { flex: 1; min-height: 300px; font: 13.5px/1.6 var(--mono); padding: 12px; }
  .links { display: grid; gap: 4px; margin-top: 6px; }
</style>
