<script>
  // Liste compacte de notices trouvées en ligne (nouveautés de veille, articles proches), avec ajout.
  import Badges from "./Badges.svelte";
  import { addRecord } from "./actions.js";
  import { authorsShort, doiUrl, sourceLine } from "./format.js";
  import { t } from "./i18n.js";
  import { toast } from "./toast.js";

  let { records = $bindable(), question = null } = $props();
  let adding = $state({});

  async function add(i) {
    adding[i] = true;
    try {
      records[i] = { ...records[i], in_library: await addRecord(records[i], question) };
    } catch (e) {
      toast(e.message, "error");
    } finally {
      adding[i] = false;
    }
  }
</script>

<ul>
  {#each records as rec, i (i)}
    <li>
      <div class="grow">
        <div class="title-serif t">
          {#if doiUrl(rec)}<a href={doiUrl(rec)} target="_blank" rel="noreferrer">{rec.title}</a>{:else}{rec.title}{/if}
        </div>
        <div class="small muted">{authorsShort(rec)}{#if sourceLine(rec)} — <em>{sourceLine(rec)}</em>{/if}</div>
        <Badges {rec} compact />
      </div>
      {#if rec.in_library}
        <a class="chip lib small" href="#/library/{rec.in_library}">✓ {$t("source.open_in_library")}</a>
      {:else}
        <button class="small" disabled={adding[i]} onclick={() => add(i)}>{adding[i] ? $t("source.adding") : `＋ ${$t("source.add")}`}</button>
      {/if}
    </li>
  {/each}
</ul>

<style>
  ul { list-style: none; margin: 0; padding: 0; }
  li { display: flex; gap: 12px; align-items: flex-start; padding: 9px 0; border-bottom: 1px solid var(--border); }
  li:last-child { border-bottom: none; }
  .t { font-size: 14.5px; font-weight: 600; line-height: 1.35; }
  .t a { color: var(--text); }
  .lib { background: var(--accent-soft); color: var(--accent); white-space: nowrap; }
</style>
