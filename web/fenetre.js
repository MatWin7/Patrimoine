// Actions « Fenêtre » de la carte Aide de Réglages (D-127) : les actions de l'ancienne barre de menu de
// la fenêtre (blanche en thème sombre). Seulement dans la fenêtre de l'app, ouverte avec
// « ?fenetre » ; le serveur confirme qu'une fenêtre est bien ouverte. Chaque action est une
// route fixe de l'API locale (aucun pont Python exposé à la page, aucun paramètre envoyé).
// Indépendant de app.js : il reste utilisable sur l'écran de choix du profil ou si app.js plante.
(() => {
  if (!new URLSearchParams(location.search).has("fenetre")) return;
  const $ = (id) => document.getElementById(id);
  const panel = $("window-actions");
  const error = $("window-error");
  const ROUTES = { browser: "/api/window/browser", folder: "/api/window/folder", zoom: "/api/window/zoom" };
  const FAILED = {
    browser: "Le navigateur n'a pas pu être ouvert.",
    folder: "Le dossier des données n'a pas pu être ouvert.",
    zoom: "Le zoom n'a pas pu être remis à 100 %.",
  };

  function showError(text) {
    error.textContent = text;
    error.hidden = !text;
  }

  async function run(action) {
    if (action === "reload") {
      location.reload();
      return;
    }
    showError("");
    try {
      const res = await fetch(ROUTES[action], { method: "POST" });
      const data = res.ok ? await res.json() : {};
      if (!data.done) throw new Error();
    } catch {
      showError(FAILED[action]);
    }
  }

  fetch("/api/window")
    .then((res) => (res.ok ? res.json() : {}))
    .then((info) => {
      if (!info.window) return;
      panel.querySelector('[data-window="zoom"]').hidden = !info.zoom;
      panel.hidden = false;
      panel.querySelectorAll("[data-window]").forEach((button) => {
        button.addEventListener("click", () => run(button.dataset.window));
      });
    })
    .catch(() => {}); // serveur injoignable : pas d'actions, la page le signale déjà
})();
