export type ResourceKind = "release" | "archive" | "raw";
export type PageKind = "page";
export type ResolveKind = ResourceKind | PageKind;

export interface ResolveResult {
  readonly kind: ResolveKind;
  readonly sourceUrl: string;
  readonly proxyUrl: string;
}

export class ResolveError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ResolveError";
    this.code = code;
    this.status = status;
  }
}

const NETWORK_ERROR_MESSAGE = "网络请求失败，请检查连接后重试。";
const FALLBACK_ERROR_MESSAGE = "解析失败，请稍后重试。";
const AUTH_FALLBACK_ERROR_MESSAGE = "操作失败，请稍后重试。";
const INVALID_RESPONSE_MESSAGE = "服务返回了无法识别的结果。";

/** The hourly access allowance an account currently holds. */
export interface Quota {
  readonly limit: number;
  readonly remaining: number;
  readonly resetAfterSeconds: number;
}

/** Who is signed in, and what is left of their hourly allowance. */
export interface Session {
  readonly authenticated: boolean;
  readonly username: string | null;
  readonly quota: Quota | null;
  /** The address on file, reported even while the account waits to be confirmed. */
  readonly email: string | null;
  /** True once the address is confirmed, false while it still has to be, null when unknown. */
  readonly emailVerified: boolean | null;
}

/** Which account operation the panel is performing. */
export type AuthMode = "login" | "register";

/** The first credential rule the input violates, if any. */
export interface CredentialProblem {
  readonly field: "username" | "password" | "email";
  readonly message: string;
}

export class AuthError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "AuthError";
    this.code = code;
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isResourceKind(value: unknown): value is ResourceKind {
  return value === "release" || value === "archive" || value === "raw";
}

function isPageKind(value: unknown): value is PageKind {
  return value === "page";
}

/** Hosts the shorthand form may name explicitly, with or without a scheme. */
const GITHUB_HOST_PREFIXES = ["github.com/", "www.github.com/", "raw.githubusercontent.com/"];

/**
 * Turn the shorthand the form accepts into an absolute GitHub URL.
 *
 * The input is meant to read as "name the profile or project and go", so a bare
 * ``1zyq1`` or ``acme/widgets`` is completed with ``https://github.com/``.
 * Anything already carrying a scheme is passed through untouched, keeping the
 * backend the single authority on which URLs are supported.
 */
export function normalizeSourceInput(raw: string): string {
  const value = raw.trim().replace(/^\/+/, "");
  if (value.includes("://")) {
    return value;
  }
  const lowered = value.toLowerCase();
  if (GITHUB_HOST_PREFIXES.some((host) => lowered.startsWith(host))) {
    return `https://${value}`;
  }
  return `https://github.com/${value}`;
}

/**
 * Routes this application serves at its own root, which a mirror path must
 * therefore never point at. Kept in step with the backend's reserved prefixes.
 */
const OWN_ROUTE_PREFIXES = ["/api", "/proxy", "/assets", "/fonts", "/stats"] as const;

function isSameOriginPath(value: string): boolean {
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("://")) {
    return false;
  }
  return !OWN_ROUTE_PREFIXES.some((prefix) => value === prefix || value.startsWith(`${prefix}/`));
}

function parseResult(payload: unknown): ResolveResult | null {
  if (!isRecord(payload)) {
    return null;
  }
  const kind = payload["kind"];
  const sourceUrl = payload["source_url"];
  const proxyUrl = payload["proxy_url"];
  if (typeof sourceUrl !== "string" || sourceUrl.length === 0 || typeof proxyUrl !== "string") {
    return null;
  }
  if (isResourceKind(kind)) {
    return proxyUrl.startsWith("/proxy/") ? { kind, sourceUrl, proxyUrl } : null;
  }
  if (isPageKind(kind)) {
    return isSameOriginPath(proxyUrl) ? { kind, sourceUrl, proxyUrl } : null;
  }
  return null;
}

function parseErrorDetail(
  payload: unknown,
): { readonly code: string; readonly message: string } | null {
  if (!isRecord(payload)) {
    return null;
  }
  const detail = payload["error"];
  if (!isRecord(detail)) {
    return null;
  }
  const code = detail["code"];
  const message = detail["message"];
  if (typeof code !== "string" || typeof message !== "string") {
    return null;
  }
  return { code, message };
}

/**
 * Resolve one GitHub URL through the same-origin API.
 *
 * Bare shorthand is completed into a full GitHub URL first; the backend then
 * remains the single authority for which URLs are allowed, so this client only
 * forwards the normalized value and never re-implements the policy.
 */
export async function resolveSource(url: string): Promise<ResolveResult> {
  const normalized = normalizeSourceInput(url);
  let response: Response;
  try {
    response = await fetch("/api/resolve", {
      method: "POST",
      // The session cookie is the whole access grant, so it must travel with
      // the request; omitting it would make every resolve an anonymous 401.
      credentials: "same-origin",
      mode: "same-origin",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({ url: normalized }),
    });
  } catch {
    throw new ResolveError("network_error", NETWORK_ERROR_MESSAGE, 0);
  }

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = parseErrorDetail(payload);
    throw new ResolveError(
      detail?.code ?? "request_failed",
      detail?.message ?? FALLBACK_ERROR_MESSAGE,
      response.status,
    );
  }

  const result = parseResult(payload);
  if (result === null) {
    throw new ResolveError("invalid_response", INVALID_RESPONSE_MESSAGE, response.status);
  }
  return result;
}

