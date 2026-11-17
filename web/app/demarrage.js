// --- Démarrage ------------------------------------------------------------------------------
// Dernier fichier chargé (D-139) : tout le reste est défini quand ces appels partent. Leurs
// réponses arrivent plus tard et appellent des fonctions de tous les autres fichiers.
checkHealth();
setInterval(checkHealth, 60000);
api("/api/updates").then(renderUpdates).catch(() => {}); // serveur injoignable : signalé par checkHealth
api("/api/settings").then(renderSettings).catch(() => {});
refresh().catch((err) => showMessage(err.message, true));
showPage();
