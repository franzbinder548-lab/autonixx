describe('Autonix trade activity storage', () => {
  beforeEach(() => {
    jest.resetModules();
    localStorage.clear();
    window.AUTONIX_USER_ID = 'user-123';
    delete window.AutonixTradeActivity;
    require('../static/js/trade-activity.js');
  });

  afterEach(() => {
    delete window.AutonixTradeActivity;
    delete window.AUTONIX_USER_ID;
  });

  it('stores completed trades in the current Autonix user history', () => {
    expect(window.AutonixTradeActivity.record({
      type: 'Basic Bot',
      selection: 'DIFFER 4',
      stake: 10,
      pl: -10,
      time: '10:00:00',
    })).toBe(true);

    expect(JSON.parse(localStorage.getItem('autonix-trade-history:user-123'))).toEqual([
      {
        type: 'Basic Bot',
        selection: 'DIFFER 4',
        entry: '—',
        exit: '—',
        stake: 10,
        pl: -10,
        time: '10:00:00',
      },
    ]);
    expect(localStorage.getItem('autonix-trade-history')).toBeNull();
  });
});
