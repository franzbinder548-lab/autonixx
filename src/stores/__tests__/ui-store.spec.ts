import UiStore from '../ui-store';

describe('UiStore theme', () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it('uses dark mode by default', () => {
        expect(new UiStore().is_dark_mode_on).toBe(true);
    });

    it('does not allow a saved light preference to override the site theme', () => {
        localStorage.setItem('at_theme', 'light');
        localStorage.setItem('theme', 'light');

        expect(new UiStore().is_dark_mode_on).toBe(true);
    });
});
