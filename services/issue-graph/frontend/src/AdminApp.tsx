import { useEffect, useState } from "react";
import { api } from "./api";
import "./index.css";

export default function AdminApp() {
  const [enabled, setEnabled] = useState(true);
  const [loggedIn, setLoggedIn] = useState(false);
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // поля конфига
  const [gitlabUrl, setGitlabUrl] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [hasSecret, setHasSecret] = useState(false);
  const [botUrl, setBotUrl] = useState("");
  const [botSecret, setBotSecret] = useState("");
  const [hasBotSecret, setHasBotSecret] = useState(false);
  const [redirectUri, setRedirectUri] = useState("");
  const [appEnabled, setAppEnabled] = useState(true);
  const [pro, setProState] = useState(false);

  const loadConfig = () =>
    api.adminGetConfig().then((c) => {
      setAppEnabled(c.app_enabled);
      setProState(c.pro);
      setGitlabUrl(c.gitlab_url ?? "");
      setClientId(c.oauth_client_id ?? "");
      setHasSecret(c.has_oauth_secret);
      setBotUrl(c.bot_url ?? "");
      setHasBotSecret(c.has_bot_secret);
      setRedirectUri(c.redirect_uri);
    });

  useEffect(() => {
    api.adminStatus().then((s) => {
      setEnabled(s.enabled);
      setLoggedIn(s.logged_in);
      if (s.logged_in) loadConfig();
    });
  }, []);

  const login = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.adminLogin(pw);
      setLoggedIn(true);
      await loadConfig();
    } catch {
      setErr("Неверный пароль");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setErr(null);
    setInfo(null);
    try {
      await api.adminSetConfig({
        gitlab_url: gitlabUrl.trim(),
        oauth_client_id: clientId.trim(),
        oauth_client_secret: clientSecret.trim(),
        bot_url: botUrl.trim(),
        bot_secret: botSecret.trim(),
      });
      setClientSecret("");
      setBotSecret("");
      setInfo("Сохранено");
      await loadConfig();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!enabled)
    return (
      <div className="admin-wrap">
        <div className="admin-card">
          <h2>Админка отключена</h2>
          <p className="modal-sub">
            Задайте переменную окружения <code>ADMIN_PASSWORD</code> при запуске
            бэкенда, чтобы включить панель администратора.
          </p>
          <a href="/" className="back-link">
            ← на главную
          </a>
        </div>
      </div>
    );

  if (!loggedIn)
    return (
      <div className="admin-wrap">
        <div className="admin-card">
          <h2>Вход в админку</h2>
          <p className="modal-sub">Доступ по паролю администратора.</p>
          <label className="modal-field">
            <span>Пароль</span>
            <input
              type="password"
              value={pw}
              autoFocus
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && login()}
            />
          </label>
          {err && <div className="error small">{err}</div>}
          <div className="modal-actions">
            <a href="/" className="back-link">
              ← на главную
            </a>
            <span className="ma-spacer" />
            <button className="primary" disabled={busy || !pw} onClick={login}>
              {busy ? "Вход…" : "Войти"}
            </button>
          </div>
        </div>
      </div>
    );

  return (
    <div className="admin-wrap">
      <div className="admin-card wide">
        <h2>Настройки инстанса</h2>
        <p className="modal-sub">
          Эти параметры — общие для всего инстанса (один GitLab). Пользователи
          входят через OAuth и видят свои задачи.
        </p>

        <section className="admin-sec">
          <h3>Доступ (рубильник)</h3>
          <div className="killswitch">
            <span>
              Приложение сейчас:{" "}
              <b className={appEnabled ? "ks-on" : "ks-off"}>
                {appEnabled ? "включено" : "выключено"}
              </b>
            </span>
            <button
              className={appEnabled ? "ks-btn off" : "ks-btn on"}
              onClick={async () => {
                const r = await api.adminSetEnabled(!appEnabled);
                setAppEnabled(r.enabled);
              }}
            >
              {appEnabled ? "Выключить для всех" : "Включить"}
            </button>
          </div>
        </section>

        <section className="admin-sec">
          <h3>Тариф</h3>
          <div className="killswitch">
            <span>
              Режим:{" "}
              <b className={pro ? "ks-on" : "ks-off"}>{pro ? "PRO (Premium/Ultimate)" : "Free"}</b>
            </span>
            <button
              className={pro ? "ks-btn off" : "ks-btn on"}
              onClick={async () => {
                const r = await api.adminSetPro(!pro);
                setProState(r.pro);
              }}
            >
              {pro ? "Выключить PRO" : "Включить PRO"}
            </button>
          </div>
          <p className="modal-sub" style={{ marginTop: 8 }}>
            PRO включает epics, blocking-связи и iterations. Включайте, только
            если в GitLab реально есть Premium/Ultimate.{" "}
            <b>Как проверить:</b> в GitLab открой любой проект →{" "}
            <b>Issues → Boards</b> и попробуй создать <b>Epic</b> (меню группы →
            «Epics»); либо у issue в связях есть тип <b>«blocks / is blocked
            by»</b> (не только «relates to»); либо <b>Admin Area → Subscription</b>{" "}
            показывает план Premium/Ultimate. Если этого нет — оставь Free.
          </p>
        </section>

        <section className="admin-sec">
          <h3>GitLab</h3>
          <label className="modal-field">
            <span>Host</span>
            <input
              value={gitlabUrl}
              onChange={(e) => setGitlabUrl(e.target.value)}
              placeholder="https://gitlab.example.com"
            />
          </label>
        </section>

        <section className="admin-sec">
          <h3>OAuth-приложение</h3>
          <p className="modal-sub">
            В GitLab: <b>Admin Area → Applications</b> (или Group → Applications),
            scope <code>api</code>, Confidential, Redirect URI:
          </p>
          <code className="oauth-redirect">{redirectUri}</code>
          <div className="oauth-grid">
            <label className="modal-field">
              <span>Application ID</span>
              <input
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="application id"
              />
            </label>
            <label className="modal-field">
              <span>Secret {hasSecret && "(задан — пусто = не менять)"}</span>
              <input
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                placeholder={hasSecret ? "••••••" : "application secret"}
              />
            </label>
          </div>
        </section>

        <section className="admin-sec">
          <h3>Бот уведомлений (необязательно)</h3>
          <p className="modal-sub">
            Адрес бота и его <code>poke_token</code> (тот же, что задан в боте:
            env <code>POKE_TOKEN</code> или админка бота) для кнопки «🔔 Пнуть
            исполнителя». Пусто в URL — отключить.
          </p>
          <div className="oauth-grid">
            <label className="modal-field">
              <span>URL бота</span>
              <input
                value={botUrl}
                onChange={(e) => setBotUrl(e.target.value)}
                placeholder="http://localhost:8080"
              />
            </label>
            <label className="modal-field">
              <span>poke_token {hasBotSecret && "(задан)"}</span>
              <input
                type="password"
                value={botSecret}
                onChange={(e) => setBotSecret(e.target.value)}
                placeholder={hasBotSecret ? "••••••" : "токен пинка"}
              />
            </label>
          </div>
        </section>

        {info && <div className="modal-info">{info}</div>}
        {err && <div className="error small">{err}</div>}
        <div className="modal-actions">
          <a href="/" className="back-link">
            ← на главную
          </a>
          <span className="ma-spacer" />
          <button className="primary" disabled={busy || !gitlabUrl.trim()} onClick={save}>
            {busy ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}
