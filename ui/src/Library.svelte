<script>
  import { onMount } from "svelte";
  import WorkDetail from "./WorkDetail.svelte";
  import Badges from "./lib/Badges.svelte";
  import { EXPORTS, exportWorks, importFromZotero, isFromOutside, makePackage, sendToZotero } from "./lib/actions.js";
  import { api } from "./lib/api.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let { route, version, aiEnabled = false } = $props();

  const PAGE = 100;
  let labels = $state([]);
  let filter = $state({ type: "all" }); // all | read_status | label | journal
  let q = $state("");
  let items = $state([]);
  let total = $state(0);
  let loading = $state(false);
  let checked = $state(new Set());
  let newLabel = $state(null); // kind en cours de création
  let newName = $state("");
  let searchBox;

  // Largeurs des panneaux, gardées d'une session à l'autre.
  let widths = $state(load("biblio.widths", [220, 420]));
  function load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  }

  const current = $derived(decodeURIComponent(route.match(/^\/library\/(.+)/)?.[1] ?? ""));
  const collections = $derived(labels.filter((l) => l.kind === "collection"));
  const tags = $derived(labels.filter((l) => l.kind === "tag"));
  const currentIndex = $derived(items.findIndex((w) => w.citekey === current));

  onMount(refreshLabels);

  $effect(() => {
    version; // rafraîchir après un import (glisser-déposer)
    filter; q;
    const timer = setTimeout(() => refresh(), q ? 220 : 0);
    return () => clearTimeout(timer);
  });

  async function refreshLabels() {
    labels = await api("/labels");
  }

  async function refresh(more = false) {
    loading = true;
    try {
      if (q.trim()) {
        const out = await api("/search/library", { params: { q: q.trim(), limit: 100 } });
        items = out.results;
        total = out.total;
      } else {
        const params = { limit: PAGE, offset: more ? items.length : 0 };
        if (filter.type === "read_status") params.read_status = filter.value;
        if (filter.type === "label") params.label = filter.value;
        if (filter.type === "journal") params.journal_status = filter.value;
        if (filter.type === "retracted") params.retracted = true;
        const out = await api("/works", { params });
        items = more ? [...items, ...out.items] : out.items;
        total = out.total;
      }
    } catch (e) {
      toast(e.message, "error");
    } finally {
      loading = false;
    }
  }

  function choose(f) {
    filter = f;
    q = "";
    checked = new Set();
  }

  function open(work) {
    location.hash = `#/library/${encodeURIComponent(work.citekey)}`;
  }

  function toggle(id) {
    const next = new Set(checked);
    next.has(id) ? next.delete(id) : next.add(id);
    checked = next;
  }

  async function createLabel(e) {
    e.preventDefault();
    if (!newName.trim()) return (newLabel = null);
    await api("/labels", { method: "POST", body: { kind: newLabel, name: newName } });
    newLabel = null;
    newName = "";
    refreshLabels();
  }

  async function removeLabel(label) {
    if (!confirm($t("library.confirm_delete_label", { name: label.name }))) return;
    await api(`/labels/${label.id}`, { method: "DELETE" });
    if (filter.type === "label" && filter.value === label.id) choose({ type: "all" });
    refreshLabels();
  }

  async function batchLabel(id) {
    if (!id) return;
    const out = await api(`/labels/${id}/works`, { method: "POST", body: { works: [...checked].map(String) } });
    toast($t("library.batch_done", { n: out.changed }), "ok");
    refreshLabels();
    refresh();
  }

  async function batchRead(status) {
    for (const id of checked) await api(`/works/${id}`, { method: "PATCH", body: { read_status: status } });
    toast($t("library.batch_done", { n: checked.size }), "ok");
    refresh();
  }

  // Glisser une notice sur une collection ou un tag de la colonne de gauche.
  async function dropOn(e, label) {
    if (isFromOutside(e.dataTransfer)) { // depuis Zotero : rangé dans cette collection (un fichier PDF : import simple)
      if (e.dataTransfer.files.length || label.kind !== "collection") return;
      e.preventDefault();
      e.handledByLabel = true; // la fenêtre (App) ne refait pas l'import
      if (await importFromZotero(e.dataTransfer, label.id)) { refresh(); refreshLabels(); }
      return;
    }
    e.preventDefault();
    const ids = e.dataTransfer.getData("text/x-biblio-works");
    if (!ids) return;
    const out = await api(`/labels/${label.id}/works`, { method: "POST", body: { works: ids.split(",") } });
    toast($t("library.added_to", { n: out.changed, name: label.name }), "ok");
    refreshLabels();
  }

  // Importer une collection Zotero choisie dans la liste (si la glisser depuis Zotero ne passe pas).
  let zoteroList = $state(null); // null : fermé ; [] : chargement ; sinon les collections
  let sameName = $state(true);
  let importingKey = $state(null);

  async function openZotero() {
    zoteroList = [];
    try {
      zoteroList = await api("/zotero/collections");
    } catch (e) {
      zoteroList = null;
      toast(e.message, "error", 12_000);
    }
  }

  async function importZotero(c) {
    importingKey = c.key;
    try {
      const label = sameName ? (await api("/labels", { method: "POST", body: { kind: "collection", name: c.name } })).id : null;
      const out = await api("/zotero/import", { method: "POST", body: { collection: c.key, label } });
      toast($t("zotero.imported", { created: out.created.length, completed: out.completed.length, pdfs: out.pdfs }), "ok", 8000);
      zoteroList = null;
      refresh();
      refreshLabels();
    } catch (e) {
      toast(e.message, "error", 12_000);
    } finally {
      importingKey = null;
    }
  }

  function drag(e, work) {
    const ids = checked.has(work.id) ? [...checked] : [work.id];
    e.dataTransfer.setData("text/x-biblio-works", ids.join(","));
  }

  function resize(i, e) {
    const start = e.clientX;
    const initial = widths[i];
    const move = (ev) => {
      widths[i] = Math.max(160, Math.min(700, initial + ev.clientX - start));
    };
    const up = () => {
      removeEventListener("pointermove", move);
      try { localStorage.setItem("biblio.widths", JSON.stringify(widths)); } catch {}
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up, { once: true });
  }

  function onKey(e) {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
    if (e.key === "/" && !typing) {
      e.preventDefault();
      searchBox?.focus();
    } else if (e.key === "Escape" && typing) {
      e.target.blur();
    } else if (!typing && !e.metaKey && !e.ctrlKey && items.length) {
      if (e.key === "j") open(items[Math.min(currentIndex + 1, items.length - 1)]);
      else if (e.key === "k") open(items[Math.max(currentIndex - 1, 0)]);
      else if (e.key === "x" && currentIndex >= 0) toggle(items[currentIndex].id);
    }
  }

  const focus = (el) => el.focus();
  const isOn = (f) => filter.type === f.type && filter.value === f.value && !q;
