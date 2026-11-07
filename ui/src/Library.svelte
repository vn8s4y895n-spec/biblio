<script>
  import { onMount } from "svelte";
  import WorkDetail from "./WorkDetail.svelte";
  import Badges from "./lib/Badges.svelte";
  import { EXPORTS, exportWorks, importZoteroRdf, makePackage, sendToZotero } from "./lib/actions.js";
  import { api } from "./lib/api.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let { route, version, aiEnabled = false } = $props();

  const PAGE = 100;
  let labels = $state([]);
  // inbox (« Sans statut », vue par défaut : à trier + favoris) | all | favorite | read_status | label | journal
  let filter = $state({ type: "inbox" });
  let q = $state("");
  let items = $state([]);
  let total = $state(0);
  let loading = $state(false);
  let checked = $state(new Set());
  let newLabel = $state(null); // kind en cours de création
  let newName = $state("");
  let searchBox;
  let counts = $state(null); // effectifs de la colonne de gauche, années et types d'étude présents
  // Préférences d'affichage, gardées d'une session à l'autre.
  let sort = $state(load("biblio.sort", "added"));
  // Affichage de la liste : cartes, compact (une ligne par article) ou tableau (colonnes triables).
  let display = $state(load("biblio.display", load("biblio.compact", false) ? "compact" : "cards"));
  const compact = $derived(display === "compact");
  let collapsed = $state(new Set(load("biblio.collapsed", []))); // collections repliées (chemin)
  let showFilters = $state(false);
  // Collections et tags cochés : la bibliothèque n'affiche que leurs articles (ce qui est en cours ou intéressant),
  // le reste est archivé sans être perdu. Gardés d'une session à l'autre.
  let shown = $state(new Set(load("biblio.shown", [])));
  let shownAll = $state(load("biblio.shownAll", false)); // dans tous les cochés (sinon : dans l'un au moins)
  const shownParams = () => (shown.size ? { labels: [...shown], all_labels: shownAll } : {});
  function show(id, on) {
    const next = new Set(shown);
    on ? next.add(id) : next.delete(id);
    shown = next;
    keep("biblio.shown", [...next]);
  }
  let views = $state([]); // vues enregistrées
  let editing = $state(null); // collection ou tag renommé sur place (id), ou "view" : nom de la vue à enregistrer
  let editName = $state("");
  let archivesOpen = $state(load("biblio.archivesOpen", false));
  let palette = $state(null); // id de la collection / du tag dont on choisit la couleur
  const COLORS = { red: "#d64545", orange: "#e07b39", yellow: "#d4a72c", green: "#3f9d5a", teal: "#1f8f8a",
                   blue: "#3a7bd5", purple: "#8a5cc7", gray: "#8a8f98" };  // prettier-ignore
  let detailVersion = $state(0); // la fiche ouverte se recharge après un changement fait depuis la liste
  let extra = $state({ yearFrom: "", yearTo: "", studyType: "", pdf: "" }); // filtres combinables

  // Largeurs des panneaux, gardées d'une session à l'autre.
  let widths = $state(load("biblio.widths", [220, 420]));
  function load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  }
  function keep(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
  }
  const activeFilters = $derived(Object.values(extra).filter(Boolean).length);

  const current = $derived(decodeURIComponent(route.match(/^\/library\/(.+)/)?.[1] ?? ""));
  const collections = $derived(labels.filter((l) => l.kind === "collection"));
  const colored = $derived(new Map(labels.filter((l) => l.color).map((l) => [l.id, l])));
  const pinned = $derived(collections.filter((l) => l.pinned));
  const tags = $derived(labels.filter((l) => l.kind === "tag"));
  const currentIndex = $derived(items.findIndex((w) => w.citekey === current));

  onMount(refreshLabels);

  $effect(() => {
    version; // rafraîchir après un import (glisser-déposer)
    filter; q; sort; shown; shownAll; extra.yearFrom; extra.yearTo; extra.studyType; extra.pdf;
    const timer = setTimeout(() => refresh(), q ? 220 : 0);
    return () => clearTimeout(timer);
  });

  async function refreshLabels() {
    [labels, counts, views] = await Promise.all([api("/labels"), api("/library/counts", { params: shownParams() }), api("/views")]);
    const known = new Set(labels.map((l) => l.id)); // une collection supprimée n'est plus cochée
    if ([...shown].some((id) => !known.has(id))) {
      shown = new Set([...shown].filter((id) => known.has(id)));
      keep("biblio.shown", [...shown]);
    }
  }

  async function refresh(more = false) {
    loading = true;
    try {
      if (q.trim()) {
        const out = await api("/search/library", { params: { q: q.trim(), limit: 100 } });
        items = out.results;
        total = out.total;
      } else {
        const params = { limit: PAGE, offset: more ? items.length : 0, sort, ...shownParams() };
        if (extra.yearFrom) params.year_from = extra.yearFrom;
        if (extra.yearTo) params.year_to = extra.yearTo;
        if (extra.studyType) params.study_type = extra.studyType;
        if (extra.pdf) params.has_pdf = extra.pdf === "yes";
        if (filter.type === "inbox") params.inbox = true;
        if (filter.type === "recent") params.recent = true;
        if (filter.type === "favorite") params.favorite = true;
        if (filter.type === "read_status") params.read_status = filter.value;
        if (filter.type === "label") params.label = filter.value;
        if (filter.type === "journal") params.journal_status = filter.value;
        if (filter.type === "retracted") params.retracted = true;
        const out = await api("/works", { params });
        items = more ? [...items, ...out.items] : out.items;
        total = out.total;
        if (!more) api("/library/counts", { params: shownParams() }).then((c) => (counts = c));
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
    api(`/works/${work.id}/opened`, { method: "POST" }).catch(() => {}); // vue « Récemment ouverts »
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
    if (filter.type === "label" && filter.value === label.id) choose({ type: "inbox" });
    refreshLabels();
  }

  async function batchLabel(id) {
    if (!id) return;
    const out = await api(`/labels/${id}/works`, { method: "POST", body: { works: [...checked].map(String) } });
    toast($t("library.batch_done", { n: out.changed }), "ok");
    refreshLabels();
    refresh();
  }

  // Statut de lecture ou favori des notices cochées, ou de celle ouverte (raccourcis clavier).
  async function batchUpdate(changes, ids = [...checked]) {
    for (const id of ids) await api(`/works/${id}`, { method: "PATCH", body: changes });
    toast($t("library.batch_done", { n: ids.length }), "ok", 2000);
    detailVersion++;
    refresh();
  }

  // Déplacer vers une collection (sortie des autres), ou retirer de la collection affichée.
  async function batchMove(id) {
    if (!id) return;
    const out = await api(`/labels/${id}/works`, { method: "POST", body: { works: [...checked].map(String), move: true } });
    toast($t("library.batch_done", { n: out.changed || checked.size }), "ok");
    checked = new Set();
    detailVersion++;
    refreshLabels();
    refresh();
  }

  async function batchRemove() {
    await api(`/labels/${filter.value}/works`, { method: "POST", body: { works: [...checked].map(String), remove: true } });
    toast($t("library.batch_done", { n: checked.size }), "ok");
    checked = new Set();
    detailVersion++;
    refreshLabels();
    refresh();
  }

  // Collections en arborescence : « Parent / Enfant » (nom donné par l'import Zotero ou à la main). Les collections
  // épinglées (en cours) sont en haut ; s'il y en a, les autres sont rangées sous « Archives », repliées.
  const tree = $derived(buildTree(pinned.length ? pinned : collections));
  const archives = $derived(pinned.length ? buildTree(collections.filter((l) => !l.pinned)) : null);
  function buildTree(list) {
    const root = { children: new Map() };
    for (const label of list) {
      let node = root;
      label.name.split(" / ").forEach((part, i, parts) => {
        const path = parts.slice(0, i + 1).join(" / ");
        if (!node.children.has(part)) node.children.set(part, { name: part, path, children: new Map(), label: null });
        node = node.children.get(part);
      });
      node.label = label;
    }
    return root;
  }

  // --- Vues enregistrées : l'état de l'écran sous un nom (cases cochées, vue, filtres, tri, affichage).
  function snapshot() {
    return { filter, shown: [...shown], shownAll, sort, extra: { ...extra }, display };
  }

  function applyView(view) {
    const s = view.state;
    filter = s.filter ?? { type: "inbox" };
    shown = new Set(s.shown ?? []);
    shownAll = !!s.shownAll;
    sort = s.sort ?? "added";
    extra = { yearFrom: "", yearTo: "", studyType: "", pdf: "", ...s.extra };
    display = s.display ?? display;
    q = "";
    checked = new Set();
    keep("biblio.shown", [...shown]);
    keep("biblio.shownAll", shownAll);
    keep("biblio.sort", sort);
    keep("biblio.display", display);
  }

  async function saveView() {
    const name = editName.trim();
    editing = null;
    if (!name) return;
    await api("/views", { method: "PUT", body: { name, state: snapshot() } });
    toast($t("library.view_saved", { name }), "ok");
    refreshLabels();
  }

  async function deleteView(view) {
    if (!confirm($t("library.view_delete", { name: view.name }))) return;
    await api(`/views/${view.id}`, { method: "DELETE" });
    refreshLabels();
  }

  // --- Collections et tags : renommer, colorer, épingler, glisser une collection dans une autre.
  async function updateLabel(label, changes) {
    try {
      await api(`/labels/${label.id}`, { method: "PATCH", body: changes });
    } catch (e) {
      toast(e.message, "error", 8000);
    }
    refreshLabels();
  }

  function startRename(label) {
    editing = label.id;
    editName = label.name;
  }

  function finishRename(label) {
    if (editing !== label.id) return;
    editing = null;
    if (editName.trim() && editName.trim() !== label.name) updateLabel(label, { name: editName.trim() });
  }

  const leaf = (name) => name.split(" / ").pop();

  function fold(path) {
    const next = new Set(collapsed);
    next.has(path) ? next.delete(path) : next.add(path);
    collapsed = next;
    keep("biblio.collapsed", [...next]);
  }

  async function toggleFavorite(work) {
    await api(`/works/${work.id}`, { method: "PATCH", body: { favorite: !work.favorite } });
    detailVersion++;
    refresh();
  }

  // Glisser une notice sur une collection ou un tag de la colonne de gauche ; ou une collection dans une autre
  // (elle devient sa sous-collection), ou sur le titre « Collections » (elle revient au premier niveau).
  async function dropOn(e, label) {
    e.preventDefault();
    const moved = Number(e.dataTransfer.getData("text/x-biblio-label"));
    if (moved) {
      const source = collections.find((c) => c.id === moved);
      if (source && (!label || (label.kind === "collection" && label.id !== moved)))
        updateLabel(source, { name: label ? `${label.name} / ${leaf(source.name)}` : leaf(source.name) });
      return;
    }
    if (!label) return;
    const ids = e.dataTransfer.getData("text/x-biblio-works");
    if (!ids) return;
    const out = await api(`/labels/${label.id}/works`, { method: "POST", body: { works: ids.split(",") } });
    toast($t("library.added_to", { n: out.changed, name: label.name }), "ok");
    refreshLabels();
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
      else if (currentIndex >= 0 || checked.size) {
        const ids = checked.size ? [...checked] : [items[currentIndex].id];
        const STATUS = { 0: null, 1: "to_read", 2: "to_reread", 3: "read" };
        if (e.key in STATUS) batchUpdate({ read_status: STATUS[e.key] }, ids);
        else if (e.key === "f") batchUpdate({ favorite: !items.filter((w) => ids.includes(w.id)).every((w) => w.favorite) }, ids);
      }
    }
  }

  const focus = (el) => el.focus();
  // Collection de Biblio sélectionnée à gauche (cible d'un import), sinon null.
  const selectedCollection = () => (filter.type === "label" && collections.some((c) => c.id === filter.value) ? filter.value : null);
  const isOn = (f) => filter.type === f.type && filter.value === f.value && !q;
</script>

<svelte:window onkeydown={onKey} />

{#snippet labelRow(label, text, depth, node = null)}
  <div class="labelrow" role="listitem" style="padding-left: {depth * 12}px" draggable={label.kind === "collection"}
    ondragstart={(e) => e.dataTransfer.setData("text/x-biblio-label", String(label.id))}
    ondragover={(e) => e.preventDefault()} ondrop={(e) => dropOn(e, label)}>
    {#if node?.children.size}
      <button class="ghost tw" aria-label={$t("library.fold")} onclick={() => fold(node.path)}>{collapsed.has(node.path) ? "▸" : "▾"}</button>
    {/if}
    <input type="checkbox" class="show" checked={shown.has(label.id)} title={$t("library.show_help")}
      aria-label={$t("library.show", { name: label.name })} onchange={(e) => show(label.id, e.target.checked)} />
    <button class="ghost dot" title={$t("library.color")} aria-label={$t("library.color")}
      style="--c: {COLORS[label.color] ?? 'transparent'}" class:none={!label.color} onclick={() => (palette = palette === label.id ? null : label.id)}></button>
    {#if editing === label.id}
      <form class="grow" onsubmit={(e) => { e.preventDefault(); finishRename(label); }}>
        <input type="text" class="new" bind:value={editName} use:focus onblur={() => finishRename(label)}
          onkeydown={(e) => { if (e.key === "Escape") editing = null; }} />
      </form>
    {:else}
      <button class="item grow" class:on={isOn({ type: "label", value: label.id })} title={$t("library.rename_help")}
        onclick={() => choose({ type: "label", value: label.id })} ondblclick={() => startRename(label)}>
        <span class="grow name">{text}</span><span class="faint small">{label.count}</span>
      </button>
    {/if}
    {#if label.kind === "collection"}
      <button class="ghost small pin" class:on={label.pinned} title={$t(label.pinned ? "library.unpin" : "library.pin")}
        onclick={() => updateLabel(label, { pinned: !label.pinned })}>📌</button>
    {/if}
    <button class="ghost small del" title={$t("common.delete")} onclick={() => removeLabel(label)}>✕</button>
  </div>
  {#if palette === label.id}
    <div class="palette" style="padding-left: {depth * 12 + 30}px">
      {#each Object.entries(COLORS) as [name, hex]}
        <button class="ghost swatch" style="--c: {hex}" class:on={label.color === name} aria-label={name}
          onclick={() => { updateLabel(label, { color: name }); palette = null; }}></button>
      {/each}
      <button class="ghost small" onclick={() => { updateLabel(label, { color: null }); palette = null; }}>{$t("library.no_color")}</button>
    </div>
  {/if}
{/snippet}

{#snippet branch(node, depth)}
  {#if node.label}
    {@render labelRow(node.label, node.name, depth, node)}
  {:else}
    <div class="labelrow" style="padding-left: {depth * 12}px">
      <button class="ghost tw" aria-label={$t("library.fold")} onclick={() => fold(node.path)}>{collapsed.has(node.path) ? "▸" : "▾"}</button>
      <span class="item grow faint name">{node.name}</span>
    </div>
  {/if}
  {#if !collapsed.has(node.path)}
    {#each node.children.values() as child (child.path)}{@render branch(child, depth + 1)}{/each}
  {/if}
{/snippet}

<div class="library" style="grid-template-columns: {widths[0]}px 5px {display === 'table' && !q ? 'minmax(0, 1fr) 5px 38%' : `${widths[1]}px 5px minmax(0, 1fr)`}">
  <nav class="filters scroll">
    <div class="group row first">
      <span class="eyebrow grow">{$t("library.views")}</span>
      <button class="ghost small" title={$t("library.view_new")} onclick={() => { editing = "view"; editName = ""; }}>＋</button>
    </div>
    {#if editing === "view"}
      <form onsubmit={(e) => { e.preventDefault(); saveView(); }}>
        <input type="text" class="new" bind:value={editName} use:focus placeholder={$t("library.view_name")} onblur={saveView} />
      </form>
    {/if}
    {#each views as view (view.id)}
      <div class="labelrow">
        <button class="item grow" onclick={() => applyView(view)} title={$t("library.view_apply")}><span class="grow name">◧ {view.name}</span></button>
        <button class="ghost small del" title={$t("common.delete")} onclick={() => deleteView(view)}>✕</button>
      </div>
    {:else}
      {#if editing !== "view"}<p class="small faint hint">{$t("library.no_view")}</p>{/if}
    {/each}
    {#if shown.size}
      <div class="shown small">
        <span>{$t("library.shown", { n: shown.size })}</span>
        {#if shown.size > 1}
          <select class="mini" value={shownAll ? "all" : "any"} aria-label={$t("library.shown_mode")}
            onchange={(e) => { shownAll = e.target.value === "all"; keep("biblio.shownAll", shownAll); }}>
            <option value="any">{$t("library.shown_any")}</option>
            <option value="all">{$t("library.shown_all")}</option>
          </select>
        {/if}
        <button class="ghost small" onclick={() => { shown = new Set(); keep("biblio.shown", []); }}>{$t("library.shown_clear")}</button>
      </div>
    {/if}
    <button class="item" class:on={isOn({ type: "inbox" })} title={$t("library.inbox_help")} onclick={() => choose({ type: "inbox" })}><span class="grow">{$t("library.unread")}</span><span class="faint small">{counts?.inbox ?? ""}</span></button>
    {#each [["to_read", "read.to_read"], ["to_reread", "read.to_reread"], ["read", "read.read"]] as [value, key]}
      <button class="item" class:on={isOn({ type: "read_status", value })} onclick={() => choose({ type: "read_status", value })}><span class="grow">{$t(key)}</span><span class="faint small">{counts?.[value] ?? ""}</span></button>
    {/each}
    <button class="item" class:on={isOn({ type: "favorite" })} onclick={() => choose({ type: "favorite" })}><span class="grow">♥ {$t("library.favorites")}</span><span class="faint small">{counts?.favorite ?? ""}</span></button>
    <button class="item" class:on={isOn({ type: "recent" })} onclick={() => choose({ type: "recent" })}><span class="grow">🕘 {$t("library.recent")}</span></button>
    <button class="item" class:on={isOn({ type: "all" })} onclick={() => choose({ type: "all" })}><span class="grow">{$t("library.all")}</span><span class="faint small">{counts?.all ?? ""}</span></button>

    {#each [["collection", collections, "library.collections"], ["tag", tags, "library.tags"]] as [kind, list, title]}
      <div class="group row" role="presentation" ondragover={(e) => e.preventDefault()}
        ondrop={(e) => kind === "collection" && dropOn(e, null)} title={kind === "collection" ? $t("library.drop_top") : ""}>
        <span class="eyebrow grow">{$t(title)}</span>
        {#if kind === "collection"}
          <button class="ghost small" title={$t("zotero.rdf_help")}
            onclick={async () => { if (await importZoteroRdf(selectedCollection())) { refresh(); refreshLabels(); } }}>{$t("zotero.rdf_button")}</button>
        {/if}
        <button class="ghost small" title={$t("library.new")} onclick={() => { newLabel = kind; newName = ""; }}>＋</button>
      </div>
      {#if newLabel === kind}
        <form onsubmit={createLabel}>
          <input type="text" class="new" bind:value={newName} use:focus placeholder={$t("library.name")} onblur={createLabel} />
        </form>
      {/if}
      {#if kind === "collection"}
        {#each tree.children.values() as node (node.path)}{@render branch(node, 0)}{/each}
        {#if archives}
          <div class="labelrow">
            <button class="ghost tw" aria-label={$t("library.fold")}
              onclick={() => { archivesOpen = !archivesOpen; keep("biblio.archivesOpen", archivesOpen); }}>{archivesOpen ? "▾" : "▸"}</button>
            <span class="item grow faint name">{$t("library.archives")}</span>
          </div>
          {#if archivesOpen}
            {#each archives.children.values() as node (node.path)}{@render branch(node, 1)}{/each}
          {/if}
        {/if}
      {:else}
        {#each list as label (label.id)}{@render labelRow(label, "#" + label.name, 0)}{/each}
      {/if}
      {#if !list.length && newLabel !== kind}<p class="small faint hint">{$t(`library.no_${kind}`)}</p>{/if}
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
        <span class="grow">{loading ? "…" : $t("library.count", { n: $fmt.number(total) })}</span>
        {#if !q}
          <select class="mini" value={sort} aria-label={$t("library.sort")} onchange={(e) => { sort = e.target.value; keep("biblio.sort", sort); }}>
            {#each ["added", "year", "author", "title", "journal", "study_type", "status", "rating"] as s}<option value={s}>{$t(`library.sort_${s}`)}</option>{/each}
          </select>
          <button class="ghost small" class:on={showFilters || activeFilters} onclick={() => (showFilters = !showFilters)}>
            {$t("library.filters")}{activeFilters ? ` (${activeFilters})` : ""}
          </button>
        {/if}
        <span class="modes" role="group" aria-label={$t("library.display")}>
          {#each [["cards", "▤"], ["compact", "☰"], ["table", "▦"]] as [mode, icon]}
            <button class="ghost small" class:on={display === mode} title={$t(`library.display_${mode}`)} aria-pressed={display === mode}
              onclick={() => { display = mode; keep("biblio.display", mode); }}>{icon}</button>
          {/each}
        </span>
      </div>
      {#if showFilters && !q}
        <div class="row wrap small extra">
          <label class="row">{$t("library.years")}
            <input class="year" type="number" placeholder={counts?.years?.[0] ?? ""} bind:value={extra.yearFrom} /> –
            <input class="year" type="number" placeholder={counts?.years?.[1] ?? ""} bind:value={extra.yearTo} />
          </label>
          <select class="mini" bind:value={extra.studyType} aria-label={$t("library.study_type")}>
            <option value="">{$t("library.any_study")}</option>
            {#each counts?.study_types ?? [] as st}<option value={st.value}>{$t(`study.${st.value}`, {}, st.value)} ({st.count})</option>{/each}
          </select>
          <select class="mini" bind:value={extra.pdf} aria-label="PDF">
            <option value="">{$t("library.pdf_any")}</option>
            <option value="yes">{$t("library.pdf_yes")}</option>
            <option value="no">{$t("library.pdf_no")}</option>
          </select>
          {#if activeFilters}<button class="ghost small" onclick={() => (extra = { yearFrom: "", yearTo: "", studyType: "", pdf: "" })}>{$t("library.clear_filters")}</button>{/if}
        </div>
      {/if}
    </div>
    {#if checked.size}
      <div class="batch row wrap">
        <strong class="small">{$t("library.selected", { n: checked.size })}</strong>
        <select onchange={(e) => { batchLabel(Number(e.target.value)); e.target.value = ""; }}>
          <option value="">{$t("library.add_to")}</option>
          {#each labels as label}<option value={label.id}>{label.kind === "tag" ? "#" : ""}{label.name}</option>{/each}
        </select>
        <select onchange={(e) => { batchUpdate({ read_status: e.target.value || null }); e.target.value = "-"; }}>
          <option value="-">{$t("library.set_status")}</option>
          {#each ["to_read", "to_reread", "read"] as s}<option value={s}>{$t(`read.${s}`)}</option>{/each}
          <option value="">{$t("library.unread")}</option>
        </select>
        <select onchange={(e) => { batchMove(Number(e.target.value)); e.target.value = ""; }}>
          <option value="">{$t("library.move_to")}</option>
          {#each collections as label}<option value={label.id}>{label.name}</option>{/each}
        </select>
        {#if filter.type === "label" && collections.some((c) => c.id === filter.value)}
          <button class="small" onclick={batchRemove}>{$t("library.remove_from")}</button>
        {/if}
        <button class="small" onclick={() => batchUpdate({ favorite: !items.filter((w) => checked.has(w.id)).every((w) => w.favorite) })}>♥ {$t("library.favorite")}</button>
        <select onchange={(e) => { exportWorks({ works: [...checked].map(String) }, e.target.value); e.target.value = ""; }}>
          <option value="">{$t("export.title")}…</option>
          {#each EXPORTS as [format, label]}<option value={format}>{label}</option>{/each}
        </select>
        <button class="small" onclick={() => sendToZotero({ works: [...checked].map(String) })}>Zotero</button>
        <button class="small" onclick={() => makePackage({ works: [...checked].map(String) })}>{$t("package.make")}</button>
        <button class="ghost small" onclick={() => (checked = new Set())}>{$t("common.cancel")}</button>
      </div>
    {/if}
    {#if display === "table" && !q}
      <div class="scroll tablewrap">
        <table>
          <thead>
            <tr>
              <th></th><th></th>
              {#each [["title", "title"], ["author", "author"], ["year", "year"], ["journal", "journal"], ["study_type", "study_type"], ["status", "status"], ["rating", "rating"], ["added", "added"]] as [col, key]}
                <th><button class="ghost small" class:on={sort === key} onclick={() => { sort = key; keep("biblio.sort", sort); }}>{$t(`library.col_${col}`)}{sort === key ? " ▾" : ""}</button></th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each items as work (work.id)}
              <tr class:on={work.citekey === current} draggable="true" ondragstart={(e) => drag(e, work)} onclick={() => open(work)}>
                <td><input type="checkbox" checked={checked.has(work.id)} onclick={(e) => e.stopPropagation()} onchange={() => toggle(work.id)} aria-label={$t("library.select")} /></td>
                <td><button class="ghost fav" class:on={work.favorite} aria-pressed={!!work.favorite} title={$t(work.favorite ? "library.unfavorite" : "library.favorite")}
                  onclick={(e) => { e.stopPropagation(); toggleFavorite(work); }}>♥</button></td>
                <td class="ttl">{#each work.labels.filter((id) => colored.has(id)) as id}<span class="tag-dot" style="--c: {COLORS[colored.get(id).color]}" title={colored.get(id).name}></span>{/each}{work.title}</td>
                <td>{work.first_author ?? ""}</td>
                <td>{work.year ?? ""}</td>
                <td class="journal">{work.container_title ?? ""}</td>
                <td>{work.study_type ? $t(`study.${work.study_type}`, {}, work.study_type) : ""}</td>
                <td>{work.read_status ? $t(`read.${work.read_status}`) : ""}</td>
                <td>{work.rating ? "★".repeat(work.rating) : ""}</td>
                <td class="faint">{work.created_at ? $fmt.date(work.created_at) : ""}</td>
              </tr>
            {/each}
          </tbody>
        </table>
        {#if !items.length && !loading}<p class="empty muted">{filter.type === "inbox" && total === 0 ? $t("library.inbox_empty") : $t("library.empty")}</p>{/if}
        {#if items.length < total}<p class="more"><button class="small" onclick={() => refresh(true)}>{$t("library.more")}</button></p>{/if}
      </div>
    {:else}
    <ul class="scroll">
      {#each items as work (work.id)}
        <li class:on={work.citekey === current} class:compact draggable="true" ondragstart={(e) => drag(e, work)}>
          <span class="side">
            <input type="checkbox" checked={checked.has(work.id)} onchange={() => toggle(work.id)} aria-label={$t("library.select")} />
            <button class="ghost fav" class:on={work.favorite} title={$t(work.favorite ? "library.unfavorite" : "library.favorite")}
              aria-pressed={!!work.favorite} onclick={() => toggleFavorite(work)}>♥</button>
          </span>
          <button class="row-btn" onclick={() => open(work)}>
            <span class="t title-serif">{#each (work.labels ?? []).filter((id) => colored.has(id)) as id}<span class="tag-dot" style="--c: {COLORS[colored.get(id).color]}" title={colored.get(id).name}></span>{/each}{work.title}</span>
            <span class="small muted">{work.first_author ?? ""}{work.year ? ` · ${work.year}` : ""}{work.container_title ? ` · ${work.container_title}` : ""}</span>
            {#if work.passages?.length}
              <span class="small snippet">{@html work.passages[0].snippet}</span>
            {/if}
            <span class="row badges" class:hidden={compact}>
              <Badges rec={work} compact />
              {#if work.read_status}<span class="chip">{$t(`read.${work.read_status}`)}</span>{/if}
              {#if work.rating}<span class="faint small">{"★".repeat(work.rating)}</span>{/if}
            </span>
          </button>
        </li>
      {:else}
        <li class="empty muted">{loading ? "" : q ? $t("library.no_match") : filter.type === "inbox" && total === 0 ? $t("library.inbox_empty") : $t("library.empty")}</li>
      {/each}
      {#if !q && items.length < total}
        <li class="more"><button class="small" onclick={() => refresh(true)}>{$t("library.more")}</button></li>
      {/if}
    </ul>
    {/if}
  </section>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="splitter" onpointerdown={(e) => resize(1, e)}></div>

  <section class="detail">
    {#if current}
      {#key `${current}:${detailVersion}`}
        <WorkDetail ref={current} {labels} {aiEnabled} onlabels={refreshLabels} onchange={() => refresh()} />
      {/key}
    {:else}
      <div class="placeholder muted">
        <p>{$t("library.pick")}</p>
        <p class="small faint">{$t("library.drop_hint")}</p>
        <p class="small faint">
          <span class="kbd">/</span> {$t("keys.search")} · <span class="kbd">j</span> <span class="kbd">k</span> {$t("keys.navigate")} ·
          <span class="kbd">x</span> {$t("keys.select")} ·
          <span class="kbd">f</span> {$t("keys.favorite")} · <span class="kbd">1</span> <span class="kbd">2</span> <span class="kbd">3</span> <span class="kbd">0</span> {$t("keys.status")}
        </p>
      </div>
    {/if}
  </section>
</div>

<style>
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
  li.compact { padding: 5px 12px; align-items: center; }
  li.compact .t { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  li.compact .row-btn { gap: 0; }
  li.compact .row-btn > .small { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 11.5px; }
  li.compact .side { flex-direction: row; gap: 6px; }
  li.compact input { margin-top: 0; }
  .hidden { display: none; }
  .mini { padding: 1px 4px; font-size: 12px; }
  .extra { margin-top: 6px; gap: 8px; }
  .extra .year { width: 64px; padding: 1px 4px; }
  .count .on { color: var(--accent); }
  .item { display: flex; gap: 6px; }
  .tw { padding: 0 2px; width: 18px; font-size: 12px; color: var(--muted); flex: none; }
  .group .ghost { margin-left: 4px; }
  .show { margin: 0 2px 0 4px; flex: none; }
  .first { margin-top: 0; }
  .dot { width: 10px; height: 10px; padding: 0; border-radius: 50%; flex: none; background: var(--c); border: 1px solid var(--c); margin: 0 3px; }
  .dot.none { border: 1px dashed var(--border); opacity: 0; }
  .labelrow:hover .dot.none { opacity: 1; }
  .pin { visibility: hidden; padding: 0 3px; font-size: 11px; filter: grayscale(1); opacity: 0.6; }
  .pin.on { visibility: visible; filter: none; opacity: 1; }
  .labelrow:hover .pin { visibility: visible; }
  .palette { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; padding: 4px 0 6px; }
  .swatch { width: 16px; height: 16px; padding: 0; border-radius: 50%; background: var(--c); border: 2px solid transparent; }
  .swatch.on { border-color: var(--text); }
  .tag-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--c); margin-right: 5px; vertical-align: 1px; }
  .modes { display: inline-flex; gap: 1px; }
  .tablewrap { flex: 1; overflow: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th { position: sticky; top: 0; background: var(--surface); text-align: left; border-bottom: 1px solid var(--border); padding: 2px 4px; z-index: 1; }
  th button { font-weight: 600; padding: 2px 4px; white-space: nowrap; }
  td { padding: 4px 6px; border-bottom: 1px solid var(--border); vertical-align: top; }
  td.ttl { font-weight: 600; min-width: 240px; }
  td.journal { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  tr { cursor: pointer; }
  tr:hover { background: var(--surface-2); }
  tr.on { background: var(--accent-soft); }
  .shown { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; padding: 6px 8px; margin-bottom: 6px;
    border-radius: 8px; background: var(--accent-soft); color: var(--accent); }
  .side { display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .fav { padding: 0 2px; font-size: 14px; line-height: 1; color: var(--faint); opacity: 0.35; }
  .fav:hover { opacity: 1; }
  .fav.on { color: var(--danger); opacity: 1; }
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
