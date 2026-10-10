<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import {
  AuthError,
  type AuthMode,
  type CredentialProblem,
  credentialProblem,
  emailProblem,
  fetchSession,
  fetchSsoProviders,
  login,
  register,
  requestVerification,
} from "../api/client";

const emit = defineEmits<{ authenticated: [] }>();

const UNEXPECTED_ERROR_MESSAGE = "操作失败，请稍后重试。";
const PENDING_MESSAGE = "验证邮件已发送。";
const RESENT_MESSAGE = "验证邮件已重新发送。";

const mode = ref<AuthMode>("login");
const username = ref("");
const password = ref("");
const email = ref("");
const submitting = ref(false);
const usernameError = ref("");
const passwordError = ref("");
const emailError = ref("");
const formError = ref("");
const notice = ref("");
/** True once the credentials were accepted but the address is still unconfirmed. */
const awaiting = ref(false);
/** The address the last verification mail went to. */
const pendingEmail = ref("");
/** Whether the blog cross-site sign-in is configured, so its button may show. */
const ssoAvailable = ref<boolean | null>(null);
const usernameInput = ref<HTMLInputElement | null>(null);
const passwordInput = ref<HTMLInputElement | null>(null);
const emailInput = ref<HTMLInputElement | null>(null);

const submitLabel = computed(() => {
  if (mode.value === "login") {
    return submitting.value ? "正在登录" : "登录";
  }
  return submitting.value ? "正在注册" : "注册";
});

const pendingHint = computed(() =>
  pendingEmail.value.length > 0
    ? `验证链接已发送到 ${pendingEmail.value}。请打开邮件完成验证，验证后本页会自动更新。`
    : "该账号还没有邮箱地址，无法发送验证链接。请填写地址后重新发送。",
);

function clearErrors(): void {
  usernameError.value = "";
  passwordError.value = "";
  emailError.value = "";
  formError.value = "";
}

function selectMode(next: AuthMode): void {
  if (submitting.value || next === mode.value) {
    return;
  }
  mode.value = next;
  clearErrors();
}

/** Reject the input locally when it cannot possibly be accepted by the API. */
function reject(problem: CredentialProblem): void {
  if (problem.field === "username") {
    usernameError.value = problem.message;
    usernameInput.value?.focus();
    return;
  }
  if (problem.field === "email") {
    emailError.value = problem.message;
    emailInput.value?.focus();
    return;
  }
  passwordError.value = problem.message;
  passwordInput.value?.focus();
}

/** Report a failed request on the field that owns the reason, when one does. */
function reportFailure(error: unknown): void {
  if (!(error instanceof AuthError)) {
    formError.value = UNEXPECTED_ERROR_MESSAGE;
    return;
  }
  if (error.code === "username_taken") {
    usernameError.value = error.message;
    usernameInput.value?.focus();
    return;
  }
  if (error.code === "email_taken") {
    emailError.value = error.message;
    emailInput.value?.focus();
    return;
  }
  formError.value = error.message;
}

/** Whether the pending view is listening for the confirmation. */
let watching = false;
/** Whether a probe is in flight, so overlapping events share one request. */
let probing = false;

/** Stop listening for the confirmation. */
function stopWatching(): void {
  if (!watching) {
    return;
  }
  watching = false;
  document.removeEventListener("visibilitychange", handleReturn);
  window.removeEventListener("focus", handleReturn);
}

/**
 * Ask whether the session exists yet.
 *
 * The mailed link is opened in whichever tab the mail client picks, and the
 * cookie it sets belongs to the whole browser, so looking again when the
 * visitor comes back is enough to notice the confirmation. A failed look is
 * dropped rather than reported: the resend button and a reload remain.
 */
async function probeConfirmation(): Promise<void> {
  if (probing) {
    return;
  }
  probing = true;
  try {
    const session = await fetchSession().catch(() => null);
    if (session?.authenticated) {
      stopWatching();
      emit("authenticated");
    }
  } finally {
    probing = false;
  }
}

/** Re-check on the two ways a visitor returns: the tab wakes, or the window does. */
function handleReturn(): void {
  if (!watching || document.visibilityState === "hidden") {
    return;
  }
  void probeConfirmation();
}

/** Listen for the visitor coming back, which is when the cookie would be here. */
function startWatching(): void {
  if (watching) {
    return;
  }
  watching = true;
  document.addEventListener("visibilitychange", handleReturn);
  window.addEventListener("focus", handleReturn);
}

