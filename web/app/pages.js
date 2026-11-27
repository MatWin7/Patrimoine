// --- Pages (onglets) ---------------------------------------------------------------
// Une seule page visible à la fois ; l'adresse (#performance...) garde la page au
// rechargement et permet le bouton « Précédent » du navigateur.

const PAGES = {
  visualisations: refreshVisualisations, performance: refreshBenchmark, // la comparaison vit dans Performance (D-133)
  projection: () => Promise.all([refreshProjection(), refreshTargets()]),
  revenus: () => refreshIncome(), fiscalite: () => refreshTax(), crypto: () => refreshAave().finally(refreshRewards).finally(refreshBorrows),
};
// Anciennes adresses (favoris) : Calendrier, Allocation cible et Comparaison ont rejoint d'autres pages.
const PAGE_ALIASES = { calendrier: "visualisations", cible: "projection", comparaison: "performance" };
// Nom affiché d'un onglet (sans son icône ni sa pastille) ; la roue dentée n'a que son libellé.
const tabName = (a) => a.querySelector(".tab-label")?.textContent ?? a.getAttribute("aria-label") ?? "";
let currentPage = "apercu";

function showPage() {
  stopArranging(); // avant de lire le nom de l'onglet (les cases du réglage en font partie)
  const asked = location.hash.slice(1);
  if (PAGE_ALIASES[asked]) {
    history.replaceState(null, "", `#${PAGE_ALIASES[asked]}`);
    return showPage();
  }
  // Sans adresse : le premier onglet affiché, dans l'ordre choisi.
  const name = asked || $("tabs").querySelector("[data-tab]:not([hidden])").dataset.tab;
  currentPage = document.querySelector(`[data-page="${name}"]`) ? name : "apercu";
  for (const page of document.querySelectorAll("[data-page]")) page.hidden = page.dataset.page !== currentPage;
  for (const tab of document.querySelectorAll("[data-tab]")) {
    tab.classList.toggle("active", tab.dataset.tab === currentPage);
    tab.setAttribute("aria-current", tab.dataset.tab === currentPage ? "page" : "false");
  }
  const tab = document.querySelector(`[data-tab="${currentPage}"]`);
  $("current-page").textContent = tab ? tabName(tab) : "";
  // Fenêtre étroite : la barre d'onglets défile (D-115), l'onglet ouvert reste visible.
  const bar = $("tabs");
  if (tab && bar.scrollWidth > bar.clientWidth) bar.scrollLeft = tab.offsetLeft - bar.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2;
  closeMenu();
  renderFilterButton();
  resizeCharts();
  loadPage();
}

// Un graphique dessiné dans une page cachée (ou une carte repliée) a une taille nulle : on le recalcule.
function resizeCharts() {
  for (const canvas of document.querySelectorAll(`[data-page="${currentPage}"] canvas`)) charts[canvas.id]?.resize();
}

// --- Disposition des cartes (D-070) ------------------------------------------------------
// Chaque page range ses cartes (data-card) dans l'ordre choisi, gardé dans ce navigateur comme
// les autres réglages d'affichage. Les éléments fixes (Total, filtre, bandeaux) restent en haut.
// Mode « Réorganiser » : cartes repliées sur leur titre, déplaçables à la souris, au doigt ou
// au clavier ; hors de ce mode, rien ne bouge par accident.
const LAYOUT_KEY = PREF + "disposition";
const layout = loadPref(LAYOUT_KEY, {});
const defaultLayout = {};
const pageCards = (page) => [...page.children].filter((el) => el.matches(".card[data-card]"));

function applyLayout(page) {
  const name = page.dataset.page;
  const saved = layout[name] ?? defaultLayout[name];
  const cards = pageCards(page);
  const rank = (card) => {
    const i = saved.indexOf(card.dataset.card);
    return i < 0 ? saved.length + defaultLayout[name].indexOf(card.dataset.card) : i; // carte nouvelle : à la fin
  };
  const bar = page.querySelector(":scope > .layout-bar");
  for (const card of cards.sort((a, b) => rank(a) - rank(b))) page.insertBefore(card, bar);
}

function stopArranging() {
  for (const page of document.querySelectorAll(".page.arranging")) {
    page.classList.remove("arranging");
    for (const card of pageCards(page)) {
      card.removeAttribute("tabindex");
      card.removeAttribute("aria-label");
    }
    page.querySelector(".layout-bar").replaceChildren(arrangeButton(page));
  }
  stopArrangingTabs();
  resizeCharts();
}

