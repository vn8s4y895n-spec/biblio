<script>
  import { onMount } from "svelte";
  import { copy, download } from "./lib/actions.js";
  import { api } from "./lib/api.js";
  import { fmt, t } from "./lib/i18n.js";
  import { toast } from "./lib/toast.js";

  let info = $state(null);
  let zotero = $state(null);
  let hooks = $state({ events: [], items: [] });
  let backups = $state([]);
  let newHook = $state({ url: "", events: ["search.saved"] });
  let shown = $state({}); // secrets dévoilés
  let backingUp = $state(false);

  onMount(async () => {
    [info, zotero, hooks, backups] = await Promise.all([api("/connectors"), api("/zotero"), api("/webhooks"), api("/backups")]);
  });

  const json = (value) => JSON.stringify(value, null, 2);

  async function addHook() {
    try {
      await api("/webhooks", { method: "POST", body: newHook });
      newHook = { url: "", events: ["search.saved"] };
      hooks = await api("/webhooks");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function patchHook(hook, body) {
    await api(`/webhooks/${hook.id}`, { method: "PATCH", body });
    hooks = await api("/webhooks");
  }

  async function testHook(hook) {
    const out = await api(`/webhooks/${hook.id}/test`, { method: "POST" });
    toast(out.error ? $t("connectors.hook_failed", { error: out.error }) : $t("connectors.hook_ok", { status: out.status }),
      out.error ? "error" : "ok", 6000);
    hooks = await api("/webhooks");
  }

  async function removeHook(hook) {
    if (!confirm($t("connectors.hook_delete", { url: hook.url }))) return;
    await api(`/webhooks/${hook.id}`, { method: "DELETE" });
    hooks = await api("/webhooks");
  }

  async function backupNow() {
    backingUp = true;
    try {
      const out = await api("/backups", { method: "POST" });
      backups = await api("/backups");
      toast($t("connectors.backup_done", { name: out.name }), "ok", 6000);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      backingUp = false;
    }
  }

  function toggleEvent(list, event) {
    return list.includes(event) ? list.filter((e) => e !== event) : [...list, event];
  }
</script>

<section class="card">
  <h2>{$t("connectors.claude")}</h2>
  <p class="muted small">{$t("connectors.claude_help")}</p>
  <div class="field">
    <span>{$t("connectors.desktop")}<small class="faint">{$t("connectors.desktop_help")}</small></span>
    <button class="primary" onclick={() => download("/connectors/claude/biblio.mcpb", "biblio.mcpb")}>{$t("connectors.mcpb")}</button>
  </div>
  <div class="field">
    <span>{$t("connectors.skill")}<small class="faint">{$t("connectors.skill_help")}</small></span>
    <button onclick={() => download("/connectors/claude/biblio-skill.zip", "biblio-skill.zip")}>{$t("connectors.skill_download")}</button>
  </div>
  {#if info}
    <div class="field">
      <span>Claude Code<small class="faint">{$t("connectors.code_help")}</small></span>
      <button onclick={() => copy(info.claude_code)}>{$t("connectors.copy_command")}</button>
    </div>
    <code class="block">{info.claude_code}</code>
  {/if}
</section>

<section class="card">
  <h2>{$t("connectors.other")}</h2>
  <p class="muted small">{$t("connectors.other_help")}</p>
  {#if info}
    <div class="field">
      <span>stdio<small class="faint">{$t("connectors.stdio_help")}</small></span>
      <button onclick={() => copy(json(info.stdio))}>{$t("connectors.copy_config")}</button>
    </div>
    <pre class="block">{json(info.stdio)}</pre>
    <div class="field">
      <span>HTTP<small class="faint">{$t("connectors.http_help", { command: info.http_command })}</small></span>
      <button onclick={() => copy(json(info.http))}>{$t("connectors.copy_config")}</button>
    </div>
    <p class="small warn">{$t("connectors.token_warning")}</p>
  {/if}
  <div class="field">
    <span>Zotero<small class="faint">{$t("settings.zotero_help")}</small></span>
    <span class="status">
      {#if zotero?.running}
        <span class="chip ok">✓ {$t("settings.zotero_on", { where: zotero.collection ?? zotero.library })}</span>
      {:else}
        <span class="chip">{$t("settings.zotero_off")}</span>
      {/if}
      <button class="small" onclick={async () => (zotero = await api("/zotero"))}>{$t("settings.check")}</button>
    </span>
  </div>
</section>

<section class="card">
  <h2>{$t("connectors.webhooks")}</h2>
  <p class="muted small">{$t("connectors.webhooks_help")}</p>
  {#each hooks.items as hook (hook.id)}
    <div class="hook" class:off={!hook.active}>
      <div class="row wrap">
        <span class="mono grow">{hook.url}</span>
        <label class="small"><input type="checkbox" checked={hook.active} onchange={(e) => patchHook(hook, { active: e.target.checked })} /> {$t("connectors.active")}</label>
        <button class="small" onclick={() => testHook(hook)}>{$t("connectors.test")}</button>
        <button class="small ghost" title={$t("connectors.delete")} onclick={() => removeHook(hook)}>✕</button>
      </div>
      <div class="row wrap small">
        {#each hooks.events as event}
          <label><input type="checkbox" checked={hook.events.includes(event)}
            onchange={() => patchHook(hook, { events: toggleEvent(hook.events, event) })} /> {$t(`connectors.event.${event}`)}</label>
        {/each}
      </div>
      <div class="row wrap small faint">
        <span>{$t("connectors.secret")} :</span>
        {#if shown[hook.id]}<span class="mono">{hook.secret}</span>{:else}<span>••••••</span>{/if}
        <button class="ghost small" onclick={() => (shown[hook.id] = !shown[hook.id])}>{shown[hook.id] ? $t("connectors.hide") : $t("connectors.show")}</button>
        <button class="ghost small" onclick={() => copy(hook.secret)}>{$t("connectors.copy")}</button>
        {#if hook.last_at}
          <span class="grow right" class:warn={hook.last_error}>
            {$t("connectors.last", { date: $fmt.date(hook.last_at), result: hook.last_error ?? hook.last_status })}
          </span>
        {/if}
      </div>
    </div>
  {/each}
  <form class="hook new" onsubmit={(e) => { e.preventDefault(); addHook(); }}>
    <div class="row">
      <input class="grow" type="url" required placeholder="https://…" bind:value={newHook.url} aria-label={$t("connectors.url")} />
      <button type="submit" disabled={!newHook.url || !newHook.events.length}>{$t("connectors.add")}</button>
    </div>
    <div class="row wrap small">
      {#each hooks.events as event}
        <label><input type="checkbox" checked={newHook.events.includes(event)}
          onchange={() => (newHook.events = toggleEvent(newHook.events, event))} /> {$t(`connectors.event.${event}`)}</label>
      {/each}
    </div>
  </form>
</section>

<section class="card">
  <h2>{$t("connectors.backups")}</h2>
  <p class="muted small">{$t("connectors.backups_help")}</p>
  <div class="row wrap">
    <button class="primary" disabled={backingUp} onclick={backupNow}>{backingUp ? "…" : $t("connectors.backup_now")}</button>
    <button onclick={() => download("/export/full", "biblio-export.zip", "POST")}>{$t("connectors.export_full")}</button>
  </div>
  {#each backups.slice(0, 7) as b (b.name)}
    <div class="row small"><span class="mono grow">{b.name}</span><span class="faint">{(b.size / 1e6).toFixed(1)} Mo</span></div>
  {/each}
  <p class="small faint">{$t("connectors.restore_help")}</p>
</section>

<style>
  section { padding: 16px 18px; display: grid; gap: 10px; }
  section p { margin: 0; }
  h2 { margin: 0 0 4px; font-size: 15px; }
  .field { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .field > span:first-child { display: grid; flex: 1; }
  .field small { font-size: 12px; }
  .status { display: flex; align-items: center; gap: 10px; }
  .block { display: block; font-family: var(--mono); font-size: 12px; background: var(--surface-2, var(--bg)); border: 1px solid var(--border);
    border-radius: 7px; padding: 8px 10px; margin: 0; white-space: pre-wrap; word-break: break-all; }
  .mono { font-family: var(--mono); font-size: 12.5px; word-break: break-all; }
  .hook { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: grid; gap: 6px; }
  .hook.off { opacity: 0.6; }
  .hook.new { border-style: dashed; }
  .right { text-align: right; }
  .ok { background: var(--ok-soft); color: var(--ok); }
  .warn { color: var(--warn); }
  label { display: inline-flex; align-items: center; gap: 4px; }
</style>
