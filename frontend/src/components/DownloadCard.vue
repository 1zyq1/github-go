<script setup lang="ts">
import { computed, ref } from "vue";
import type { ResolveKind, ResolveResult } from "../api/client";

type CardState = "empty" | "loading" | "success" | "error";
type CopyState = "idle" | "copied" | "failed";

const props = defineProps<{
  state: CardState;
  result: ResolveResult | null;
  errorMessage: string;
}>();

const emit = defineEmits<{ reset: [] }>();

const copyState = ref<CopyState>("idle");

const KIND_LABELS: Record<ResolveKind, string> = {
  release: "Release 资产",
  archive: "源码归档",
  raw: "文件内容",
  page: "镜像页面",
};

function describeSource(result: ResolveResult): string {
  const segments = new URL(result.sourceUrl).pathname.split("/").filter((part) => part.length > 0);
  if (result.kind === "page") {
    return segments.join("/");
  }
  if (result.kind === "archive") {
    const repository = segments[1] ?? "archive";
    const refIndex = segments.indexOf("refs");
    const reference = (refIndex >= 0 ? segments[refIndex + 2] : undefined) ?? "";
    const ref = reference.replace(/\.zip$/, "");
    return ref.length > 0 ? `${repository}-${ref}.zip` : `${repository}-archive.zip`;
  }
  const last = segments.at(-1);
  return last === undefined ? "" : decodeURIComponent(last);
}

const subjectName = computed(() => (props.result === null ? "" : describeSource(props.result)));

const isPage = computed(() => props.result?.kind === "page");

const absoluteProxyUrl = computed(() => {
  const result = props.result;
  return result === null ? "" : new URL(result.proxyUrl, window.location.origin).href;
});

const kindLabel = computed(() => {
  const result = props.result;
  return result === null ? "" : KIND_LABELS[result.kind];
});

const statusMessage = computed(() => {
  switch (props.state) {
    case "empty":
      return "粘贴地址后，这里会显示下载链接或镜像页面。";
    case "loading":
      return "正在解析 GitHub 地址。";
    case "success":
      return isPage.value
        ? `已解析：${subjectName.value}，可以在镜像中打开。`
        : `已解析：${subjectName.value}，可以直接下载。`;
    case "error":
      return "";
  }
});

const copyMessage = computed(() => {
  switch (copyState.value) {
    case "idle":
      return "";
    case "copied":
      return "已复制到剪贴板。";
    case "failed":
      return "无法复制，请手动选中上方地址。";
  }
});

async function copyDownloadLink(): Promise<void> {
  const result = props.result;
  if (result === null) {
    return;
  }
  const clipboard: Clipboard | undefined = navigator.clipboard;
  if (clipboard === undefined) {
    copyState.value = "failed";
    return;
  }
  try {
    await clipboard.writeText(new URL(result.proxyUrl, window.location.origin).href);
    copyState.value = "copied";
  } catch {
    copyState.value = "failed";
  }
}

function resetConverter(): void {
  copyState.value = "idle";
  emit("reset");
}
</script>

<template>
  <section class="result" data-test="result-panel" :data-state="state" aria-label="解析结果">
    <p class="result__status" data-test="result-status" role="status" aria-live="polite">
      {{ statusMessage }}
    </p>

    <div v-if="state === 'loading'" class="result__skeleton" aria-hidden="true">
      <span class="skeleton result__skeleton-badge"></span>
      <span class="skeleton result__skeleton-title"></span>
      <span class="skeleton result__skeleton-action"></span>
    </div>

    <div v-else-if="state === 'error'" class="result__notice">
      <svg
        class="result__notice-icon"
        viewBox="0 0 20 20"
        width="var(--space-5)"
        height="var(--space-5)"
        aria-hidden="true"
        focusable="false"
      >
        <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.75" />
        <path
          d="M10 6v4.5"
          fill="none"
          stroke="currentColor"
          stroke-width="1.75"
          stroke-linecap="round"
        />
        <circle cx="10" cy="13.8" r="1.1" fill="currentColor" />
      </svg>
      <p class="result__notice-text">{{ errorMessage }}</p>
    </div>

    <template v-else-if="state === 'success' && result !== null">
      <p class="result__badge">{{ kindLabel }}</p>
      <p class="result__file">{{ subjectName }}</p>
      <p v-if="isPage" class="result__address" data-test="mirror-address">
        <code>{{ absoluteProxyUrl }}</code>
      </p>
      <p class="result__origin">
        {{
          isPage
            ? "同源镜像页面，脚本已剥离以避免重复渲染。"
            : "同源代理链接，直连后端不经过第三方 CDN。"
        }}
      </p>
      <div class="result__actions">
        <a
          class="button button--primary"
          :data-test="isPage ? 'mirror-link' : 'download-link'"
          :href="result.proxyUrl"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ isPage ? "打开反代地址" : "在新标签页下载" }}
        </a>
        <button
          class="button button--quiet"
          type="button"
          data-test="copy-button"
          @click="copyDownloadLink"
        >
          复制链接
        </button>
        <button
          class="button button--quiet"
          type="button"
          data-test="reset-button"
          @click="resetConverter"
        >
          重新解析
        </button>
      </div>
      <p class="result__copy-feedback" data-test="copy-feedback" role="status">
        {{ copyMessage }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.result {
  display: grid;
  align-content: start;
  gap: var(--space-3);
  min-height: var(--space-16);
  padding: var(--space-5);
  border: var(--border-hairline);
  border-radius: var(--radius-surface);
  background-color: var(--surface-raised);
}

.result__status {
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.result__status:empty {
  display: none;
}

.result__skeleton {
  display: grid;
  gap: var(--space-3);
}

.result__skeleton-badge {
  width: var(--space-16);
  height: var(--space-6);
}

.result__skeleton-title {
  width: 75%;
  height: var(--space-6);
}

.result__skeleton-action {
  width: 60%;
  height: var(--target-min);
}

/* Offset the widest bar so the three do not breathe in lockstep. */
@media (prefers-reduced-motion: no-preference) {
  .result__skeleton-action {
    animation-delay: var(--duration-standard);
  }
}

.result__notice {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-control);
  background-color: var(--status-danger-soft);
  color: var(--status-danger);
}

.result__notice-icon {
  flex-shrink: 0;
  margin-top: var(--space-1);
}

.result__notice-text {
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
  overflow-wrap: anywhere;
}

.result__badge {
  justify-self: start;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-badge);
  background-color: var(--accent-soft);
  color: var(--text-secondary);
  font-size: var(--text-caption-size);
  font-weight: 500;
  line-height: var(--text-caption-leading);
}

.result__file {
  font-size: var(--text-subheading-size);
  font-weight: 500;
  line-height: var(--text-subheading-leading);
  overflow-wrap: anywhere;
}

.result__address {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  background-color: var(--surface-sunken);
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
  overflow-wrap: anywhere;
}

.result__origin {
  color: var(--text-secondary);
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
}

.result__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-1);
}

.result__copy-feedback {
  min-height: var(--text-body-leading);
  color: var(--text-secondary);
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
}

.result__copy-feedback:empty {
  display: none;
}
</style>