</script>

<svelte:window onkeydown={onKey} />

<div class="library" style="grid-template-columns: {widths[0]}px 5px {widths[1]}px 5px minmax(0, 1fr)">
  <nav class="filters scroll">
    <button class="item" class:on={isOn({ type: "all" })} onclick={() => choose({ type: "all" })}>{$t("library.all")}</button>
    {#each [["unread", "library.unread"], ["to_read", "read.to_read"], ["read", "read.read"], ["to_reread", "read.to_reread"]] as [value, key]}
      <button class="item" class:on={isOn({ type: "read_status", value })} onclick={() => choose({ type: "read_status", value })}>{$t(key)}</button>
    {/each}

    {#each [["collection", collections, "library.collections"], ["tag", tags, "library.tags"]] as [kind, list, title]}
      <div class="group row">
        <span class="eyebrow grow">{$t(title)}</span>
        {#if kind === "collection"}
          <button class="ghost small" title={$t("zotero.import_title")} onclick={openZotero}>{$t("zotero.import_button")}</button>
        {/if}
        <button class="ghost small" title={$t("library.new")} onclick={() => { newLabel = kind; newName = ""; }}>＋</button>
      </div>
      {#if newLabel === kind}
        <form onsubmit={createLabel}>
          <input type="text" class="new" bind:value={newName} use:focus placeholder={$t("library.name")} onblur={createLabel} />
        </form>
      {/if}
      {#each list as label (label.id)}
        <div class="labelrow" role="listitem" ondragover={(e) => e.preventDefault()} ondrop={(e) => dropOn(e, label)}>
          <button class="item grow" class:on={isOn({ type: "label", value: label.id })} onclick={() => choose({ type: "label", value: label.id })}>
            <span class="grow name">{kind === "tag" ? "#" : ""}{label.name}</span><span class="faint small">{label.count}</span>
          </button>
          <button class="ghost small del" title={$t("common.delete")} onclick={() => removeLabel(label)}>✕</button>
        </div>
      {:else}
        {#if newLabel !== kind}<p class="small faint hint">{$t(`library.no_${kind}`)}</p>{/if}
      {/each}
    {/each}

    <div class="group"><span class="eyebrow">{$t("library.journals")}</span></div>
    {#each ["recommended", "not_listed", "withdrawn", "preprint"] as value}
      <button class="item" class:on={isOn({ type: "journal", value })} onclick={() => choose({ type: "journal", value })}>{$t(`status.${value}.label`)}</button>
    {/each}
    <button class="item" class:on={isOn({ type: "retracted" })} onclick={() => choose({ type: "retracted" })}>⚠ {$t("watch.retracted_plural")}</button>
  </nav>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="splitter" onpointerdown={(e) => resize(0, e)}></div>

  <section class="list">
    <div class="search">
      <input type="search" bind:this={searchBox} bind:value={q} placeholder={$t("library.search")} />
      <div class="row small faint count">
        <input type="checkbox" aria-label={$t("library.select_all")} title={$t("library.select_all")}
          checked={items.length > 0 && items.every((w) => checked.has(w.id))}
          onchange={(e) => (checked = e.target.checked ? new Set(items.map((w) => w.id)) : new Set())} />
        {loading ? "…" : $t("library.count", { n: $fmt.number(total) })}
      </div>
    </div>
    {#if checked.size}
      <div class="batch row wrap">
        <strong class="small">{$t("library.selected", { n: checked.size })}</strong>
        <select onchange={(e) => { batchLabel(Number(e.target.value)); e.target.value = ""; }}>
          <option value="">{$t("library.add_to")}</option>
          {#each labels as label}<option value={label.id}>{label.kind === "tag" ? "#" : ""}{label.name}</option>{/each}
        </select>
        <button class="small" onclick={() => batchRead("read")}>{$t("library.mark_read")}</button>
        <select onchange={(e) => { exportWorks({ works: [...checked].map(String) }, e.target.value); e.target.value = ""; }}>
          <option value="">{$t("export.title")}…</option>
          {#each EXPORTS as [format, label]}<option value={format}>{label}</option>{/each}
        </select>
        <button class="small" onclick={() => sendToZotero({ works: [...checked].map(String) })}>Zotero</button>
        <button class="small" onclick={() => makePackage({ works: [...checked].map(String) })}>{$t("package.make")}</button>
        <button class="ghost small" onclick={() => (checked = new Set())}>{$t("common.cancel")}</button>
      </div>
    {/if}
    <ul class="scroll">
      {#each items as work (work.id)}
        <li class:on={work.citekey === current} draggable="true" ondragstart={(e) => drag(e, work)}>
          <input type="checkbox" checked={checked.has(work.id)} onchange={() => toggle(work.id)} aria-label={$t("library.select")} />
          <button class="row-btn" onclick={() => open(work)}>
            <span class="t title-serif">{work.title}</span>
            <span class="small muted">{work.first_author ?? ""}{work.year ? ` · ${work.year}` : ""}{work.container_title ? ` · ${work.container_title}` : ""}</span>
            {#if work.passages?.length}
              <span class="small snippet">{@html work.passages[0].snippet}</span>
            {/if}
            <span class="row badges">
              <Badges rec={work} compact />
              {#if work.read_status}<span class="chip">{$t(`read.${work.read_status}`)}</span>{/if}
              {#if work.rating}<span class="faint small">{"★".repeat(work.rating)}</span>{/if}
            </span>
          </button>
        </li>
      {:else}
        <li class="empty muted">{loading ? "" : q ? $t("library.no_match") : $t("library.empty")}</li>
      {/each}
      {#if !q && items.length < total}
        <li class="more"><button class="small" onclick={() => refresh(true)}>{$t("library.more")}</button></li>
      {/if}
    </ul>
  </section>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="splitter" onpointerdown={(e) => resize(1, e)}></div>

  <section class="detail">
    {#if current}
      {#key current}
        <WorkDetail ref={current} {labels} {aiEnabled} onlabels={refreshLabels} onchange={() => refresh()} />
      {/key}
    {:else}
      <div class="placeholder muted">
        <p>{$t("library.pick")}</p>
        <p class="small faint">{$t("library.drop_hint")}</p>
        <p class="small faint">
          <span class="kbd">/</span> {$t("keys.search")} · <span class="kbd">j</span> <span class="kbd">k</span> {$t("keys.navigate")} ·
          <span class="kbd">x</span> {$t("keys.select")}
        </p>
      </div>
    {/if}
  </section>
</div>

{#if zoteroList}
  <div class="backdrop" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) zoteroList = null; }}>
    <div class="modal card" role="dialog" aria-modal="true" aria-labelledby="zotero-title">
      <h2 id="zotero-title">{$t("zotero.import_title")}</h2>
      <p class="small muted">{$t("zotero.import_help")}</p>
      <label class="row small"><input type="checkbox" bind:checked={sameName} /> {$t("zotero.same_name")}</label>
      <div class="zlist scroll">
        {#if !zoteroList.length}<p class="muted small">…</p>{/if}
        {#each zoteroList as c (c.key)}
          <button class="item zrow" style="padding-left: {10 + c.level * 16}px" disabled={!!importingKey} onclick={() => importZotero(c)}>
            <span class="grow name">{c.name}</span>
            <span class="faint small">{importingKey === c.key ? "…" : c.count}</span>
          </button>
        {/each}
      </div>
      <div class="row"><span class="grow"></span><button onclick={() => (zoteroList = null)}>{$t("common.close")}</button></div>
    </div>
  </div>
{/if}

<style>
  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.35); display: grid; place-items: center; z-index: 55; }
  .modal { width: min(520px, 92vw); max-height: 80vh; display: flex; flex-direction: column; gap: 10px; padding: 20px 22px; }
  .modal h2 { margin: 0; font-size: 18px; }
  .modal p { margin: 0; }
  .zlist { min-height: 80px; max-height: 50vh; border: 1px solid var(--border); border-radius: 8px; padding: 4px; }
  .zrow { width: 100%; display: flex; gap: 8px; }
  .library { display: grid; height: 100%; min-height: 0; }
  .library > * { min-height: 0; min-width: 0; }
  .splitter { cursor: col-resize; background: var(--border); background-clip: content-box; padding: 0 2px; }
  .splitter:hover { background-color: var(--accent); }

  .filters { padding: 12px 8px; display: flex; flex-direction: column; gap: 1px; }
  .item {
    border: none; background: transparent; justify-content: flex-start; text-align: left; width: 100%;
    padding: 4px 10px; border-radius: 6px; color: var(--muted);
  }
  .item:hover { background: var(--surface-2); color: var(--text); }
  .item.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .name { overflow: hidden; text-overflow: ellipsis; }
  .group { margin: 14px 0 4px; padding: 0 4px 0 10px; }
  .labelrow { display: flex; align-items: center; }
  .labelrow .del { visibility: hidden; padding: 0 5px; }
  .labelrow:hover .del { visibility: visible; }
  .new { width: 100%; padding: 3px 8px; margin: 2px 0; }
  .hint { margin: 2px 10px; }

  .list { display: flex; flex-direction: column; background: var(--surface); }
  .search { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  .search input[type="search"] { width: 100%; }
  .count { margin-top: 5px; }
  .batch { padding: 8px 12px; background: var(--accent-soft); border-bottom: 1px solid var(--border); }
  .batch select { padding: 2px 6px; }
  ul { list-style: none; margin: 0; padding: 0; flex: 1; }
  li { display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
  li.on { background: var(--accent-soft); }
  li input { margin-top: 4px; }
  .row-btn {
    border: none; background: transparent; padding: 0; text-align: left; display: flex; flex-direction: column;
    align-items: stretch; gap: 3px; white-space: normal; flex: 1; min-width: 0;
  }
  .t { font-size: 14.5px; line-height: 1.35; font-weight: 600; }
  .snippet { color: var(--muted); }
  .badges { gap: 6px; margin-top: 2px; }
  .empty { padding: 30px 16px; justify-content: center; border: none; }
  .more { justify-content: center; border: none; }

  .detail { overflow: hidden; }
  .placeholder { height: 100%; display: grid; place-content: center; text-align: center; padding: 20px; }
</style>
