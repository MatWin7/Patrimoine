// Diagnostic à copier dans un signalement (D-132). Chargé avant les autres scripts, sans app.js :
// il relève les erreurs de la page dès le chargement et reste utilisable sur l'écran de choix du
// profil ou si app.js plante. Le texte vient de /api/diagnostic (sans montant, nom ni chemin) ;
// la page y ajoute son moteur, sa taille et ses dernières erreurs. Rien n'est envoyé : le texte
// est montré, copié dans le presse-papiers, et l'utilisateur le colle lui-même.
(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_ERRORS = 5;
  const pageErrors = [];
  let errorCount = 0;

  // Messages écrits par le moteur JavaScript (« Cannot read properties of undefined (reading
  // 'x')») : ils parlent du code. Ceux de l'app (Error) reprennent souvent une réponse du
  // serveur : jamais recopiés. Dans les premiers, un texte entre guillemets qui n'est pas un nom
  // de propriété en minuscules (morceau de réponse, ticker « 'AAPL' », nom propre) et les nombres
  // longs ou à virgule sont masqués.
  const ENGINE_ERRORS = new Set(["TypeError", "ReferenceError", "RangeError", "SyntaxError", "URIError"]);
  const scrub = (text) =>
    String(text)
      .replace(/(["'`])(.*?)\1/g, (all, quote, inner) => (/^[a-z_$][\w$]*$/.test(inner) ? all : `${quote}…${quote}`))
      .replace(/\d+[.,]\d+|\d{4,}/g, "…")
      .slice(0, 160);

  // « app.js:120:7 » : le nom du fichier de la page, jamais une adresse complète.
  function place(file, line, column) {
    const name = /\/([\w.-]+\.js)(?:[?#].*)?$/.exec(file ?? "")?.[1];
    if (!name) return "script inconnu";
    return [name, line, column].filter(Boolean).join(":");
  }

  function placeFromStack(stack) {
    const match = /\/([\w.-]+\.js)(?:\?[^:]*)?:(\d+):(\d+)/.exec(stack ?? "");
    return match ? `${match[1]}:${match[2]}:${match[3]}` : "script inconnu";
  }

  function remember(kind, error, where) {
    errorCount += 1;
    const name = error?.name ?? "Erreur";
    const message = ENGINE_ERRORS.has(name) && error?.message ? ` : ${scrub(error.message)}` : "";
    const at = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    pageErrors.push(`  ${at} ${kind} ${name}${message} (${where})`);
    if (pageErrors.length > MAX_ERRORS) pageErrors.shift();
  }

  window.addEventListener("error", (event) => {
    if (!event.error && !event.message) return; // image ou feuille de style introuvable
    remember("erreur", event.error, place(event.filename, event.lineno, event.colno));
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    remember("promesse rejetée", reason instanceof Error ? reason : null, placeFromStack(reason?.stack));
  });

  function pageLines() {
    const dark = document.documentElement.dataset.theme === "dark" ? "sombre" : "clair";
    const scale = String(Math.round(devicePixelRatio * 100) / 100).replace(".", ",");
    const lines = [
      `Page : ${navigator.userAgent}`,
      `Affichage de la page : ${innerWidth} × ${innerHeight} px, échelle ${scale}, ${navigator.language}, thème ${dark}, page ${location.hash || "#apercu"}`,
      "Erreurs de la page :",
    ];
    if (!errorCount) lines.push("  aucune");
    else lines.push(...pageErrors);
    if (errorCount > pageErrors.length) lines.push(`  (${errorCount} en tout, les ${pageErrors.length} dernières ici)`);
    return lines;
  }

  // --- Fenêtre « Diagnostic » ---------------------------------------------------------------

  const dialog = $("dlg-diagnostic");
  const text = $("diagnostic-text");
  const state = $("diagnostic-state");
  const report = $("diagnostic-report");

  function say(message, kind) {
    state.textContent = message;
    state.className = `small ${kind ?? ""}`.trim();
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text.value);
    } catch {
      // Presse-papiers refusé par le moteur : copie classique du texte sélectionné.
      text.focus();
      text.select();
      if (!document.execCommand("copy")) {
        say("Copie refusée : sélectionne le texte (Ctrl + A) puis copie-le (Ctrl + C).", "warn");
        return;
      }
    }
    say("Copié. Colle-le dans ton signalement.", "ok");
  }

  async function open() {
    let data;
    try {
      const res = await fetch("/api/diagnostic", { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error();
      data = await res.json();
    } catch {
      data = { text: "Patrimoine : serveur injoignable, diagnostic du serveur indisponible." };
    }
    text.value = `${data.text}\n${pageLines().join("\n")}\n`;
    report.hidden = !data.report_url;
    if (data.report_url) report.href = data.report_url;
    say("");
    if (!dialog.open) dialog.showModal();
    text.scrollTop = 0;
    await copy();
  }

  for (const button of document.querySelectorAll("[data-diagnostic-open]")) button.addEventListener("click", open);
  $("diagnostic-copy").addEventListener("click", copy);
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
})();
