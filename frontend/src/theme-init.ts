/**
 * Theme bootstrap.
 *
 * It is declared as its own module entry ahead of the app bundle, so the stored
 * (or system) preference decides the first paint — a dark-mode visit never
 * flashes a white page before Vue mounts. The `dark` class on <html> stays the
 * single source of truth; `ThemeToggle.vue` only mirrors it afterwards.
 *
 * It lives in a file rather than inline in `index.html` because the deployment
 * serves a strict Content-Security-Policy (`script-src 'self'`, no inline or
 * hash source), so an inline block would be blocked outright. Pointing the tag
 * at this file lets Vite fold it into the content-hashed `/assets` bundle, which
 * the policy already allows.
 */
let stored: string | null = null;
try {
  stored = localStorage.getItem("theme");
} catch {
  /* Storage can be blocked; the system preference still applies. */
}

const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
document.documentElement.classList.toggle("dark", stored ? stored === "dark" : prefersDark);
