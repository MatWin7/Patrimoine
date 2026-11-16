// Tutoriel de premier lancement (D-099) : cinq étapes dans une fenêtre, sur les vrais formulaires.
// Il se lance seul quand le profil ouvert est vide et que le tutoriel n'a pas été fini ou passé ;
// l'étape cochée se déduit des données (compte, actif, opération existants), rien d'autre n'est
// retenu en route. « Passé » ou « terminé » est gardé dans le profil (profils.json), ou dans ce
// navigateur quand l'app n'a pas de profils. Aucune donnée saisie ne passe par ici.
(() => {
  const $ = (id) => document.getElementById(id);
  const dialog = $("dlg-tutorial");
  const DONE_KEY = "patrimoine.tutoriel"; // sans profils seulement
  const RESUME_KEY = "patrimoine.tutoriel-reprise"; // après la création du profil, la page se recharge

  const store = {
    get(storage, key) {
      try {
        return window[storage].getItem(key);
      } catch {
        return null;
      }
    },
    set(storage, key, value) {
      try {
        if (value === null) window[storage].removeItem(key);
        else window[storage].setItem(key, value);
      } catch {
        // stockage refusé (navigation privée) : le tutoriel reviendra, rien de grave
      }
    },
  };

  const profile = () => window.patrimoineProfile ?? null;
  const isEmpty = () => !lists.accounts.length && !lists.assets.length && !lists.operations.length;
  const isDone = () => (profile() ? Boolean(profile().tutorial_done) : store.get("localStorage", DONE_KEY) === "1");
  // Premier lancement (aucun profil encore), ou depuis le profil Démo : on crée alors le vrai profil.
  const inDemo = () => Boolean(profile()?.demo);
  const canCreateProfile = () => (!profile() && isEmpty()) || inDemo();

  async function markDone() {
    if (!profile()) return store.set("localStorage", DONE_KEY, "1");
    const updated = await api(`/api/profiles/${encodeURIComponent(profile().id)}`, {
      method: "PATCH",
      body: JSON.stringify({ tutorial_done: true }),
    });
    Object.assign(profile(), updated);
  }

  // --- Petits éléments --------------------------------------------------------------------

  function el(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  }

  function tip(text) {
    const button = el("button", { type: "button", className: "tip", textContent: "?" });
    button.dataset.tip = text;
    button.setAttribute("aria-label", "Plus d'infos");
    return button;
  }

  const done = (text) => el("p", { className: "ok small" }, `✓ ${text}`);

  // --- Étapes -----------------------------------------------------------------------------
  // Chaque étape : titre, une ligne, une infobulle pour le détail, et sa zone d'action.

  const TOUR = {
    apercu: "Le total, la répartition et les gains du jour.",
    performance: "Ce que rapporte le patrimoine, par jour, mois ou année.",
    visualisations: "Carte du portefeuille, calendrier des rendements, listes de suivi.",
    comparaison: "Ton parcours face à des indices et des livrets.",
    projection: "Où tu seras plus tard, et ton allocation cible.",
    revenus: "Dividendes reçus et frais payés.",
    fiscalite: "Repères sur les plafonds et les impôts, en estimation.",
    crypto: "Récompenses, frais et dépôts Aave.",
    positions: "Chaque ligne par compte, et toutes les opérations.",
    gerer: "Comptes, actifs, imports, achats programmés.",
  };

  // Ferme le tutoriel le temps d'utiliser un vrai formulaire, le rouvre quand celui-ci se ferme.
  function useDialog(id, open) {
    dialog.close();
    $(id).addEventListener("close", () => show(index), { once: true });
    open();
  }

  const STEPS = [
    {
      title: "Bienvenue",
      text: "Ici, tes données restent sur cet ordinateur et l'app ne peut passer aucun ordre.",
      tip: "Sur Internet, l'app ne demande que des cours, des taux de change et des indices publics, sans montant, quantité ni adresse. Elle est en lecture seule : elle ne peut rien acheter ni virer.",
      body(box, next) {
        if (!canCreateProfile()) return next("Commencer");
        const form = el("form", { id: "form-tutorial-profile", className: "tutorial-form" });
        form.append(
          el("label", {}, "Ton prénom", el("input", { name: "name", required: true, maxLength: 40, autocomplete: "given-name" })),
          el(
            "label",
            {},
            el("span", { className: "lab" }, "Code (facultatif)", tip("Le code évite les regards curieux dans l'app. Il ne chiffre pas les données : le fichier de la base reste lisible sur l'ordinateur. Code oublié : celui qui a installé l'app le retire dans le terminal.")),
            el("input", { name: "code", type: "password", minLength: 4, maxLength: 64, autocomplete: "new-password" }),
          ),
        );
        const error = el("p", { className: "ko small", hidden: true, role: "alert" });
        form.append(error);
        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          try {
            const created = await api("/api/profiles", {
              method: "POST",
              body: JSON.stringify({ name: form.name.value, code: form.code.value || null, adopt_empty: !inDemo() }),
            });
            if (inDemo()) {
              // Depuis la démo, le nouveau profil est à côté : on l'ouvre (le code saisi sert ici).
              await api(`/api/profiles/${encodeURIComponent(created.id)}/open`, {
                method: "POST",
                body: JSON.stringify({ code: form.code.value || "" }),
              });
            }
            store.set("sessionStorage", RESUME_KEY, "1");
            location.reload(); // la page repart sur le nouveau profil, puis reprend à l'étape 2
          } catch (err) {
            error.textContent = err.message;
            error.hidden = false;
          }
        });
        box.append(form);
        const demo = el("button", { type: "button", className: "link-plain", textContent: "Explorer avec la démo (données fictives)" });
        demo.addEventListener("click", async () => {
          try {
            const created = await api("/api/profiles/demo", { method: "POST" });
            await api(`/api/profiles/${encodeURIComponent(created.id)}/open`, { method: "POST", body: "{}" });
            location.reload();
          } catch (err) {
            error.textContent = err.message;
            error.hidden = false;
          }
        });
        if (!inDemo()) box.append(demo);
        return { submit: form, label: "Commencer" };
      },
    },
    {
      title: "Comment ça marche",
      text: "Quatre repères avant de commencer.",
      tip: "Ils restent dans Réglages (roue dentée), carte « Aide », avec la liste de ce qui sort de cet ordinateur.",
      body(box, next) {
        const blocks = el("div", { className: "how-grid" }, el("p", { className: "muted small" }, "Chargement…"));
        fillHow(blocks);
        box.append(blocks, outboundLink());
        return next("Suivant");
      },
    },
    {
      title: "Un compte par enveloppe",
      text: "Un compte, c'est une enveloppe : PEA, assurance-vie, livret, wallet crypto...",
      tip: "Crée un compte par enveloppe, comme chez ta banque ou ton courtier. Le type règle les champs utiles (versements d'un PEA, espèces d'un livret) et les repères fiscaux.",
      body(box, next) {
        if (lists.accounts.length) {
          box.append(done(`Compte « ${lists.accounts[0].name} » créé`));
          return next("Suivant");
        }
        return { action: () => useDialog("dlg-account", () => openAccount()), label: "Ajouter un compte" };
      },
    },
    {
      title: "Un actif par ligne",
      text: "Un actif, c'est une ligne que tu détiens : un ETF, une action, une crypto, un fonds euros.",
      tip: "Avec un ticker ou un identifiant CoinGecko, le cours est trouvé tout seul. Sans, c'est une valeur saisie à la main (fonds euros, immobilier) : tu en donnes la valeur de temps en temps.",
      body(box, next) {
        if (lists.assets.length) {
          box.append(done(`Actif « ${lists.assets[0].name} » créé`));
          return next("Suivant");
        }
        box.append(
          el("button", { type: "button", className: "link-plain", textContent: "J'ai un relevé : l'importer plutôt" }),
        );
        box.lastChild.addEventListener("click", () => goTo("card-import", "Choisis le compte et ton fichier : l'aperçu te guide."));
        return { action: () => useDialog("dlg-asset", () => openAsset()), label: "Ajouter un actif" };
      },
    },
    {
      title: "Une première opération",
      text: "Une opération, c'est un achat, une vente, un dividende ou un versement.",
      tip: "L'app calcule tout à partir des opérations : quantités, prix de revient (PRU), plus-values, espèces. Seules les valeurs sans cours public se saisissent à la main.",
      body(box, next) {
        if (lists.operations.length) {
          box.append(done("Première opération enregistrée"));
          return next("Suivant");
        }
        if (!lists.accounts.length || !lists.assets.length) {
          box.append(el("p", { className: "muted small" }, "Il faut d'abord un compte et un actif."));
          return next("Suivant");
        }
        box.append(el("p", { className: "muted small" }, "Cette fenêtre revient dès que l'opération est enregistrée."));
        return {
          action: () => {
            waiting = true;
            goTo("card-operation", "Saisis un achat : compte, actif, date, quantité et prix.");
          },
          label: "Saisir un achat",
        };
      },
    },
    {
      title: "Le tour des onglets",
      text: "Voilà ce que tu trouveras, une fois tes lignes en place.",
      tip: "Les onglets se masquent et se réorganisent avec « Réorganiser » (Vue d'ensemble). Ce tutoriel se rouvre depuis Réglages (roue dentée), carte « Aide ».",
      body(box) {
        const list = el("dl", { className: "tutorial-tour" });
        for (const tab of document.querySelectorAll("#tabs [data-tab]")) {
          if (tab.hidden || !TOUR[tab.dataset.tab]) continue;
          list.append(el("dt", {}, tab.firstChild.textContent.trim()), el("dd", {}, TOUR[tab.dataset.tab]));
        }
        box.append(list);
        return { action: finish, label: "Terminer" };
      },
    },
  ];

  // --- Comment ça marche et Ce qui sort de cet ordinateur (D-131) ---------------------------
  // Lu sur cet ordinateur par /api/about, sans réseau : dossier des données, sauvegardes du
  // profil ouvert, choix des mises à jour, liste des services joints et leur compteur.

  const inWindow = new URLSearchParams(location.search).has("fenetre");

  function howBlocks(info) {
    const block = (title, tipText, line, detail) =>
      el("section", { className: "how-block" }, el("h3", {}, title, " ", tip(tipText)), el("p", {}, line), ...(detail ? [detail] : []));
    const detail = (...parts) => el("p", { className: "muted small" }, ...parts);

    const folder = detail(el("span", { className: "how-path", textContent: info.data_dir }));
    if (inWindow) {
      const open = el("button", { type: "button", className: "link-plain", textContent: "Ouvrir" });
      open.addEventListener("click", async () => {
        const res = await api("/api/window/folder", { method: "POST" }).catch(() => ({}));
        if (!res.done) showMessage("Le dossier des données n'a pas pu être ouvert.", true);
      });
      folder.append(" ", open);
    }

    const b = info.backups;
    const copy = b.copy_error
      ? el("span", { className: "warn", textContent: b.copy_error })
      : b.copy_dir
        ? `Autre disque : ${b.copy_dir}`
        : "Autre disque : pas encore (Réglages, carte Général)";
    const backups = detail(b.last ? `Dernière : ${frDate(b.last)}` : "Première copie au prochain lancement", " · ", copy);

    const UPDATES = { true: "avis activé", false: "avis coupé", null: "avis pas encore choisi" };

    return [
      block(
        "Tes données",
        "Une base par profil, dans ce dossier. Ni cloud, ni compte, ni télémétrie. Le code d'un profil évite les regards curieux mais ne chiffre pas la base.",
        "Sur cet ordinateur, nulle part ailleurs.",
        folder,
      ),
      block(
        "Sauvegardes",
        `Copie relue de chaque base au lancement puis chaque heure (une par jour au plus), dans le dossier « sauvegardes ». Gardées : les ${b.keep} dernières, une par mois sur ${b.months} mois et une par an. Une copie sur un autre disque ou une clé USB survit à une panne : carte Réglages.`,
        "Une copie par jour d'utilisation, vérifiée.",
        backups,
      ),
      block(
        "Mises à jour",
        "Si tu l'as accepté, l'app demande à GitHub, une fois par jour au plus, le numéro de la dernière version. Elle prévient seulement : tu télécharges la nouvelle version toi-même, tes données restent dans leur dossier.",
        "L'app prévient, tu télécharges.",
        detail(`Version ${info.version} · ${UPDATES[info.updates]}`),
      ),
      block(
        "Ce que tu peux changer",
        "Comptes, actifs et opérations se corrigent ou se suppriment à tout moment ; supprimer un compte ou un actif fait d'abord une sauvegarde. Onglets : « Réorganiser » dans la Vue d'ensemble. Réglages, mises à jour et profils : cartes de Gérer.",
        "Tout se corrige, à tout moment.",
        detail("Gérer : saisies · Roue dentée : réglages · Vue d'ensemble : Réorganiser"),
      ),
    ];
  }

  async function fillHow(box) {
    try {
      box.replaceChildren(...howBlocks(await api("/api/about")));
    } catch {
      box.replaceChildren(el("p", { className: "muted small" }, "Repères indisponibles pour l'instant."));
    }
  }

  function outboundLink() {
    const link = el("button", { type: "button", className: "link-plain", textContent: "Ce qui sort de cet ordinateur" });
    link.addEventListener("click", openOutbound);
    return link;
  }

  let sortBound = false;
  async function openOutbound() {
    let info;
    try {
      info = await api("/api/about");
    } catch (err) {
      return showMessage(err.message, true);
    }
    const o = info.outbound;
    if (!sortBound) {
      bindSortable("outbound-table", ["count"]); // app.js se charge après ce script
      sortBound = true;
    }
    $("outbound-never").textContent = o.never_sent;
    $("outbound-seen").dataset.tip = o.seen_anyway;
    $("outbound-outside").textContent = o.outside_app;
    const refused = $("outbound-refused");
    refused.hidden = !o.refused;
    refused.textContent = `Bloquées depuis le lancement : ${o.refused} requête(s) vers une adresse non déclarée.`;
    fillSortable(
      "outbound-table",
      o.services,
      (s) => {
        const name = cell(s.name);
        name.append(" ", tip(`Pour t'en passer : ${s.stop} Adresse : ${s.hosts.join(", ")}.`));
        const count = cell(s.count ? `${s.count} · à ${formatTime(s.last_at)}` : "aucune", "num");
        if (s.last_at) count.title = `Dernière à ${formatTime(s.last_at)}`;
        return rowOf(name, cell(s.purpose), cell(s.sent), cell(s.when), count);
      },
      "",
    );
    const outbound = $("dlg-outbound");
    if (!outbound.open) outbound.showModal();
  }

  for (const button of document.querySelectorAll("[data-outbound-open]")) button.addEventListener("click", openOutbound);

  // Carte « Aide » de Réglages : relue à chaque ouverture de la page (dernière sauvegarde, réglages changés).
  const refreshHowCard = () => {
    if (location.hash === "#reglages") fillHow($("how-blocks"));
  };
  window.addEventListener("hashchange", refreshHowCard);
  document.addEventListener("patrimoine:data", refreshHowCard, { once: true });

  // --- Fenêtre ----------------------------------------------------------------------------

  let index = 0;
  let shown = ""; // nombre de comptes/actifs/opérations au dernier affichage
  const counts = () => `${lists.accounts.length}/${lists.assets.length}/${lists.operations.length}`;
  let waiting = false; // l'opération se saisit hors de la fenêtre

  function goTo(cardId, hint) {
    dialog.close();
    location.hash = "#gerer";
    requestAnimationFrame(() => {
      // « Ajouter des opérations » (D-135) : une carte, deux panneaux ; on ouvre le bon.
      if (cardId === "card-import" || cardId === "card-operation") {
        document.dispatchEvent(new CustomEvent("operationmode", { detail: cardId === "card-import" ? "import" : "saisie" }));
        cardId = "card-operation";
      }
      $(cardId)?.scrollIntoView({ behavior: "smooth", block: "start" });
      showMessage(hint);
    });
  }

  async function finish() {
    try {
      await markDone();
    } catch (err) {
      showMessage(err.message, true);
    }
    dialog.close();
    location.hash = "#apercu";
  }

  function show(i) {
    index = i;
    shown = counts();
    const step = STEPS[i];
    const box = el("div", { className: "tutorial-body" });
    let primary = null;
    const next = (label) => {
      primary = { action: () => show(i + 1), label };
      return primary;
    };
    primary = step.body(box, next);

    const dots = el("p", { className: "muted small" }, `Étape ${i + 1} sur ${STEPS.length}`);
    const footer = el("div", { className: "form-actions tutorial-actions" });
    const skip = el("button", { type: "button", textContent: i === STEPS.length - 1 ? "Fermer" : "Passer le tutoriel" });
    skip.addEventListener("click", async () => {
      if (i < STEPS.length - 1) await markDone().catch((err) => showMessage(err.message, true));
      dialog.close();
    });
    footer.append(skip);
    if (i > 0) {
      const back = el("button", { type: "button", textContent: "Précédent" });
      back.addEventListener("click", () => show(i - 1));
      footer.append(back);
    }
    const go = el("button", { type: primary.submit ? "submit" : "button", className: "primary", textContent: primary.label });
    if (primary.submit) go.setAttribute("form", primary.submit.id);
    else go.addEventListener("click", primary.action);
    footer.append(go);

    $("tutorial-title").textContent = step.title;
    $("tutorial-text").replaceChildren(step.text, " ", tip(step.tip));
    $("tutorial-step").replaceChildren(dots);
    $("tutorial-content").replaceChildren(box, footer);
    if (!dialog.open) dialog.showModal();
  }

  function start() {
    waiting = false;
    const resume = store.get("sessionStorage", RESUME_KEY) === "1";
    store.set("sessionStorage", RESUME_KEY, null);
    show(resume ? 1 : 0);
  }

  $("tutorial-open").addEventListener("click", start);
  $("demo-leave").addEventListener("click", start); // bandeau du profil Démo : créer son vrai profil
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());

  // --- Carte « Pour bien démarrer » (Vue d'ensemble) -----------------------------------------
  // Trois cases cochées d'après les données. Elle disparaît quand elles le sont toutes, ou par
  // « Masquer » (gardé dans ce navigateur, par profil). Jamais dans le profil Démo.
  const HIDE_KEY = "demarrer-masque";
  const hideKey = () => `${window.patrimoinePrefPrefix ?? "patrimoine."}${HIDE_KEY}`;

  function renderStartCard() {
    const card = $("card-start");
    const steps = [
      { label: "Un compte", done: lists.accounts.length > 0, open: () => openAccount(), action: "Ajouter" },
      { label: "Un actif", done: lists.assets.length > 0, open: () => openAsset(), action: "Ajouter" },
      {
        label: "Une première opération",
        done: lists.operations.length > 0,
        ready: lists.accounts.length > 0 && lists.assets.length > 0,
        open: () => goTo("card-operation", "Saisis un achat : compte, actif, date, quantité et prix."),
        action: "Saisir",
      },
    ];
    card.hidden = inDemo() || steps.every((s) => s.done) || store.get("localStorage", hideKey()) === "1";
    if (card.hidden) return;
    $("start-list").replaceChildren(
      ...steps.map((s) => {
        const li = el("li", { className: s.done ? "done" : "" }, el("span", { className: s.done ? "ok" : "muted", textContent: s.done ? "✓" : "○" }), el("span", { textContent: s.label }));
        if (!s.done && s.ready !== false) {
          const go = el("button", { type: "button", className: "link-plain", textContent: s.action });
          go.addEventListener("click", s.open);
          li.append(go);
        }
        return li;
      }),
    );
  }
  $("start-hide").addEventListener("click", () => {
    store.set("localStorage", hideKey(), "1");
    $("card-start").hidden = true;
  });
  document.addEventListener("patrimoine:data", renderStartCard);

  // Premier chargement des données : lancement automatique si le profil est vide.
  let first = true;
  document.addEventListener("patrimoine:data", () => {
    if (first) {
      first = false;
      if (!isDone() && isEmpty() && !dialog.open) start();
      return;
    }
    // Un formulaire vient d'enregistrer : la fenêtre, déjà rouverte, se met à jour.
    if (dialog.open && counts() !== shown) return show(index);
    // Opération enregistrée pendant que la fenêtre était fermée : on passe à la suite.
    if (waiting && lists.operations.length) {
      waiting = false;
      show(STEPS.length - 1);
    }
  });
})();
