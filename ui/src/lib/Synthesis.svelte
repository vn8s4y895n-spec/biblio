<script>
  // Réponse rédigée par l'IA : numéros de citation cliquables ; après vérification, les phrases que rien n'appuie
  // dans les sources citées sont soulignées, avec la raison au survol.
  import { t } from "./i18n.js";

  let { synthesis, oncite } = $props();
  const CITATION = /\[(\d+(?:\s*[-–,]\s*\d+)*)\]/g;

  function numbers(group) {
    return group.split(",").flatMap((part) => {
      const range = part.trim().match(/^(\d+)\s*[-–]\s*(\d+)$/);
      if (!range) return [Number(part.trim())];
      const out = [];
      for (let n = Number(range[1]); n <= Number(range[2]) && out.length < 10; n++) out.push(n);
      return out;
    });
  }

  // Morceaux de texte et citations, pour les afficher sans HTML.
  function pieces(text) {
    text = text.replace(/\*{1,2}([^*\n]+)\*{1,2}/g, "$1"); // mise en forme Markdown éventuelle du modèle
    const out = [];
    let last = 0;
    for (const m of text.matchAll(CITATION)) {
      if (m.index > last) out.push({ text: text.slice(last, m.index) });
      for (const n of numbers(m[1])) out.push({ cite: n });
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ text: text.slice(last) });
    return out;
  }

  const paragraphs = $derived(
    synthesis.sentences
      ? [synthesis.sentences]
      : (synthesis.text ?? "").split(/\n{2,}/).map((p) => [{ text: p, status: "pending" }]),
  );
</script>

<div class="synthesis">
  {#each paragraphs as sentences}
    <p>
      {#each sentences as s}
        <span class="sentence {s.status}" title={s.status && !["ok", "pending"].includes(s.status) ? $t(`ai.check.${s.status}`) : null}>
          {#each pieces(s.text) as piece}
            {#if piece.cite}<button class="cite" onclick={() => oncite(piece.cite)}>{piece.cite}</button>{:else}{piece.text}{/if}
          {/each}
        </span>{" "}
      {/each}
    </p>
  {/each}
  {#if synthesis.streaming}<span class="caret">▍</span>{/if}
</div>

<style>
  .synthesis p { margin: 0 0 10px; font-family: var(--serif); font-size: 16px; line-height: 1.6; white-space: pre-line; }
  .sentence.weak, .sentence.unsourced, .sentence.invalid {
    text-decoration: underline wavy var(--warn); text-decoration-thickness: 1px; text-underline-offset: 4px; cursor: help;
  }
  .sentence.invalid { text-decoration-color: var(--danger); }
  .cite {
    font: 650 11px var(--font); padding: 0 6px; border-radius: 5px; vertical-align: 2px; margin-left: 2px;
    background: var(--accent-soft); color: var(--accent); border: none;
  }
  .cite:hover { background: var(--accent); color: var(--accent-text); }
  .caret { animation: blink 1s steps(2) infinite; color: var(--accent); }
  @keyframes blink { 50% { opacity: 0; } }
</style>
