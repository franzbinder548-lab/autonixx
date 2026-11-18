import { applyBrandFont, applyPrimaryColor } from './apply-branding';
import { getAppName, getLogoCandidates } from './branding';
import { isPreviewMode } from './is-preview-mode';
import brandConfig from '../../brand.config.json';

/**
 * Sets the document title from the resolved app name. The BFF injects the partner app
 * name into .env.production (NEXT_PUBLIC_DERIV_APP_NAME) at deploy time, so the deployed
 * app's tab title reflects the partner brand. In preview, the App Builder's
 * PREVIEW_BRANDING `appName` overrides this afterwards.
 */
export function applyDocumentTitle(): void {
    const name = getAppName();
    if (name) document.title = name;
}

/**
 * Points the favicon <link rel="icon"> at the given href, creating the link if needed.
 */
export function setFaviconHref(href: string, type = 'image/png'): void {
    let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!link) {
        link = document.createElement('link');
        link.rel = 'icon';
        document.head.appendChild(link);
    }
    link.type = type;
    link.href = href;
}

/**
 * Uses the partner logo (public/logo.<ext>, written by the BFF at deploy time) as the
 * favicon when present. The candidates come from brand.config.json platform.logo_path
 * (see getLogoCandidates) — the exact file when a logo ships, none when it doesn't.
 * Each candidate is loaded first so the favicon is only swapped if the file actually
 * exists; if none load, falls back to the Autonix app icon.
 */
export function applyFaviconFromLogo(): void {
    // The static preview build ships no public/logo.*, so there's nothing to probe — set
    // the Autonix icon directly (avoiding pointless 404s).
    if (isPreviewMode()) {
        setFaviconHref('/static/icons/autonix-favicon-32.png');
        return;
    }

    const candidates = getLogoCandidates();
    const tryCandidate = (index: number): void => {
        if (index >= candidates.length) {
            setFaviconHref('/static/icons/autonix-favicon-32.png');
            return;
        }
        const src = candidates[index];
        const img = new Image();
        img.onload = () => setFaviconHref(src);
        img.onerror = () => tryCandidate(index + 1);
        img.src = src;
    };
    tryCandidate(0);
}

/**
 * Loads the partner's configured web font at startup. The BFF writes the chosen
 * font family into brand.config.json (typography.font_family.primary) at deploy
 * time; we load it and apply the family stack to --brand-font-primary. Unknown /
 * custom stacks (e.g. the default system stack) are left as-is.
 */
export function applyBrandFontFromConfig(): void {
    const family = brandConfig?.typography?.font_family?.primary;
    if (family) applyBrandFont(family);
}

/**
 * Applies the partner primary color baked into brand.config.json (colors.primary). The BFF
 * writes config.branding.primaryColor into colors.primary at deploy time (the single source of
 * truth — the same value generate-brand-css.js bakes into _themes.scss); we apply it at startup
 * via the same --brand-primary / --brand-red-coral vars the live preview uses. A subsequent
 * PREVIEW_BRANDING message overrides this.
 */
export function applyPrimaryColorFromConfig(): void {
    const color = brandConfig?.colors?.primary;
    if (color) applyPrimaryColor(color);
}
