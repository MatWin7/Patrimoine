// --- Visualisations : carte du portefeuille (treemap) ------------------------------------
// Plugin chartjs-chart-treemap copié dans web/vendor (pas de CDN). Surface = valeur en EUR de
// la ligne ; la couleur dit la variation du jour, la plus-value latente ou la couleur choisie.
// Suit le filtre « Lignes prises en compte », comme la Vue d'ensemble.

const TREEMAP_KEY = PREF + "carte";
const treemapPref = { area: "sqrt", ...loadPref(TREEMAP_KEY, { group: "account", color: "day" }) };

// Mélange de deux couleurs #rrggbb (le canvas ne comprend pas toujours color-mix()).
function mixHex(a, b, t) {
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [rgb(a), rgb(b)];
  return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * t)).join(",")})`;
}

// Lignes de la carte : une par (compte, actif), avec de quoi regrouper et colorer.
function treemapRows(portfolio) {
  const assets = new Map(lists.assets.map((a) => [a.id, a]));
  return (portfolio?.positions ?? [])
    .filter((p) => p.value_eur !== null && Number(p.value_eur) > 0)
    .map((p) => ({
      asset: p.asset,
      account: p.account,
      class: p.asset_class,
      category: assets.get(p.asset_id)?.category || p.asset_class,
      value: Number(p.value_eur), // surface seulement : les textes viennent des chaînes
      // Surface adoucie (D-077) : racine carrée de la valeur, pour qu'une ligne très lourde
      // n'écrase pas les autres (90 % contre 1 % : surface 9,5 fois plus grande, pas 90).
      area: Math.sqrt(Number(p.value_eur)),
      value_text: formatDecimal(p.value_eur, "EUR"),
      weight: p.weight_pct,
      day: p.day_change_pct,
      gain: p.gain_pct,
      gain_eur: p.gain_eur,
    }));
}

function treemapFill(leaf, mode, scale) {
  if (mode === "line") {
    const name = lineColors().get(leaf.asset);
    return name ? css(name) : otherColor();
  }
  const pct = leaf[mode];
  if (pct === null || pct === undefined) return css("--neutral");
  const strength = Math.min(Math.abs(Number(pct)) / scale, 1);
  return mixHex(css("--surface"), css(Number(pct) < 0 ? "--ko" : "--ok"), 0.25 + strength * 0.75);
}

function renderTreemap() {
  const group = treemapPref.group;
  const mode = treemapPref.color;
  $("treemap-group").value = group;
  $("treemap-color").value = mode;
  $("treemap-area").value = treemapPref.area;
  const rows = treemapRows(overviewPortfolio);
  // Même échelle pour toute la carte : la plus forte variation visible donne la couleur la plus vive.
  const scale = Math.max(mode === "day" ? 1 : 5, ...rows.map((r) => Math.abs(Number(r[mode] ?? 0))));
  const leafOf = (ctx) => {
    const raw = ctx.raw;
    return raw && raw.l === (group ? 1 : 0) ? raw._data.children?.[0] : null;
  };
  const textColor = css("--text");
  drawChart("chart-treemap", {
    type: "treemap",
    data: {
      datasets: [{
        tree: rows,
        key: treemapPref.area === "value" ? "value" : "area",
        groups: group ? [group, "asset"] : ["asset"],
        spacing: 1,
        borderWidth: 1,
        borderColor: css("--surface"),
        backgroundColor: (ctx) => {
          if (ctx.type !== "data") return "transparent";
          const leaf = leafOf(ctx);
          return leaf ? treemapFill(leaf, mode, scale) : css("--surface-2") || css("--surface");
        },
        captions: { display: Boolean(group), color: textColor, font: { weight: "600" }, padding: 4 },
        labels: {
          display: true,
          color: textColor,
          overflow: "hidden",
          formatter: (ctx) => {
            const leaf = leafOf(ctx);
            if (!leaf) return "";
            const pct = mode === "line" ? leaf.weight : leaf[mode];
            const suffix = pct === null || pct === undefined ? "" : mode === "line" ? `${formatDecimal(pct)} %` : formatPct(pct);
            return suffix ? [leaf.asset, suffix] : leaf.asset;
          },
        },
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => {
              const leaf = leafOf(items[0]);
              return leaf ? `${leaf.asset}${group === "account" ? "" : ` · ${leaf.account}`}` : items[0].raw.g;
            },
            label: (item) => {
              const leaf = leafOf(item);
              if (!leaf) {
                const total = rows.filter((r) => r[group] === item.raw.g).reduce((t, r) => t + r.value, 0);
                return ` ${formatDecimal(total.toFixed(2), "EUR")}`;
              }
              const lines = [` Valeur : ${leaf.value_text}${leaf.weight ? ` (${formatDecimal(leaf.weight)} %)` : ""}`];
              if (leaf.day !== null) lines.push(` Jour : ${formatPct(leaf.day)}`);
              if (leaf.gain !== null) lines.push(` Plus-value latente : ${formatPct(leaf.gain)}${leaf.gain_eur ? ` (${formatDecimal(leaf.gain_eur, "EUR")})` : ""}`);
              return lines;
            },
          },
        },
      },
    },
  });
  const notes = [];
  if (!rows.length) notes.push("Aucune ligne valorisée pour l'instant.");
  if (group === "class" || group === "category") notes.push("Une même ligne détenue dans deux comptes forme une seule tuile.");
  if (mode !== "line") notes.push("Vert : hausse, rouge : baisse (plus vif, plus fort), gris : pas de cours.");
  $("treemap-note").textContent = notes.join(" ");
}

for (const [id, key] of [["treemap-group", "group"], ["treemap-color", "color"], ["treemap-area", "area"]]) {
  $(id).addEventListener("change", (e) => {
    treemapPref[key] = e.target.value;
    savePref(TREEMAP_KEY, treemapPref);
    renderTreemap();
  });
}

// --- Visualisations : listes de suivi (D-072) ---------------------------------------------
// « Mes lignes » (actifs détenus, calculée) puis les listes créées, gardées en base.

const WATCH_KEY = PREF + "liste-suivi";
const watchPref = loadPref(WATCH_KEY, { choice: "lines" });
let watchLists = [];
const WATCH_PERIODS = ["1j", "1s", "1m", "ytd", "1a", "3a", "5a"];

const currentWatch = () => watchLists.find((w) => String(w.id) === watchPref.choice) ?? null;

function fillWatchSelect() {
  const select = $("watch-select");
  select.replaceChildren(new Option("Mes lignes", "lines"), ...watchLists.map((w) => new Option(`${w.name} (${w.items.length})`, String(w.id))));
  if (watchPref.choice !== "lines" && !currentWatch()) watchPref.choice = "lines";
  select.value = watchPref.choice;
  const own = currentWatch() !== null;
  $("watch-rename").hidden = $("watch-delete").hidden = $("form-watch-item").hidden = !own;
}

// L'ordre se règle à la poignée tant que le tableau est dans l'ordre choisi (aucun tri par colonne).
const watchOrderable = () => [null, "order"].includes(sorts["watch-table"].key);

function watchRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.id = row.id;
  if (watchOrderable()) tr.append(gripCell(row.label));
  else tr.append(document.createElement("td"));
  tr.append(cell(row.label), cell(row.ticker ?? "–"));
  const last = cell(row.last ? `${row.note ? "≈ " : ""}${formatDecimal(row.last, row.currency)}` : "–", "num");
  if (row.last_date) last.title = `Clôture du ${frDate(row.last_date)}`;
  if (row.note) last.title = row.note;
  tr.append(last);
  for (const key of WATCH_PERIODS) {
    const v = row[key];
    const td = cell(v === null || v === undefined ? "–" : formatPct(v), "num");
    if (v) td.classList.add(v.startsWith("-") ? "ko" : "ok");
    tr.append(td);
  }
  if (row.error) {
    tr.cells[2].textContent = "indisponible";
    tr.cells[2].title = row.error;
  }
  const actions = document.createElement("td");
  actions.className = "actions";
  if (row.ticker && !row.error) {
    const show = document.createElement("button");
    show.type = "button";
    show.className = "link edit";
    show.textContent = "Calendrier";
    show.onclick = () => showInCalendar(row);
    actions.append(show);
  }
  const list = currentWatch();
  if (list) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "link";
    del.textContent = "Retirer";
    del.onclick = () => watchAction(api(`/api/watchlists/${list.id}/items/${row.id}`, { method: "DELETE" }), `« ${row.label} » retiré de la liste.`);
    actions.append(del);
  }
  tr.append(actions);
  return tr;
}

let watchShown = null;
let watchSeq = 0; // une réponse lente ne remplace pas celle d'une liste choisie après
async function refreshWatch() {
  watchLists = await api("/api/watchlists");
  fillWatchSelect();
  const list = currentWatch();
  const seq = ++watchSeq;
  // Actualisation automatique de la même liste : on garde le tableau affiché en attendant.
  if (watchShown !== watchPref.choice) $("watch-rows").replaceChildren(emptyRow("Chargement des cours…"));
  watchShown = watchPref.choice;
  const data = await api(list ? `/api/watchlists/${list.id}/performance` : "/api/watchlists/lines/performance");
  if (seq !== watchSeq) return;
  const empty = list ? "Liste vide : ajoute un actif avec son ticker Yahoo." : "Aucune ligne cotée détenue pour l'instant.";
  fillSortable("watch-table", data.rows.map((r, i) => ({ ...r, order: i })), watchRow, empty);
}

// Glisser une ligne : « Mes lignes » suit l'ordre des actifs de « Gérer » (les lignes non
// affichées gardent leur place), une liste créée garde son propre ordre.
makeReorderable($("watch-rows"), "tr[data-id]", ".drag-handle", async (rows) => {
  const ids = rows.map((tr) => Number(tr.dataset.id));
  const list = currentWatch();
  try {
    if (list) {
      await api(`/api/watchlists/${list.id}/items/order`, { method: "PUT", body: JSON.stringify({ ids }) });
    } else {
      const shown = new Set(ids);
      const queue = [...ids];
      const full = lists.assets.map((a) => (shown.has(a.id) ? queue.shift() : a.id));
      await api("/api/assets/order", { method: "PUT", body: JSON.stringify({ ids: full }) });
      lists.assets.sort((a, b) => full.indexOf(a.id) - full.indexOf(b.id));
      renderLists(lists.accounts, lists.assets, lists.operations);
    }
    showMessage("Ordre enregistré.");
  } catch (err) {
    showMessage(err.message, true);
  }
  await refreshWatch().catch((err) => showMessage(err.message, true));
});

async function watchAction(promise, done) {
  try {
    await promise;
    if (done) showMessage(done);
    await refreshWatch();
  } catch (err) {
    showMessage(err.message, true);
  }
}

$("watch-select").addEventListener("change", (e) => {
  watchPref.choice = e.target.value;
  savePref(WATCH_KEY, watchPref);
  refreshWatch().catch((err) => showMessage(err.message, true));
});
$("watch-new").addEventListener("click", async () => {
  const name = (await askText("Nom de la nouvelle liste", { placeholder: "ex. ETF à surveiller", ok: "Créer" }))?.trim();
  if (!name) return;
  try {
    const created = await api("/api/watchlists", { method: "POST", body: JSON.stringify({ name }) });
    watchPref.choice = String(created.id);
    savePref(WATCH_KEY, watchPref);
    await watchAction(Promise.resolve(), `Liste « ${name} » créée : ajoute-lui des actifs.`);
    $("form-watch-item").label.focus();
  } catch (err) {
    showMessage(err.message, true);
  }
});
$("watch-rename").addEventListener("click", async () => {
  const list = currentWatch();
  const name = (await askText("Nouveau nom de la liste", { value: list.name, ok: "Renommer" }))?.trim();
  if (!name || name === list.name) return;
  watchAction(api(`/api/watchlists/${list.id}`, { method: "PATCH", body: JSON.stringify({ name }) }), "Liste renommée.");
});
$("watch-delete").addEventListener("click", async () => {
  const list = currentWatch();
  if (!await askConfirm(`Supprimer la liste « ${list.name} » et ses ${list.items.length} actifs suivis ?`)) return;
  watchPref.choice = "lines";
  savePref(WATCH_KEY, watchPref);
  watchAction(api(`/api/watchlists/${list.id}`, { method: "DELETE" }), "Liste supprimée.");
});
$("form-watch-item").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  const body = { label: form.label.value.trim(), ticker: form.ticker.value.trim(), crypto: form.kind.value === "crypto" };
  watchAction(api(`/api/watchlists/${currentWatch().id}/items`, { method: "POST", body: JSON.stringify(body) }), `« ${body.label} » ajouté.`).then(() => {
    const kind = form.kind.value;
    form.reset();
    form.kind.value = kind; // on ajoute souvent plusieurs cryptos d'affilée
    form.label.focus();
  });
});
// Crypto : l'id CoinGecko ou le symbole suffit, l'API trouve le ticker Yahoo (D-111).
$("form-watch-item").kind.addEventListener("change", (e) => {
  const crypto = e.target.value === "crypto";
  $("watch-ticker-label").textContent = crypto ? "Id CoinGecko ou symbole" : "Ticker Yahoo";
  e.target.form.ticker.placeholder = crypto ? "ex. bitcoin, BTC, BTC-EUR" : "ex. AI.PA, ^FCHI";
  e.target.form.label.placeholder = crypto ? "ex. Bitcoin" : "ex. Air Liquide";
});
bindSortable("watch-table", ["order", "last", ...WATCH_PERIODS]);

function refreshVisualisations() {
  renderTreemap();
  refreshWatch().catch((err) => {
    $("watch-rows").replaceChildren(emptyRow(`Listes indisponibles : ${err.message}`));
  });
  return refreshCalendar();
}
