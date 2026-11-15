// Thème clair ou sombre (D-133). Chargé dans l'en-tête, avant le premier affichage, pour qu'une
// page sombre ne s'ouvre pas sur un éclair blanc. Le choix (clair, sombre, ou celui du système)
// est un réglage d'affichage gardé dans ce navigateur, commun à tous les profils.
(() => {
  const KEY = "patrimoine.theme";
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const read = () => {
    try {
      const saved = localStorage.getItem(KEY);
      return saved === "clair" || saved === "sombre" ? saved : "auto";
    } catch {
      return "auto"; // stockage refusé (navigation privée stricte) : le thème du système
    }
  };
  function apply() {
    const choice = read();
    const dark = choice === "sombre" || (choice === "auto" && media.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.dispatchEvent(new CustomEvent("themechange"));
  }
  media.addEventListener("change", apply);
  window.patrimoineTheme = {
    get: read,
    set(choice) {
      try {
        if (choice === "auto") localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, choice);
      } catch {
        // rien à garder : le thème s'applique quand même à cette page
      }
      apply();
    },
  };
  apply();
})();
