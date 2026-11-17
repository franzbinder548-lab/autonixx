(function () {
  "use strict";

  var INSTALL_ACCEPTED_KEY = "autonix_pwa_installed";
  var INSTALL_DISMISSED_KEY = "autonix_pwa_prompt_dismissed";
  var FIRST_PROMPT_DELAY = 5000;
  var DEFAULT_MESSAGE =
    "Install Autonix directly from your browser for faster access and a cleaner full-screen workspace.";
  var MANUAL_INSTALL_MESSAGE =
    "In Chrome, open the menu and select Install app or Add to Home screen. Other browsers may offer a similar option in their menu.";

  var deferredPrompt = null;
  var loadDelayComplete = false;

  var promptEl = document.getElementById("installAppPrompt");
  var installBtn = document.getElementById("installAppBtn");
  var closeBtn = document.getElementById("installAppClose");
  var messageEl = document.getElementById("installAppMessage");
  var legacyInstallBtn = document.querySelector(".install-app-btn");

  if (legacyInstallBtn) {
    legacyInstallBtn.remove();
  }

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function (err) {
        console.warn("SW registration failed:", err);
      });
    });
  }

  function isAndroid() {
    return /Android/i.test(navigator.userAgent || "");
  }

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function isInstalled() {
    return isStandalone() || localStorage.getItem(INSTALL_ACCEPTED_KEY) === "true";
  }

  function isDismissed() {
    return localStorage.getItem(INSTALL_DISMISSED_KEY) === "true";
  }

  function canShowPrompt() {
    return Boolean(
      promptEl &&
        installBtn &&
        isAndroid() &&
        !isInstalled() &&
        !isDismissed(),
    );
  }

  function setInstallMode(hasNativePrompt) {
    if (messageEl) {
      messageEl.textContent = hasNativePrompt
        ? DEFAULT_MESSAGE
        : MANUAL_INSTALL_MESSAGE;
    }
    if (installBtn) {
      installBtn.textContent = hasNativePrompt ? "Install Web App" : "Got it";
    }
  }

  function hidePrompt() {
    if (!promptEl) return;
    promptEl.hidden = true;
    promptEl.classList.remove("is-visible");
  }

  function showPrompt() {
    if (!canShowPrompt()) return;
    setInstallMode(Boolean(deferredPrompt));
    promptEl.hidden = false;
    window.requestAnimationFrame(function () {
      promptEl.classList.add("is-visible");
    });
  }

  function dismissPrompt() {
    localStorage.setItem(INSTALL_DISMISSED_KEY, "true");
    hidePrompt();
  }

  function markInstalled() {
    localStorage.setItem(INSTALL_ACCEPTED_KEY, "true");
    localStorage.removeItem(INSTALL_DISMISSED_KEY);
    deferredPrompt = null;
    hidePrompt();
  }

  function maybeShowInitialPrompt() {
    if (loadDelayComplete && canShowPrompt()) {
      showPrompt();
    }
  }

  window.addEventListener("load", function () {
    window.setTimeout(function () {
      loadDelayComplete = true;
      maybeShowInitialPrompt();
    }, FIRST_PROMPT_DELAY);
  });

  window.addEventListener("beforeinstallprompt", function (event) {
    if (!isAndroid() || isInstalled() || isDismissed()) return;
    event.preventDefault();
    deferredPrompt = event;
    setInstallMode(true);
    maybeShowInitialPrompt();
  });

  window.addEventListener("appinstalled", markInstalled);

  if (closeBtn) {
    closeBtn.addEventListener("click", function () {
      dismissPrompt();
    });
  }

  if (installBtn) {
    installBtn.addEventListener("click", async function () {
      if (!canShowPrompt()) return;

      var promptEvent = deferredPrompt;
      if (!promptEvent) {
        dismissPrompt();
        return;
      }

      hidePrompt();
      deferredPrompt = null;

      try {
        promptEvent.prompt();
        var choice = await promptEvent.userChoice;
        if (choice && choice.outcome === "accepted") {
          markInstalled();
          return;
        }
        dismissPrompt();
      } catch (error) {
        console.warn("PWA installation prompt failed:", error);
        setInstallMode(false);
        showPrompt();
      }
    });
  }

  if (isInstalled()) {
    markInstalled();
  }
})();