/* Account access -------------------------------------------------------------------
 * Sessions are carried by the ``ga_session`` cookie, so every auth call is a plain
 * same-origin fetch with credentials attached. The backend stays the single
 * authority on the credential rules; the checks below only mirror them so the panel
 * can point at the offending field before spending a request.
 */

/** Username rule mirrored from the backend account contract. */
const USERNAME_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9_-]{1,30}[A-Za-z0-9])?$/;
const USERNAME_MAX_LENGTH = 32;
const PASSWORD_MIN_LENGTH = 8;
const PASSWORD_MAX_LENGTH = 128;

const USERNAME_REQUIRED_MESSAGE = "请填写用户名。";
const USERNAME_INVALID_MESSAGE =
  "用户名需为 3 至 32 位字母、数字、下划线或连字符，并以字母或数字开头和结尾。";
const USERNAME_TOO_LONG_MESSAGE = "用户名不能超过 32 位。";
const PASSWORD_REQUIRED_MESSAGE = "请填写密码。";
const PASSWORD_INVALID_MESSAGE = "密码长度需为 8 至 128 位。";

/**
 * Address rule mirrored from the backend account contract.
 *
 * It is deliberately shallow: the only authority on whether an address can receive
 * mail is the message that is actually delivered, so this check only catches typos
 * before a request is spent.
 */
const EMAIL_PATTERN = /^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$/;
const EMAIL_MAX_LENGTH = 254;

const EMAIL_REQUIRED_MESSAGE = "请填写邮箱地址。";
const EMAIL_INVALID_MESSAGE = "请填写有效的邮箱地址，例如 name@example.com。";

/**
 * Point at the first credential rule the input breaks.
 *
 * ``username`` and ``email`` are expected to arrive already trimmed. Registering
 * enforces the full username shape, a minimum password length, and a usable address;
 * signing in only needs the fields to be present, so a legacy account is never locked
 * out by a newer rule. The order matches the backend's field order, so the panel points
 * at the same field the server would have rejected.
 */
export function credentialProblem(
  mode: AuthMode,
  username: string,
  password: string,
  email: string,
): CredentialProblem | null {
  if (username.length === 0) {
    return { field: "username", message: USERNAME_REQUIRED_MESSAGE };
  }
  if (username.length > USERNAME_MAX_LENGTH) {
    return { field: "username", message: USERNAME_TOO_LONG_MESSAGE };
  }
  if (mode === "register" && !USERNAME_PATTERN.test(username)) {
    return { field: "username", message: USERNAME_INVALID_MESSAGE };
  }
  if (password.length === 0) {
    return { field: "password", message: PASSWORD_REQUIRED_MESSAGE };
  }
  if (
    mode === "register" &&
    (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH)
  ) {
    return { field: "password", message: PASSWORD_INVALID_MESSAGE };
  }
  if (mode === "register") {
    return emailProblem(email);
  }
  return null;
}

/** Point at the address rule the input breaks, if any. */
export function emailProblem(email: string): CredentialProblem | null {
  if (email.length === 0) {
    return { field: "email", message: EMAIL_REQUIRED_MESSAGE };
  }
  if (email.length > EMAIL_MAX_LENGTH || !EMAIL_PATTERN.test(email)) {
    return { field: "email", message: EMAIL_INVALID_MESSAGE };
  }
  return null;
}

function parseCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function parseQuota(payload: unknown): Quota | null {
  if (!isRecord(payload)) {
    return null;
  }
  const limit = parseCount(payload["limit"]);
  const remaining = parseCount(payload["remaining"]);
  const resetAfterSeconds = parseCount(payload["reset_after_seconds"]);
  if (limit === null || remaining === null || resetAfterSeconds === null) {
    return null;
  }
  return { limit, remaining, resetAfterSeconds };
}

function parseSession(payload: unknown): Session | null {
  if (!isRecord(payload)) {
    return null;
  }
  const authenticated = payload["authenticated"];
  if (typeof authenticated !== "boolean") {
    return null;
  }
  const username = payload["username"];
  if (authenticated && typeof username !== "string") {
    return null;
  }
  const email = payload["email"];
  const emailVerified = payload["email_verified"];
  return {
    authenticated,
    username: typeof username === "string" ? username : null,
    quota: parseQuota(payload["quota"]),
    email: typeof email === "string" ? email : null,
    emailVerified: typeof emailVerified === "boolean" ? emailVerified : null,
  };
}

function requireSession(payload: unknown, status: number): Session {
  const session = parseSession(payload);
  if (session === null) {
    throw new AuthError("invalid_response", INVALID_RESPONSE_MESSAGE, status);
  }
  return session;
}

