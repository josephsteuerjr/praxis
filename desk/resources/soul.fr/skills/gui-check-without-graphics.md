# Vérifier une interface sans écran physique

Identifier la surface. Playwright peut tester une vraie interface web ; Electron dispose d'une API expérimentale. Xvfb fournit un écran Linux virtuel sans transformer contrôles natifs ou webview quelconque en DOM. Il faut une automatisation et une capture adaptées. Nommer explicitement les maquettes HTML.

`playwright-core` ne fournit pas de navigateur. Trouver un exécutable compatible ou en installer un dans un environnement isolé. Noter versions, chemin et méthode de connexion. Un navigateur headless n'exige généralement pas d'écran ; un lancement Linux fenêtré peut nécessiter Xvfb.

1. Profil distinct et configuration de test sans secrets réels. Vérifier l'adresse de l'environnement.
2. Lancer application/backend réels et attendre une disponibilité observable, pas un sleep fixe comme preuve.
3. Sélectionner par rôle, nom accessible ou test id ; vérifier visible/enabled/checked et libellés.
4. Changer le réglage par l'interface ; vérifier persistance, champs voisins et prochaine action backend. Tester le retour et la réouverture.
5. Garder assertions, console/pageerror et captures des deux états. Ouvrir les images : un PNG existant n'est pas une revue visuelle.
6. Nettoyer seulement processus et profils du test.

Cet exemple minimal exige adaptation et ne vérifie pas le backend :

    const { chromium } = require('playwright-core');
    const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH, headless: true });
    try {
      const page = await browser.newPage();
      await page.goto(process.env.TEST_APP_URL);
      const toggle = page.getByRole('checkbox', { name: 'Example option', exact: true });
      await toggle.check();
      if (!(await toggle.isChecked())) throw new Error('toggle not checked');
      await page.screenshot({ path: 'gate-on.png', fullPage: true });
      await toggle.uncheck();
      if (await toggle.isChecked()) throw new Error('toggle still checked');
      await page.screenshot({ path: 'gate-off.png', fullPage: true });
    } finally { await browser.close(); }

Le DOM ne prouve ni lisibilité ni absence de chevauchement. Un test web headless ne qualifie ni coque native, ni dialogues système, ni installation, ni téléphone physique. Nommer surface testée et limites.

Documentation :
https://playwright.dev/docs/api/class-electron
https://playwright.dev/docs/browsers
https://playwright.dev/docs/ci
