(function () {
  "use strict";

  function getStorageKey() {
    return window.AUTONIX_USER_ID
      ? "autonix-trade-history:" + window.AUTONIX_USER_ID
      : "autonix-trade-history";
  }

  function load() {
    try {
      var stored = localStorage.getItem(getStorageKey());
      if (!stored) return [];
      var records = JSON.parse(stored);
      if (!Array.isArray(records)) throw new TypeError("Trade history must be an array.");
      return records;
    } catch (error) {
      console.error("Unable to load Autonix trade history.", error);
      return [];
    }
  }

  function record(trade) {
    try {
      var history = load();
      history.unshift({
        type: String(trade.type || "Trade"),
        selection: String(trade.selection || "—"),
        entry: String(trade.entry == null ? "—" : trade.entry),
        exit: String(trade.exit == null ? "—" : trade.exit),
        stake: Number(trade.stake) || 0,
        pl: Number(trade.pl) || 0,
        time: String(trade.time || new Date().toLocaleTimeString()),
      });
      if (history.length > 2000) history.length = 2000;
      localStorage.setItem(getStorageKey(), JSON.stringify(history));
      window.dispatchEvent(new CustomEvent("autonixTradeActivityUpdated"));
      return true;
    } catch (error) {
      console.error("Unable to save Autonix trade history.", error);
      return false;
    }
  }

  window.AutonixTradeActivity = { load: load, record: record };
})();
