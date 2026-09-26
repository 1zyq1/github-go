<script setup lang="ts">
import { ref } from "vue";
import { ResolveError, type ResolveResult, resolveSource } from "../api/client";
import DownloadCard from "./DownloadCard.vue";

type ConverterState = "empty" | "loading" | "success" | "error";

const EMPTY_FIELD_MESSAGE = "请填写主页名或项目地址。";
const UNEXPECTED_ERROR_MESSAGE = "解析失败，请稍后重试。";

const props = withDefaults(defineProps<{ disabled?: boolean }>(), { disabled: false });
const emit = defineEmits<{ resolved: []; unauthorized: []; "quota-exceeded": [] }>();

const url = ref("");
const state = ref<ConverterState>("empty");
const result = ref<ResolveResult | null>(null);
const errorMessage = ref("");
const inputElement = ref<HTMLInputElement | null>(null);

/** Report an access refusal so the app can re-check the session or the allowance. */
function reportRefusal(error: ResolveError): void {
  if (error.status === 401) {
    emit("unauthorized");
    return;
  }
  if (error.status === 429) {
    emit("quota-exceeded");
  }
}

async function submit(): Promise<void> {
  if (state.value === "loading" || props.disabled) {
    return;
  }
  const candidate = url.value.trim();
  if (candidate.length === 0) {
    state.value = "error";
    result.value = null;
    errorMessage.value = EMPTY_FIELD_MESSAGE;
    return;
  }
  state.value = "loading";
  result.value = null;
  errorMessage.value = "";
  try {
    result.value = await resolveSource(candidate);
    state.value = "success";
    emit("resolved");
  } catch (error: unknown) {
    result.value = null;
    errorMessage.value = error instanceof ResolveError ? error.message : UNEXPECTED_ERROR_MESSAGE;
    state.value = "error";
    if (error instanceof ResolveError) {
      reportRefusal(error);
    }
  }
}

function reset(): void {
  url.value = "";
  result.value = null;
  errorMessage.value = "";
  state.value = "empty";
  inputElement.value?.focus();
}
</script>

<template>
  <section class="converter">
    <form class="converter__form" novalidate @submit.prevent="submit">
      <div class="field">
        <label class="field__label" for="source-url">GitHub 主页名或项目地址</label>
        <p class="field__hint" id="source-url-hint">
          直接填主页名（<code>1zyq1</code>）或项目（<code>acme/widgets</code>）就能拿到反代地址；
          也接受完整链接，包括 Release 资产、源码归档（<code>archive/refs</code>）与
          <code>raw</code> / <code>blob</code> 文件。
        </p>
        <input
          id="source-url"
          ref="inputElement"
          v-model="url"
          class="field__input"
          type="text"
          name="url"
          inputmode="url"
          enterkeyhint="go"
          autocomplete="off"
          spellcheck="false"
          placeholder="1zyq1 或 acme/widgets"
          :aria-invalid="state === 'error'"
          :aria-describedby="
            state === 'error' ? 'source-url-hint source-url-error' : 'source-url-hint'
          "
        />
        <p
          id="source-url-error"
          class="field__error"
          data-test="field-error"
          role="alert"
        >
          {{ errorMessage }}
        </p>
      </div>
      <button
        class="button button--primary converter__submit"
        type="submit"
        :disabled="state === 'loading' || disabled"
        :aria-busy="state === 'loading'"
      >
        {{ state === "loading" ? "正在解析" : "生成反代链接" }}
      </button>
    </form>
    <DownloadCard :state="state" :result="result" :error-message="errorMessage" @reset="reset" />
  </section>
</template>

<style scoped>
.converter__form {
  display: grid;
  align-content: start;
  gap: var(--space-4);
}

.converter__submit {
  justify-self: start;
}
</style>
