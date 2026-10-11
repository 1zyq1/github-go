<script setup lang="ts">
import { onMounted, ref } from "vue";

/**
 * Light/dark switch, matching the blog's toggle.
 *
 * The `dark` class on <html> is the source of truth — it is already set by
 * `src/theme-init.ts` before the first paint, so this component only mirrors it
 * for the icon and writes the choice back to storage.
 */
const dark = ref(false);

function syncFromDocument(): void {
  dark.value = document.documentElement.classList.contains("dark");
}

function toggle(): void {
  const next = document.documentElement.classList.toggle("dark");
  try {
    localStorage.setItem("theme", next ? "dark" : "light");
  } catch {
    /* Storage can be blocked; the switch still applies to this page view. */
  }
  syncFromDocument();
}

onMounted(syncFromDocument);
</script>

<template>
  <button
    class="icon-button theme-toggle"
    type="button"
    data-test="theme-toggle"
    :aria-pressed="dark"
    :aria-label="dark ? '切换到浅色主题' : '切换到深色主题'"
    :title="dark ? '切换到浅色主题' : '切换到深色主题'"
    @click="toggle"
  >
    <svg
      v-if="dark"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="m4.93 4.93 1.41 1.41" />
      <path d="m17.66 17.66 1.41 1.41" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="m6.34 17.66-1.41 1.41" />
      <path d="m19.07 4.93-1.41 1.41" />
    </svg>
    <svg
      v-else
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
    </svg>
  </button>
</template>
