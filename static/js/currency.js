(function () {
  var STORAGE_KEY = "display_currency";
  var RATE_CACHE_KEY = "usd_to_kes_rate";
  var currency = localStorage.getItem(STORAGE_KEY) === "KES" ? "KES" : "USD";
  var rate = Number(localStorage.getItem(RATE_CACHE_KEY)) || 0;

  function number(value) {
    return new Intl.NumberFormat("en-KE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  }

  function formatMoney(amount) {
    var value = Number(amount) || 0;
    if (currency === "KES" && rate > 0) {
      value = Math.round((value * rate + Number.EPSILON) * 100) / 100;
      return "KSh " + number(value);
    }
    return "$" + number(value);
  }

  function formatAccount(amount, sourceCurrency) {
    var value = Number(amount);
    var source = String(sourceCurrency || "USD").toUpperCase();
    if (!Number.isFinite(value)) return "—";
    if (source === currency) return (currency === "KES" ? "KSh " : "$") + number(value);
    if (rate > 0 && source === "USD" && currency === "KES") {
      return "KSh " + number(value * rate);
    }
    if (rate > 0 && source === "KES" && currency === "USD") {
      return "$" + number(value / rate);
    }
    return number(value) + " " + source;
  }

  function renderAccountBalances() {
    document.querySelectorAll("[data-account-balance]").forEach(function (element) {
      var value = element.getAttribute("data-account-amount");
      if (value === null) return;
      element.textContent = formatAccount(value, element.getAttribute("data-account-currency"));
    });
  }

  function formatSignedMoney(amount) {
    var value = Number(amount) || 0;
    return (value >= 0 ? "+" : "-") + formatMoney(Math.abs(value));
  }

  function render() {
    document.querySelectorAll("[data-usd-amount]").forEach(function (element) {
      element.textContent = formatMoney(element.getAttribute("data-usd-amount"));
    });
    document.querySelectorAll("[data-display-currency]").forEach(function (element) {
      element.textContent = currency;
    });
    document.querySelectorAll("[data-currency-label]").forEach(function (element) {
      element.textContent = element.getAttribute("data-currency-label") + " (" + currency + ")";
    });
    document.querySelectorAll("#display-currency-select").forEach(function (select) {
      select.value = currency;
    });
    renderAccountBalances();
  }

  function notifyCurrencyChange() {
    window.dispatchEvent(new CustomEvent("displaycurrencychange", {
      detail: { currency: currency, rate: rate },
    }));
  }

  function applyCurrency(nextCurrency) {
    currency = nextCurrency === "KES" ? "KES" : "USD";
    localStorage.setItem(STORAGE_KEY, currency);
    render();
    notifyCurrencyChange();
  }

  function setCurrency(nextCurrency) {
    applyCurrency(nextCurrency);
    fetch("/api/display-currency", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ currency: currency }),
    }).then(function (response) {
      if (!response.ok) throw new Error("Currency preference could not be saved.");
    }).catch(function (error) {
      console.error(error);
    });
  }

  document.addEventListener("derivBalance", function (event) {
    if (!event.detail) return;
    var active = window.SESSION_DATA && window.SESSION_DATA.activeAccount;
    var accountId = event.detail.loginid;
    if (active && accountId && active.account !== accountId) return;
    document.querySelectorAll("[data-account-balance]").forEach(function (element) {
      var elementAccount = element.getAttribute("data-account-id");
      var isActiveBalance = element.hasAttribute("data-account-active-balance");
      if (elementAccount && accountId && elementAccount !== accountId && !isActiveBalance) return;
      if (isActiveBalance && accountId) element.setAttribute("data-account-id", accountId);
      element.setAttribute("data-account-amount", event.detail.balance);
      element.setAttribute("data-account-currency", event.detail.currency || "USD");
    });
    renderAccountBalances();
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("#display-currency-select").forEach(function (select) {
      select.value = currency;
      select.addEventListener("change", function () {
        setCurrency(select.value);
      });
    });
    render();
    fetch("/api/display-currency", { credentials: "include" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (!data) return;
        if (data.currency === "USD" || data.currency === "KES") {
          if (data.currency !== currency) applyCurrency(data.currency);
          return;
        }
        fetch("/api/display-currency", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ currency: currency }),
        }).then(function (response) {
          if (!response.ok) throw new Error("Currency preference could not be saved.");
        }).catch(function (error) {
          console.error(error);
        });
      })
      .catch(function (error) {
        console.error("Currency preference could not be loaded.", error);
      });
    fetch("/api/exchange-rate", { credentials: "include" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (!data || !Number.isFinite(Number(data.rate)) || Number(data.rate) <= 0) return;
        rate = Number(data.rate);
        localStorage.setItem(RATE_CACHE_KEY, String(rate));
        render();
        notifyCurrencyChange();
      })
      .catch(function () {
        render();
      });
  });

  window.addEventListener("storage", function (event) {
    if (event.key === STORAGE_KEY && (event.newValue === "USD" || event.newValue === "KES")) {
      currency = event.newValue;
      render();
      notifyCurrencyChange();
    }
    if (event.key === RATE_CACHE_KEY) {
      rate = Number(event.newValue) || 0;
      render();
      notifyCurrencyChange();
    }
  });

  window.AutonixCurrency = {
    format: formatMoney,
    formatAccount: formatAccount,
    formatSigned: formatSignedMoney,
    get: function () { return currency; },
    getRate: function () { return rate; },
    renderAccountBalances: renderAccountBalances,
  };
})();