/** The credentials were accepted, but access still waits on the mailed link. */
function awaitConfirmation(address: string): void {
  email.value = address;
  pendingEmail.value = address;
  notice.value = PENDING_MESSAGE;
  awaiting.value = true;
  startWatching();
  void nextTick(() => {
    emailInput.value?.focus();
  });
}

async function submit(): Promise<void> {
  if (submitting.value) {
    return;
  }
  clearErrors();
  const name = username.value.trim();
  const address = email.value.trim();
  const problem = credentialProblem(mode.value, name, password.value, address);
  if (problem !== null) {
    reject(problem);
    return;
  }

  submitting.value = true;
  try {
    const session =
      mode.value === "login"
        ? await login(name, password.value)
        : await register(name, password.value, address);
    if (!session.authenticated) {
      awaitConfirmation(session.email ?? address);
      return;
    }
    password.value = "";
    emit("authenticated");
  } catch (error: unknown) {
    reportFailure(error);
  } finally {
    submitting.value = false;
  }
}

/** Mail another link, which is also how an account with no address gets one. */
async function resend(): Promise<void> {
  if (submitting.value) {
    return;
  }
  clearErrors();
  const name = username.value.trim();
  const address = email.value.trim();
  const problem = emailProblem(address);
  if (problem !== null) {
    reject(problem);
    return;
  }

  submitting.value = true;
  try {
    const session = await requestVerification(name, password.value, address);
    pendingEmail.value = session.email ?? address;
    email.value = pendingEmail.value;
    notice.value = RESENT_MESSAGE;
  } catch (error: unknown) {
    reportFailure(error);
  } finally {
    submitting.value = false;
  }
}

function backToForm(): void {
  if (submitting.value) {
    return;
  }
  stopWatching();
  awaiting.value = false;
  notice.value = "";
  mode.value = "login";
  clearErrors();
  void nextTick(() => {
    passwordInput.value?.focus();
  });
}

onMounted(() => {
  void fetchSsoProviders().then((providers) => {
    ssoAvailable.value = providers.blog;
  });
});

onUnmounted(stopWatching);
</script>