// « Réorganiser » : les cartes de la page (s'il y en a au moins deux) et la barre d'onglets.
function arrangeButton(page) {
  const button = smallButton("Réorganiser", "Changer l'ordre des cartes de cette page et des onglets, masquer un onglet", () => {
    stopArranging();
    page.classList.add("arranging");
    const movable = pageCards(page).length >= 2;
    for (const card of movable ? pageCards(page) : []) {
      card.tabIndex = 0;
      card.setAttribute("aria-label", `Carte « ${card.querySelector("h2").textContent} » : flèches pour la déplacer`);
    }
    startArrangingTabs();
    const help = document.createElement("span");
    help.className = "muted small";
    help.textContent = movable
      ? "Glisse les cartes et les onglets à la souris ou au doigt (au clavier : Tab puis flèches) ; décoche un onglet pour le masquer."
      : "Glisse les onglets à la souris ou au doigt (au clavier : Tab puis flèches) ; décoche un onglet pour le masquer.";
    const buttons = [help];
    if (movable) {
      buttons.push(smallButton("Cartes par défaut", "Revenir à l'ordre d'origine des cartes de cette page", () => {
        delete layout[page.dataset.page];
        savePref(LAYOUT_KEY, layout);
        applyLayout(page);
      }, ""));
    }
    buttons.push(
      smallButton("Onglets par défaut", "Tous les onglets affichés, dans l'ordre d'origine", () => {
        Object.assign(tabsOrder, { order: null, hidden: [] });
        savePref(TABS_KEY, tabsOrder);
        applyTabsOrder();
      }, ""),
      smallButton("Terminé", "Quitter le mode Réorganiser", stopArranging, "primary"),
    );
    page.querySelector(".layout-bar").replaceChildren(...buttons);
    (movable ? pageCards(page)[0] : movableTabs()[0]).focus();
  });
  return button;
}

// Toutes les pages ont le bouton : même une page d'une seule carte permet de régler les onglets.
for (const page of document.querySelectorAll(".page[data-page]")) {
  defaultLayout[page.dataset.page] = pageCards(page).map((c) => c.dataset.card);
  const bar = document.createElement("div");
  bar.className = "layout-bar";
  bar.append(arrangeButton(page));
  page.append(bar);
  if (pageCards(page).length < 2) continue; // une seule carte : rien à réordonner
  applyLayout(page);
  makeReorderable(page, ".card[data-card]", ".page.arranging > .card[data-card]", (cards) => {
    layout[page.dataset.page] = cards.map((c) => c.dataset.card);
    savePref(LAYOUT_KEY, layout);
  });
}

// --- Ordre et masquage des onglets (D-072, D-073) --------------------------------------------
// Réglés dans le mode « Réorganiser », gardés dans ce navigateur comme la disposition des
// cartes ; « Gérer » reste en dernier et toujours affiché. Pendant le réglage, les onglets
// masqués réapparaissent grisés, avec une case pour les afficher de nouveau.
const TABS_KEY = PREF + "onglets";
const tabsOrder = loadPref(TABS_KEY, { order: null, hidden: [] });
if (!Array.isArray(tabsOrder.hidden)) tabsOrder.hidden = [];
const movableTabs = () => [...$("tabs").querySelectorAll("[data-tab]")].filter((a) => a.dataset.tab !== "gerer");
const defaultTabs = movableTabs().map((a) => a.dataset.tab);

function applyTabsOrder() {
  const saved = Array.isArray(tabsOrder.order) ? tabsOrder.order : defaultTabs;
  const rank = (a) => {
    const i = saved.indexOf(a.dataset.tab);
    return i < 0 ? saved.length + defaultTabs.indexOf(a.dataset.tab) : i; // onglet nouveau : à la fin
  };
  const gerer = $("tabs").querySelector('[data-tab="gerer"]');
  const arranging = $("tabs").classList.contains("arranging");
  for (const a of movableTabs().sort((x, y) => rank(x) - rank(y))) {
    $("tabs").insertBefore(a, gerer);
    const masked = tabsOrder.hidden.includes(a.dataset.tab);
    a.hidden = masked && !arranging;
    a.classList.toggle("masked", masked);
    const box = a.querySelector(".tab-shown");
    if (box) box.checked = !masked;
  }
}

function startArrangingTabs() {
  const tabs = $("tabs");
  tabs.classList.add("arranging", "open"); // petit écran : le menu s'ouvre pour montrer les onglets
  for (const a of movableTabs()) {
    const name = tabName(a);
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "tab-shown";
    box.setAttribute("aria-label", `Afficher l'onglet « ${name} »`);
    box.addEventListener("change", () => {
      const others = tabsOrder.hidden.filter((t) => t !== a.dataset.tab);
      tabsOrder.hidden = box.checked ? others : [...others, a.dataset.tab];
      savePref(TABS_KEY, tabsOrder);
      applyTabsOrder();
    });
    a.prepend(box);
    a.setAttribute("aria-label", `Onglet « ${name} » : flèches pour le déplacer`);
  }
  applyTabsOrder();
}

