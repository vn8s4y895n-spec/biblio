<script>
  // Badges d'une notice : statut de revue (liste Sorbonne), PDF ou accès libre, type d'étude, effectif.
  import { t } from "./i18n.js";

  let { rec, compact = false } = $props();
  const status = $derived(typeof rec.journal_status === "string" ? rec.journal_status : rec.journal_status?.status);
  const oa = $derived(rec.is_oa || rec.oa_pdf_url || rec.has_pdf);
</script>

<span class="badges">
  {#if rec.retracted}
    <span class="chip retracted" title={$t("watch.retracted_help")}>⚠ {$t("watch.retracted")}</span>
  {/if}
  {#if status}
    <span class="chip status {status}" title={$t(`status.${status}.help`)}>
      <span class="dot"></span>{$t(`status.${status}.label`)}
    </span>
  {/if}
  {#if rec.has_pdf}
    <span class="chip pdf">PDF</span>
  {:else if oa}
    <span class="chip oa">{$t("badge.oa")}</span>
  {/if}
  {#if !compact && rec.study_type}
    <span class="chip">{$t(`study.${rec.study_type}`, {}, rec.study_type)}</span>
  {/if}
  {#if !compact && rec.sample_size}
    <span class="chip">n = {rec.sample_size.toLocaleString()}</span>
  {/if}
</span>

<style>
  .badges { display: inline-flex; flex-wrap: wrap; gap: 4px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .status.recommended { background: var(--ok-soft); color: var(--ok); }
  .status.withdrawn { background: var(--warn-soft); color: var(--warn); }
  .status.preprint { background: var(--violet-soft); color: var(--violet); }
  .status.not_listed, .status.unidentified, .status.no_list { background: var(--grey-soft); color: var(--muted); }
  .retracted { background: color-mix(in srgb, var(--danger) 14%, transparent); color: var(--danger); font-weight: 650; }
  .oa, .pdf { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
</style>
