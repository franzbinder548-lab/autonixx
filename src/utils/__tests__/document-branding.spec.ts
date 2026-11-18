describe('applyPrimaryColorFromConfig', () => {
    afterEach(() => {
        // The CSS vars are set on the shared jsdom <html>; clear between cases.
        document.documentElement.removeAttribute('style');
        jest.resetModules();
    });

    it('applies brand.config.json colors.primary to the brand CSS vars', () => {
        jest.isolateModules(() => {
            jest.doMock('../../../brand.config.json', () => ({ colors: { primary: '#ff00ff' } }));
            const { applyPrimaryColorFromConfig } = require('../document-branding');

            applyPrimaryColorFromConfig();

            const root = document.documentElement;
            // applyPrimaryColor() sets both the semantic name and the legacy alias the UI reads.
            expect(root.style.getPropertyValue('--brand-primary')).toBe('#ff00ff');
            expect(root.style.getPropertyValue('--brand-red-coral')).toBe('#ff00ff');
        });
    });

    it('is a no-op when colors.primary is absent', () => {
        jest.isolateModules(() => {
            jest.doMock('../../../brand.config.json', () => ({ colors: {} }));
            const { applyPrimaryColorFromConfig } = require('../document-branding');

            applyPrimaryColorFromConfig();

            expect(document.documentElement.style.getPropertyValue('--brand-primary')).toBe('');
        });
    });
});

describe('applyFaviconFromLogo', () => {
    const RealImage = global.Image;
    let imageProbeCount = 0;
    let probedSrcs: string[] = [];

    beforeEach(() => {
        imageProbeCount = 0;
        probedSrcs = [];
        // Minimal Image stub that records probe attempts (each candidate sets `src`).
        // @ts-expect-error - partial stub is enough for the probing path under test.
        global.Image = class {
            onload: (() => void) | null = null;
            onerror: (() => void) | null = null;
            set src(value: string) {
                imageProbeCount += 1;
                probedSrcs.push(value);
            }
        };
    });

    afterEach(() => {
        global.Image = RealImage;
        jest.resetModules();
    });

    it('probes logo candidates in standalone (non-preview) builds', () => {
        jest.isolateModules(() => {
            jest.doMock('../is-preview-mode', () => ({ isPreviewMode: () => false }));
            const { applyFaviconFromLogo } = require('../document-branding');

            applyFaviconFromLogo();

            expect(imageProbeCount).toBeGreaterThan(0);
        });
    });

    it('sets the Autonix favicon without probing when no logo is configured', () => {
        jest.isolateModules(() => {
            jest.doMock('../is-preview-mode', () => ({ isPreviewMode: () => false }));
            jest.doMock('../../../brand.config.json', () => ({
                platform: { name: 'Quokka', logo_path: null },
                colors: { primary: '#abcdef' },
            }));
            const { applyFaviconFromLogo } = require('../document-branding');

            applyFaviconFromLogo();

            // The BFF said no partner logo ships — no candidate may be requested (no 404 noise)…
            expect(imageProbeCount).toBe(0);
            const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
            expect(link?.getAttribute('href')).toBe('/static/icons/autonix-favicon-32.png');
        });
    });

    it('probes only the exact logo_path the BFF recorded', () => {
        jest.isolateModules(() => {
            jest.doMock('../is-preview-mode', () => ({ isPreviewMode: () => false }));
            jest.doMock('../../../brand.config.json', () => ({
                platform: { name: 'Quokka', logo_path: '/logo.webp' },
                colors: { primary: '#abcdef' },
            }));
            const { applyFaviconFromLogo } = require('../document-branding');

            applyFaviconFromLogo();

            // The BFF knows the real extension — no guessing across candidates.
            expect(probedSrcs).toEqual(['/logo.webp']);
        });
    });

    it('sets the Autonix favicon without probing in the preview build', () => {
        jest.isolateModules(() => {
            jest.doMock('../is-preview-mode', () => ({ isPreviewMode: () => true }));
            jest.doMock('../../../brand.config.json', () => ({
                platform: { name: 'Quokka' },
                colors: { primary: '#abcdef' },
            }));
            const { applyFaviconFromLogo } = require('../document-branding');

            applyFaviconFromLogo();

            // The preview build ships no public/logo.*, so it must not probe files…
            expect(imageProbeCount).toBe(0);
            const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
            expect(link?.getAttribute('href')).toBe('/static/icons/autonix-favicon-32.png');
        });
    });

    it('falls back to the Autonix favicon when no logo candidate loads', () => {
        // Stub Image so every probed candidate fails — drives the chain to exhaustion.
        // @ts-expect-error - partial stub is enough for the failing-probe path under test.
        global.Image = class {
            onload: (() => void) | null = null;
            onerror: (() => void) | null = null;
            set src(_value: string) {
                this.onerror?.();
            }
        };

        jest.isolateModules(() => {
            jest.doMock('../is-preview-mode', () => ({ isPreviewMode: () => false }));
            jest.doMock('../../../brand.config.json', () => ({
                platform: { name: 'Zephyr' },
                colors: { primary: '#3b82f6' },
            }));
            const { applyFaviconFromLogo } = require('../document-branding');

            applyFaviconFromLogo();

            const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
            expect(link?.getAttribute('href')).toBe('/static/icons/autonix-favicon-32.png');
        });
    });
});
