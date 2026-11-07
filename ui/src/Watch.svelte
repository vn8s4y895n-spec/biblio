<script>
  import { onMount } from "svelte";
  import RecordList from "./lib/RecordList.svelte";
  import { api } from "./lib/api.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let { onchange } = $props(); // le nombre d'alertes non lues a changé

  let alerts = $state(null);
  let searches = $state([]);
  let checking = $state(false);
  let running = $state({});
  let open = $state({}); // alertes de nouveaux résultats dépliées

  const INTERVALS = [[0, "watch.never"], [1, "watch.daily"], [7, "watch.weekly"], [30, "watch.monthly"]];
  const TONE = { retraction: "danger", concern: "warn", correction: "info", published: "accent", new_results: "accent" };

  onMount(refresh);

  async function refresh() {
    [alerts, searches] = await Promise.all([api("/alerts"), api("/searches")]);
    searches = searches.filter((s) => s.scope === "online");
    onchange?.(alerts.unseen);
  }

  async function seen(ids) {
    await api("/alerts/seen", { method: "POST", body: ids ? { ids } : {} });
    refresh();
  }

  async function checkLibrary() {
    checking = true;
    try {
      const out = await api("/watch/library", { method: "POST" });
      const n = Object.values(out.alerts).reduce((a, b) => a + b, 0);
      toast($t("watch.checked", { n: out.checked, alerts: n }), n ? "warn" : "ok", 6000);
      refresh();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      checking = false;
    }
  }

  async function setInterval_(search, days) {
    await api(`/searches/${search.id}/watch`, { method: "PUT", body: { days: days || null } });
    refresh();
  }

  async function runNow(search) {
    running[search.id] = true;
    try {
      const out = await api(`/searches/${search.id}/run`, { method: "POST" });
      toast($t("watch.new_found", { n: out.new.length }), out.new.length ? "ok" : "info");
      refresh();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      running[search.id] = false;
    }
  }

  async function supersede(alert) {
    try {
      const work = await api(`/works/${alert.work_id}/supersede`, { method: "POST", body: { doi: alert.data.notice_doi } });
      toast($t("watch.superseded", { key: work.citekey }), "ok");
      await refresh(); // met à jour le compteur d'alertes de l'en-tête
      location.hash = `#/library/${work.citekey}`;
    } catch (e) {
      toast(e.message, "error", 8000);
    }
  }
</script>

<div class="watch scroll">
  <div class="inner">
    <header class="row wrap">
      <h1 class="grow">{$t("nav.watch")}</h1>
      {#if alerts?.unseen}<button onclick={() => seen(null)}>{$t("watch.all_seen")}</button>{/if}
    </header>

    <section>
      <div class="row">
        <h2 class="grow">{$t("watch.alerts")}</h2>
        <span class="small faint">{$t("watch.library_checked", { date: alerts?.library_check ? $fmt.date(alerts.library_check) : "—" })}</span>
        <button class="small" disabled={checking} onclick={checkLibrary}>{checking ? "…" : $t("watch.check_library")}</button>
      </div>
      {#if alerts && !alerts.items.length}
        <p class="muted empty">{$t("watch.no_alerts")}</p>
      {/if}
      {#each alerts?.items ?? [] as alert (alert.id)}
        <article class="card alert {TONE[alert.kind]}" class:seen={alert.seen_at}>
          <div class="row">
            <span class="kind">{$t(`watch.kind.${alert.kind}`)}</span>
            <span class="small faint grow">{$fmt.date(alert.created_at)}</span>
            {#if !alert.seen_at}<button class="ghost small" title={$t("watch.mark_seen")} onclick={() => seen([alert.id])}>✓</button>{/if}
          </div>
          {#if alert.kind === "new_results"}
            <p>
              {$t("watch.new_results", { n: alert.data.count })}
              <a href="#/search/{alert.search_id}">« {alert.question} »</a>
            </p>
            <button class="ghost small" onclick={() => (open[alert.id] = !open[alert.id])}>
              {open[alert.id] ? "▾" : "▸"} {$t("watch.show_results")}
            </button>
            {#if open[alert.id]}
              <RecordList bind:records={alert.data.records} question={alert.question} />
            {/if}
          {:else}
            <p>
              <a href="#/library/{alert.citekey}" class="title-serif">{alert.title}</a>
              {#if alert.year}<span class="small muted"> ({alert.year})</span>{/if}
            </p>
            <p class="small muted">
              {$t(`watch.notice.${alert.kind}`)}
              {#if alert.data.date} · {$fmt.date(alert.data.date)}{/if}
              {#if alert.data.source === "retraction-watch"} · Retraction Watch{/if}
              · <a href="https://doi.org/{alert.data.notice_doi}" target="_blank" rel="noreferrer">{alert.data.notice_doi} ↗</a>
            </p>
            {#if alert.kind === "published"}
              <button class="small primary" onclick={() => supersede(alert)}>{$t("watch.supersede")}</button>
            {/if}
          {/if}
        </article>
      {/each}
    </section>

    <section>
      <h2>{$t("watch.followed_searches")}</h2>
      {#if !searches.length}
        <p class="muted empty">{$t("watch.no_searches")}</p>
      {/if}
      {#each searches as s (s.id)}
        <div class="search row wrap">
          <a class="grow" href="#/search/{s.id}">{s.question}</a>
          <span class="small faint">{s.watch_days && s.last_run ? $t("watch.last_run", { date: $fmt.date(s.last_run) }) : ""}</span>
          <select value={s.watch_days ?? 0} onchange={(e) => setInterval_(s, Number(e.target.value))} aria-label={$t("watch.interval")}>
            {#each INTERVALS as [days, key]}<option value={days}>{$t(key)}</option>{/each}
          </select>
          {#if s.watch_days}
            <button class="small" disabled={running[s.id]} onclick={() => runNow(s)}>{running[s.id] ? "…" : $t("watch.run_now")}</button>
          {/if}
        </div>
      {/each}
    </section>
  </div>
</div>

<style>
  .watch { height: 100%; }
  .inner { max-width: 820px; margin: 0 auto; padding: 28px 20px 60px; display: grid; gap: 22px; }
  h1 { margin: 0; font-size: 22px; }
  h2 { margin: 0 0 8px; font-size: 15px; }
  section { display: grid; gap: 10px; }
  .empty { margin: 0; }
  .alert { padding: 12px 16px; border-left: 4px solid var(--accent); display: grid; gap: 4px; }
  .alert p { margin: 0; }
  .alert > button { justify-self: start; }
  .alert.danger { border-left-color: var(--danger); }
  .alert.warn { border-left-color: var(--warn); }
  .alert.info { border-left-color: var(--faint); }
  .alert.seen { opacity: 0.6; }
  .kind { font-size: 11.5px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; }
  .danger .kind { color: var(--danger); }
  .warn .kind { color: var(--warn); }
  .accent .kind { color: var(--accent); }
  .search { padding: 8px 0; border-bottom: 1px solid var(--border); }
  select { padding: 3px 8px; }
</style>
