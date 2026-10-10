<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { AuthError, logout as endSession, fetchSession, type Session } from "./api/client";
import AppHeader from "./components/AppHeader.vue";
import AuthPanel from "./components/AuthPanel.vue";
import FeatureStrip from "./components/FeatureStrip.vue";
import StatsPanel from "./components/StatsPanel.vue";
import UrlConverter from "./components/UrlConverter.vue";

type SessionState = "loading" | "anonymous" | "authenticated";
type Page = "accelerator" | "stats";

const SESSION_UNAVAILABLE_MESSAGE = "无法确认登录状态，请刷新页面重试。";
const SESSION_EXPIRED_MESSAGE = "登录状态已失效，请重新登录。";

/**
 * Both pages ship in one bundle, so the path is read once instead of pulling in
 * a router for a single branch. The trailing slash is dropped because the
 * server serves the shell for both forms.
 */
function readPage(): Page {
  const path = window.location.pathname.replace(/\/+$/, "");
  return path === "/stats" ? "stats" : "accelerator";
}

const page = ref<Page>(readPage());

const sessionState = ref<SessionState>("loading");
const session = ref<Session | null>(null);
const limited = ref(false);
const sessionError = ref("");
/** The verification mail was followed successfully. */
const confirmed = ref(false);
/** The verification link was already spent or has expired. */
const verifyFailed = ref(false);
/** The blog SSO sign-in completed successfully. */
const ssoConfirmed = ref(false);
/** The blog SSO sign-in failed with this message. */
const ssoError = ref("");
/** A pending return to the SSO authorize URL after a local sign-in. */
const ssoReturn = ref("");

const quotaExhausted = computed(() => {
  const quota = session.value?.quota ?? null;
  return quota !== null && quota.remaining <= 0;
});

/** Re-read the session from the server; the server is the only authority on it. */
async function refreshSession(): Promise<void> {
  try {
    const next = await fetchSession();
    session.value = next;
    sessionState.value = next.authenticated ? "authenticated" : "anonymous";
    sessionError.value = "";
    const quota = next.quota;
    if (quota === null || quota.remaining > 0) {
      limited.value = false;
    }
    if (next.authenticated && ssoReturn.value) {
      // A blog sign-in was waiting for a local session: finish the SSO handshake.
      const target = ssoReturn.value;
      ssoReturn.value = "";
      if (target.startsWith("/") && !target.startsWith("//")) {
        window.location.replace(target);
        return;
      }
    }
  } catch {
    session.value = null;
    sessionState.value = "anonymous";
    sessionError.value = SESSION_UNAVAILABLE_MESSAGE;
  }
}

onMounted((): void => {
  // The server redirects here with a flag so the outcome survives the hop out of the mail client.
  const parameters = new URLSearchParams(window.location.search);
  limited.value = parameters.get("limited") === "1";
  confirmed.value = parameters.get("verified") === "1";
  verifyFailed.value = parameters.get("verify_failed") === "1";
  // The SSO outcome (sso=ok|error) and the pending authorize return (sso_return).
  ssoConfirmed.value = parameters.get("sso") === "ok";
  if (parameters.get("sso") === "error") {
    ssoError.value = parameters.get("message") || "博客账号登录失败，请重试。";
  }
  const returnTo = parameters.get("sso_return") ?? "";
  if (returnTo.startsWith("/") && !returnTo.startsWith("//")) {
    ssoReturn.value = returnTo;
  }
  void refreshSession();
});

async function handleLogout(): Promise<void> {
  try {
    await endSession();
  } catch (error: unknown) {
    sessionError.value = error instanceof AuthError ? error.message : SESSION_UNAVAILABLE_MESSAGE;
    return;
  }
  session.value = null;
  sessionState.value = "anonymous";
  limited.value = false;
  sessionError.value = "";
}

async function handleUnauthorized(): Promise<void> {
  await refreshSession();
  sessionError.value = SESSION_EXPIRED_MESSAGE;
}
</script>

<template>
  <div class="page">
    <AppHeader
      :username="session?.username ?? null"
      :quota="session?.quota ?? null"
      :page="page"
      @logout="handleLogout"
    />
    <main id="main" class="page__main">
      <p v-if="sessionError" class="notice notice--warning" role="alert">{{ sessionError }}</p>
      <StatsPanel v-if="page === 'stats'" />
      <template v-else>
        <p v-if="confirmed" class="notice" role="status" data-test="verified-notice">
          邮箱验证成功，已自动登录。
        </p>
        <p v-if="verifyFailed" class="notice notice--warning" role="alert" data-test="verify-failed">
          验证链接无效或已过期，请重新获取。
        </p>
        <p v-if="ssoConfirmed" class="notice" role="status" data-test="sso-notice">
          已通过博客账号登录。
        </p>
        <p v-if="ssoError" class="notice notice--warning" role="alert" data-test="sso-error">
          {{ ssoError }}
        </p>
        <p
          v-if="limited || quotaExhausted"
          class="notice notice--warning"
          role="alert"
          data-test="quota-notice"
        >
          本小时的访问额度已用完，请在额度重置后再试。
        </p>
        <p v-if="sessionState === 'loading'" class="page__status" role="status">
          正在检查登录状态。
        </p>
        <AuthPanel v-else-if="sessionState === 'anonymous'" @authenticated="refreshSession" />
        <UrlConverter
          v-else
          :disabled="quotaExhausted"
          @resolved="refreshSession"
          @unauthorized="handleUnauthorized"
          @quota-exceeded="refreshSession"
        />
      </template>
    </main>
    <FeatureStrip v-if="page === 'accelerator'" />
    <footer class="page__footer">
      <p>仅代理 GitHub 官方文件与页面，不记录用户提交的地址。</p>
    </footer>
  </div>
</template>