async function sendJson(
  path: string,
  init: RequestInit,
): Promise<{ readonly response: Response; readonly payload: unknown }> {
  let response: Response;
  try {
    // Credentials are forced last so no caller can accidentally drop the cookie.
    response = await fetch(path, { ...init, credentials: "same-origin" });
  } catch {
    throw new AuthError("network_error", NETWORK_ERROR_MESSAGE, 0);
  }
  const payload: unknown = await response.json().catch(() => null);
  return { response, payload };
}

function authFailure(payload: unknown, response: Response): AuthError {
  const detail = parseErrorDetail(payload);
  return new AuthError(
    detail?.code ?? "request_failed",
    detail?.message ?? AUTH_FALLBACK_ERROR_MESSAGE,
    response.status,
  );
}

async function submitCredentials(path: string, body: Record<string, string>): Promise<Session> {
  const { response, payload } = await sendJson(path, {
    method: "POST",
    mode: "same-origin",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw authFailure(payload, response);
  }
  return requireSession(payload, response.status);
}

/** Read the current session and allowance without changing either. */
export async function fetchSession(): Promise<Session> {
  const { response, payload } = await sendJson("/api/auth/me", {
    method: "GET",
    mode: "same-origin",
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw authFailure(payload, response);
  }
  return requireSession(payload, response.status);
}

/**
 * Create an account. The returned session is deliberately anonymous: the address
 * still has to be confirmed before the account can sign in.
 */
export async function register(
  username: string,
  password: string,
  email: string,
): Promise<Session> {
  return submitCredentials("/api/auth/register", { username, password, email });
}

/** Sign in to an existing account, or report the one still waiting for its address. */
export async function login(username: string, password: string): Promise<Session> {
  return submitCredentials("/api/auth/login", { username, password });
}

/**
 * Mail a fresh verification link for an account that cannot sign in yet.
 *
 * An account created before this feature has no address on file, so the panel can
 * supply one here; the field is left out of the body when it is empty so the stored
 * address is used instead.
 */
export async function requestVerification(
  username: string,
  password: string,
  email: string,
): Promise<Session> {
  const body: Record<string, string> = { username, password };
  if (email.length > 0) {
    body["email"] = email;
  }
  return submitCredentials("/api/auth/verify/request", body);
}

/** Drop the current session and clear its cookie. */
export async function logout(): Promise<void> {
  const { response, payload } = await sendJson("/api/auth/logout", {
    method: "POST",
    mode: "same-origin",
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw authFailure(payload, response);
  }
}

/**
 * Whether the blog cross-site single sign-on is configured.
 *
 * The backend returns ``{"blog": true}`` only when it shares a secret with the
 * blog, so the "用博客账号登录" button is hidden otherwise. A failure is reported
 * as unavailable rather than thrown: the panel simply omits the button.
 */
export async function fetchSsoProviders(): Promise<boolean> {
  try {
    const { response, payload } = await sendJson("/api/sso/providers", {
      method: "GET",
      mode: "same-origin",
      headers: { accept: "application/json" },
    });
    if (!response.ok) {
      return false;
    }
    return isRecord(payload) && payload["blog"] === true;
  } catch {
    return false;
  }
}

/** The windows the server publishes, in the order it publishes them. */
const STAT_WINDOW_KEYS = ["hour", "day", "week", "month", "total"] as const;

export type StatWindowKey = (typeof STAT_WINDOW_KEYS)[number];

export interface StatsWindow {
  readonly key: StatWindowKey;
  readonly downloads: number;
  readonly pages: number;
  readonly users: number;
}

export interface StatsReport {
  readonly windows: readonly StatsWindow[];
}

function isStatWindowKey(value: unknown): value is StatWindowKey {
  return typeof value === "string" && STAT_WINDOW_KEYS.some((candidate) => candidate === value);
}

function parseStatsWindow(payload: unknown): StatsWindow | null {
  if (!isRecord(payload)) {
    return null;
  }
  const key = payload["key"];
  if (!isStatWindowKey(key)) {
    return null;
  }
  const downloads = parseCount(payload["downloads"]);
  const pages = parseCount(payload["pages"]);
  const users = parseCount(payload["users"]);
  if (downloads === null || pages === null || users === null) {
    return null;
  }
  return { key, downloads, pages, users };
}

function parseStats(payload: unknown): StatsReport | null {
  if (!isRecord(payload)) {
    return null;
  }
  const raw = payload["windows"];
  if (!Array.isArray(raw)) {
    return null;
  }
  const windows: StatsWindow[] = [];
  for (const entry of raw) {
    const window = parseStatsWindow(entry);
    if (window === null) {
      return null;
    }
    windows.push(window);
  }
  return { windows };
}

/** Read the public aggregate usage report; it needs no session. */
export async function fetchStats(): Promise<StatsReport> {
  const { response, payload } = await sendJson("/api/stats", {
    method: "GET",
    mode: "same-origin",
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw authFailure(payload, response);
  }
  const report = parseStats(payload);
  if (report === null) {
    throw new AuthError("invalid_response", INVALID_RESPONSE_MESSAGE, response.status);
  }
  return report;
}