<template>
  <section class="auth" aria-labelledby="auth-title">
    <h2 class="auth__title" id="auth-title">
      {{ awaiting ? "验证邮箱" : mode === "login" ? "登录后使用加速服务" : "注册新账号" }}
    </h2>

    <template v-if="awaiting">
      <p class="auth__lead">{{ pendingHint }}</p>
      <p class="auth__notice" role="status" data-test="pending-notice">{{ notice }}</p>

      <form class="auth__form" novalidate @submit.prevent="resend">
        <div class="field">
          <label class="field__label" for="auth-email">邮箱地址</label>
          <p class="field__hint" id="auth-email-hint">用于接收验证链接。</p>
          <input
            id="auth-email"
            ref="emailInput"
            v-model="email"
            class="field__input field__input--text"
            type="email"
            name="email"
            autocomplete="email"
            autocapitalize="none"
            spellcheck="false"
            :aria-invalid="emailError.length > 0"
            :aria-describedby="
              emailError.length > 0 ? 'auth-email-hint auth-email-error' : 'auth-email-hint'
            "
          />
          <p id="auth-email-error" class="field__error" data-test="email-error" role="alert">
            {{ emailError }}
          </p>
        </div>

        <p class="field__error" data-test="form-error" role="alert">{{ formError }}</p>

        <div class="auth__actions">
          <button class="button button--primary" type="submit" :disabled="submitting">
            {{ submitting ? "正在发送" : "重新发送验证邮件" }}
          </button>
          <button
            class="button button--quiet"
            type="button"
            :disabled="submitting"
            @click="backToForm"
          >
            返回登录
          </button>
        </div>
      </form>
    </template>

    <template v-else>
      <p class="auth__lead">
        服务按账号计量：每个账号每小时可发起 10 次文件或页面访问。注册需要验证邮箱，登录状态保存在本站 Cookie 中。
      </p>

      <div class="auth__modes" role="radiogroup" aria-label="选择登录或注册">
        <label class="auth__mode" :class="{ 'auth__mode--active': mode === 'login' }">
          <input
            class="auth__radio"
            type="radio"
            name="auth-mode"
            value="login"
            :checked="mode === 'login'"
            :disabled="submitting"
            @change="selectMode('login')"
          />
          <span>登录</span>
        </label>
        <label class="auth__mode" :class="{ 'auth__mode--active': mode === 'register' }">
          <input
            class="auth__radio"
            type="radio"
            name="auth-mode"
            value="register"
            :checked="mode === 'register'"
            :disabled="submitting"
            @change="selectMode('register')"
          />
          <span>注册</span>
        </label>
      </div>

      <form class="auth__form" novalidate @submit.prevent="submit">
        <div class="field">
          <label class="field__label" for="auth-username">用户名</label>
          <p class="field__hint" id="auth-username-hint">
            3 至 32 位字母、数字、下划线或连字符，须以字母或数字开头和结尾。
          </p>
          <input
            id="auth-username"
            ref="usernameInput"
            v-model="username"
            class="field__input field__input--text"
            type="text"
            name="username"
            autocomplete="username"
            autocapitalize="none"
            spellcheck="false"
            :aria-invalid="usernameError.length > 0"
            :aria-describedby="
              usernameError.length > 0
                ? 'auth-username-hint auth-username-error'
                : 'auth-username-hint'
            "
          />
          <p id="auth-username-error" class="field__error" data-test="username-error" role="alert">
            {{ usernameError }}
          </p>
        </div>

        <div class="field">
          <label class="field__label" for="auth-password">密码</label>
          <p class="field__hint" id="auth-password-hint">
            {{
              mode === "login"
                ? "输入注册时使用的密码。"
                : "8 至 128 位，建议混合字母、数字与符号。"
            }}
          </p>
          <input
            id="auth-password"
            ref="passwordInput"
            v-model="password"
            class="field__input field__input--text"
            type="password"
            name="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            :aria-invalid="passwordError.length > 0"
            :aria-describedby="
              passwordError.length > 0
                ? 'auth-password-hint auth-password-error'
                : 'auth-password-hint'
            "
          />
          <p id="auth-password-error" class="field__error" data-test="password-error" role="alert">
            {{ passwordError }}
          </p>
        </div>

        <div v-if="mode === 'register'" class="field">
          <label class="field__label" for="auth-email">邮箱地址</label>
          <p class="field__hint" id="auth-email-hint">用于接收验证链接。</p>
          <input
            id="auth-email"
            ref="emailInput"
            v-model="email"
            class="field__input field__input--text"
            type="email"
            name="email"
            autocomplete="email"
            autocapitalize="none"
            spellcheck="false"
            :aria-invalid="emailError.length > 0"
            :aria-describedby="
              emailError.length > 0 ? 'auth-email-hint auth-email-error' : 'auth-email-hint'
            "
          />
          <p id="auth-email-error" class="field__error" data-test="email-error" role="alert">
            {{ emailError }}
          </p>
        </div>

        <p class="field__error" data-test="form-error" role="alert">{{ formError }}</p>

        <button class="button button--primary auth__submit" type="submit" :disabled="submitting">
          {{ submitLabel }}
        </button>
      </form>

      <div v-if="ssoAvailable" class="auth__sso">
        <div class="auth__divider"><span>或</span></div>
        <a class="button button--quiet auth__sso-button" href="/api/sso/start">
          用博客账号登录
        </a>
        <p class="auth__sso-hint">第一次用会按邮箱自动绑定或建号。</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.auth {
  display: grid;
  gap: var(--space-5);
  padding: var(--space-6);
  border: var(--border-hairline);
  border-radius: var(--radius-surface);
  background-color: var(--surface-raised);
}

.auth__title {
  font-size: var(--text-subheading-size);
  font-weight: 650;
  line-height: var(--text-subheading-leading);
}

.auth__lead {
  max-width: var(--text-measure);
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.auth__notice {
  color: var(--status-success);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.auth__modes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-1);
  padding: var(--space-1);
  border: var(--border-hairline);
  border-radius: var(--radius-control);
  background-color: var(--surface-sunken);
}

.auth__mode {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: var(--target-min);
  border-radius: var(--radius-badge);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: var(--text-small-size);
  font-weight: 600;
  line-height: 1;
}

.auth__mode--active {
  background-color: var(--surface-raised);
  color: var(--accent);
}

.auth__mode:focus-within {
  outline: var(--focus-outline);
  outline-offset: var(--focus-offset);
}

/* Kept focusable; the segment label is what the user sees and clicks. */
.auth__radio {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  border: 0;
  overflow: hidden;
  white-space: nowrap;
  clip-path: inset(50%);
}

.auth__form {
  display: grid;
  align-content: start;
  gap: var(--space-4);
}

.auth__submit {
  justify-self: start;
}

.auth__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.auth__sso {
  display: grid;
  gap: var(--space-3);
}

.auth__divider {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.auth__divider::before,
.auth__divider::after {
  content: "";
  flex: 1;
  height: 1px;
  background-color: var(--border-hairline);
}

.auth__sso-button {
  width: 100%;
  min-height: var(--target-min);
  justify-content: center;
}

.auth__sso-hint {
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}
</style>