function stopArrangingTabs() {
  const tabs = $("tabs");
  if (!tabs.classList.contains("arranging")) return;
  tabs.classList.remove("arranging", "open");
  for (const box of tabs.querySelectorAll(".tab-shown")) box.remove();
  for (const a of movableTabs()) a.removeAttribute("aria-label");
  applyTabsOrder();
}

// Pendant le réglage, un clic sur un onglet ne change pas de page (la case, elle, reste cochable).
$("tabs").addEventListener("click", (e) => {
  if ($("tabs").classList.contains("arranging") && !e.target.matches(".tab-shown")) e.preventDefault();
}, true);
makeReorderable($("tabs"), 'a[data-tab]:not([data-tab="gerer"])', '#tabs.arranging a[data-tab]', (tabs, moved) => {
  tabsOrder.order = tabs.map((a) => a.dataset.tab);
  savePref(TABS_KEY, tabsOrder);
  moved.focus();
});
applyTabsOrder();

function loadPage() {
  PAGES[currentPage]?.().catch((err) => showMessage(err.message, true));
}

window.addEventListener("hashchange", showPage);

// Menu « hamburger » des petits écrans : le même bloc d'onglets, affiché en liste à la demande.
function closeMenu() {
  $("tabs").classList.remove("open");
  $("menu-toggle").setAttribute("aria-expanded", "false");
}
$("menu-toggle").addEventListener("click", () => {
  const open = $("tabs").classList.toggle("open");
  $("menu-toggle").setAttribute("aria-expanded", String(open));
});
$("tabs").addEventListener("click", (e) => {
  // Même page re-cliquée : pas de hashchange. Pendant « Réorganiser », le menu reste ouvert.
  if (e.target.closest("a") && !$("tabs").classList.contains("arranging")) closeMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenu();
});

let ttlMinutes = 5;

// Ce que montre Vue d'ensemble : le filtre de lignes (D-069) et le périmètre financier (D-143)
// demandent un second appel, servi par le cache des cours ; brut et net reprennent `portfolio`.
async function overviewView(portfolio) {
  const scope = totalScope.scope;
  if (!filterActive() && scope !== "financier") return { ...portfolio, scope };
  const params = filterActive() ? addExclude(new URLSearchParams()) : new URLSearchParams();
  params.set("scope", scope);
  return api(`/api/portfolio?${params}`);
}

// Changement de périmètre : seule la Vue d'ensemble est redessinée.
async function refreshOverview() {
  if (!lastPortfolio) return;
  renderPortfolio(lastPortfolio, await overviewView(lastPortfolio));
}

async function refresh(force = false) {
  // Prochaine actualisation automatique : le serveur ne rappelle les sources qu'une fois
  // le cache expiré, donc ce minuteur ne génère pas d'appels en trop.
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(
    () => refresh().catch((err) => showMessage(err.message, true)),
    ttlMinutes * 60 * 1000,
  );
  const [accounts, assets, operations] = await Promise.all([
    api("/api/accounts"),
    api("/api/assets"),
    api("/api/operations"),
  ]);
  renderLists(accounts, assets, operations);
  renderLineFilter();
  refreshDebts().catch((err) => showMessage(err.message, true)); // sans réseau : immédiat
  refreshRecurring().catch((err) => showMessage(err.message, true)); // sans réseau : immédiat
  const portfolio = await api(force ? "/api/portfolio?force=true" : "/api/portfolio");
  ttlMinutes = portfolio.price_ttl_minutes;
  renderPortfolio(portfolio, await overviewView(portfolio));
  renderFilterButton();
  // Après le portefeuille : la courbe finit au cours qui vient d'être mis en cache.
  refreshHistory()
    .catch((err) => {
      $("history-status").textContent = `Évolution indisponible : ${frenchError(err.message)}`;
    })
    .then(() => refreshPerformance()) // après l'historique : les clôtures sont déjà à jour
    .catch((err) => {
      $("perf-status").textContent = `Performance indisponible : ${frenchError(err.message)}`;
    })
    .then(loadPage); // page ouverte (calendrier, comparaison...) mise à jour elle aussi
}

async function deleteOperation(op) {
  if (!await askConfirm(`Supprimer l'opération du ${op.op_date} ?`)) return;
  try {
    await api(`/api/operations/${op.id}`, { method: "DELETE" });
    showMessage("Opération supprimée.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
}

const orNull = (v) => (v && v.trim() !== "" ? v.trim() : null);
