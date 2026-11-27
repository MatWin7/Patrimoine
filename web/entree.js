// Touche Entrée dans un champ de saisie (D-150) : elle valide ce qui vient d'être écrit et passe
// au champ suivant du formulaire ; sur le dernier champ, elle envoie le formulaire (comme
// « Enregistrer »). Hors formulaire (seuil d'alerte, dates de la Performance, recherche), elle
// valide en quittant le champ. Les zones de texte sur plusieurs lignes gardent leur retour à
// la ligne. Chargé avant profiles.js : l'écran de choix du profil en profite aussi. Aucun
// accès réseau, aucune donnée lue.
(() => {
  // Champs où l'on écrit une ligne (les cases, boutons, fichiers et curseurs gardent Entrée).
  const WRITABLE = new Set(["text", "search", "number", "password", "email", "tel", "url", "date", "month", "time", "datetime-local"]);

  const isField = (el) => {
    if (!(el instanceof HTMLElement) || el.disabled || el.readOnly || el.hidden) return false;
    if (el.getClientRects().length === 0) return false; // masqué (hidden, display: none)
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
    return el instanceof HTMLInputElement && (WRITABLE.has(el.type) || el.type === "checkbox");
  };

  document.addEventListener("keydown", (e) => {
    const input = e.target;
    if (e.key !== "Enter" || e.isComposing || e.defaultPrevented) return;
    if (e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;
    if (!(input instanceof HTMLInputElement) || !WRITABLE.has(input.type)) return;
    if (input.dataset.enter === "native") return;
    const form = input.form;
    if (!form) {
      e.preventDefault();
      input.blur();
      return;
    }
    e.preventDefault();
    const fields = [...form.elements].filter((el) => el === input || isField(el));
    const next = fields[fields.indexOf(input) + 1];
    if (next) {
      next.focus();
      if (next instanceof HTMLInputElement && next.type !== "checkbox") next.select?.(); // select() absent des champs date
    } else {
      input.blur();
      form.requestSubmit();
    }
  });
})();
