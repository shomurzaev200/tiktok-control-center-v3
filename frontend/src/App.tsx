import React, { useEffect, useState, useCallback } from "react";

export default function App() {
  const [user, setUser] = useState<any>(null);
  const [csrfToken, setCsrfToken] = useState<string>("");
  const [ownerInit, setOwnerInit] = useState<boolean>(true);
  const [authChecking, setAuthChecking] = useState<boolean>(true);
  const [authForm, setAuthForm] = useState({ setup_token: "", username: "", password: "", telegram_id: 0 });
  const [tab, setTab] = useState("dashboard");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ type: "err" | "ok"; msg: string } | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [confirmDialog, setConfirmDialog] = useState<{ title: string; onConfirm: () => void } | null>(null);

  const [stats, setStats] = useState<any>({ metrics: {}, health_series: [] });
  const [accounts, setAccounts] = useState<any[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<any>(null);
  const [sources, setSources] = useState<any[]>([]);
  const [videos, setVideos] = useState<any[]>([]);
  const [comments, setComments] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [selectedTask, setSelectedTask] = useState<any>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [diagnostics, setDiagnostics] = useState<any>(null);
  const [backups, setBackups] = useState<any[]>([]);
  const [settingsList, setSettingsList] = useState<any[]>([]);
  const [admins, setAdmins] = useState<any[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);

  const [accSearch, setAccSearch] = useState("");
  const [accForm, setAccForm] = useState({
    username: "@", login_name: "", password: "", proxy_url: "", daily_comment_limit: 25, daily_like_limit: 80,
    work_hour_start: 9, work_hour_end: 23, min_delay_sec: 20, max_delay_sec: 55, version: undefined as number | undefined
  });
  const [loginUi, setLoginUi] = useState<any>(null);
  const [loginCode, setLoginCode] = useState("");
  const [srcForm, setSrcForm] = useState({ source_type: "main_likes", value: "", interval_minutes: 30 });
  const [vidFilter, setVidFilter] = useState({ search: "", status: "" });
  const [vidForm, setVidForm] = useState({ video_url: "", search_keyword: "" });
  const [bulkVids, setBulkVids] = useState("");
  const [bulkCat, setBulkCat] = useState("general");
  const [bulkTxt, setBulkTxt] = useState("");
  const [editTpl, setEditTpl] = useState<any>(null);
  const [taskFilter, setTaskFilter] = useState({ queue_name: "", status: "" });
  const [logFilter, setLogFilter] = useState({ service: "", level: "", search: "" });
  const [settingForm, setSettingForm] = useState({ key: "MIN_FREE_DISK_MB", value: "1024", description: "Мин. свободного места МБ" });
  const [adminForm, setAdminForm] = useState({ username: "", password: "", role: "admin" });
  const [pwForm, setPwForm] = useState({ current_password: "", new_password: "" });

  const notify = (msg: string, type: "err" | "ok" = "ok") => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 4500);
  };

  const api = useCallback(async (url: string, opts: any = {}) => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...(opts.headers || {}),
    };
    const res = await fetch(url, { ...opts, headers, credentials: "include" });
    if (res.status === 401 && !url.includes("/api/auth/me")) {
      setUser(null);
    }
    return res;
  }, [csrfToken]);

  useEffect(() => {
    Promise.all([
      fetch("/api/auth/setup-status", { credentials: "include" }).then(r => r.json()).catch(() => ({ owner_initialized: true })),
      fetch("/api/auth/me", { credentials: "include" }).then(r => (r.ok ? r.json() : null)).catch(() => null),
    ]).then(([st, me]) => {
      setOwnerInit(Boolean(st?.owner_initialized));
      if (me) {
        setUser(me);
        if (me.csrf_token) setCsrfToken(me.csrf_token);
      }
      setAuthChecking(false);
    });
  }, []);

  const loadData = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    try {
      const [d, a, s, v, c, t, l, al, b, st, ad, ss] = await Promise.all([
        api("/api/dashboard").then(r => r.json()),
        api(`/api/accounts?search=${encodeURIComponent(accSearch)}`).then(r => r.json()),
        api("/api/sources").then(r => r.json()),
        api(`/api/videos?search=${encodeURIComponent(vidFilter.search)}&video_status=${vidFilter.status}`).then(r => r.json()),
        api("/api/comments").then(r => r.json()),
        api(`/api/tasks?queue_name=${taskFilter.queue_name}&status=${taskFilter.status}`).then(r => r.json()),
        api(`/api/logs?service=${logFilter.service}&level=${logFilter.level}&search=${encodeURIComponent(logFilter.search)}`).then(r => r.json()),
        api("/api/audit-logs").then(r => r.json()),
        api("/api/backups").then(r => r.json()),
        api("/api/settings").then(r => r.json()),
        user.role !== "viewer" ? api("/api/auth/admins").then(r => (r.ok ? r.json() : [])) : Promise.resolve([]),
        api("/api/auth/sessions").then(r => (r.ok ? r.json() : [])),
      ]);
      if (d && !d.detail) setStats(d);
      if (a?.items) setAccounts(a.items);
      if (s?.items) setSources(s.items);
      if (v?.items) setVideos(v.items);
      if (c?.items) setComments(c.items);
      if (t?.items) setTasks(t.items);
      if (l?.items) setLogs(l.items);
      if (al?.items) setAuditLogs(al.items);
      if (b?.items) setBackups(b.items);
      if (st?.items) setSettingsList(st.items);
      if (Array.isArray(ad)) setAdmins(ad);
      if (Array.isArray(ss)) setSessions(ss);
    } catch (e: any) {
      notify(e.message || "Ошибка загрузки данных", "err");
    } finally {
      setLoading(false);
    }
  }, [user, api, accSearch, vidFilter, taskFilter, logFilter]);

  useEffect(() => {
    if (!user) return;
    loadData();
  }, [user, loadData]);

  useEffect(() => {
    if (!loginUi || (loginUi.mode !== "qr" && loginUi.mode !== "qr-new")) return;
    const timer = setInterval(async () => {
      const url = loginUi.mode === "qr-new"
        ? `/api/accounts/qr-signup/${loginUi.token}`
        : `/api/accounts/${loginUi.id}/login/qr-status`;
      const r = await api(url);
      const data = await r.json();
      if (data.status === "authenticated") {
        notify(loginUi.mode === "qr-new" ? `Добавлен ${data.username}` : "Телефон подтвердил вход");
        setLoginUi(null);
        loadData();
      } else if (data.status === "failed" || data.status === "expired") {
        notify(data.detail || "QR истёк", "err");
        setLoginUi(null);
      } else if (data.detail && data.detail !== loginUi.message) {
        setLoginUi((prev: any) => prev ? { ...prev, message: data.detail } : prev);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [loginUi, api]);

  useEffect(() => {
    if (!user) return;
    let ws: WebSocket | null = null;
    let timer: any = null;
    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      ws.onopen = () => setWsConnected(true);
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          setStats((prev: any) => ({ ...prev, ...data }));
        } catch {}
      };
      ws.onclose = () => {
        setWsConnected(false);
        timer = setTimeout(connect, 4000);
      };
    };
    connect();
    return () => {
      if (timer) clearTimeout(timer);
      if (ws) ws.close();
    };
  }, [user]);

  const exportJson = (filename: string, data: any) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
  };

  const inp = "w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:border-cyan-400 outline-none";
  const btn = "bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold px-4 py-2 rounded-lg text-sm transition";
  const card = "bg-slate-900 p-5 rounded-xl border border-slate-800";

  if (authChecking) {
    return <div className="min-h-screen flex items-center justify-center bg-slate-950 text-cyan-400">Загрузка TikTok Control Center v3.0...</div>;
  }

  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950 p-4">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            const ep = ownerInit ? "/api/auth/login" : "/api/auth/setup-owner";
            const res = await fetch(ep, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "include",
              body: JSON.stringify(authForm),
            });
            const data = await res.json();
            if (!res.ok) return notify(data.detail || "Ошибка авторизации", "err");
            setCsrfToken(data.csrf_token || "");
            setUser({ username: data.username, role: data.role });
            setOwnerInit(true);
          }}
          className="w-full max-w-md bg-slate-900 border border-cyan-500/30 rounded-2xl p-8 space-y-4 shadow-2xl"
        >
          <h1 className="text-2xl font-bold text-cyan-400">
            {ownerInit ? "🔐 Вход в TikTok Control v3.0" : "👑 Инициализация владельца v3.0"}
          </h1>
          {toast && <div className={`p-3 rounded text-sm ${toast.type === "err" ? "bg-rose-500/20 text-rose-300" : "bg-emerald-500/20 text-emerald-300"}`}>{toast.msg}</div>}
          {!ownerInit && (
            <>
              <input placeholder="SETUP_SECRET_TOKEN (из .env)" value={authForm.setup_token} onChange={e => setAuthForm({ ...authForm, setup_token: e.target.value })} className={inp} required />
              <input type="number" placeholder="Ваш Telegram ID" value={authForm.telegram_id || ""} onChange={e => setAuthForm({ ...authForm, telegram_id: +e.target.value })} className={inp} required />
            </>
          )}
          <input placeholder="Логин" value={authForm.username} onChange={e => setAuthForm({ ...authForm, username: e.target.value })} className={inp} required />
          <input type="password" placeholder="Пароль (мин. 8 символов)" value={authForm.password} onChange={e => setAuthForm({ ...authForm, password: e.target.value })} className={inp} required />
          <button className={`w-full ${btn}`}>Продолжить</button>
        </form>
      </div>
    );
  }

  const navItems = [
    ["dashboard", "📊 Дашборд"],
    ["accounts", "👥 Аккаунты"],
    ["sources", "🔍 Источники"],
    ["videos", "🎬 Каталог видео"],
    ["comments", "💬 Шаблоны"],
    ["queue", "⚡ Очередь и DLQ"],
    ["logs", "🛠 Логи и Аудит"],
    ["diagnostics", "🩺 Диагностика"],
    ["backups", "💾 Резервные копии"],
    ["settings", "⚙️ Настройки"],
    ["security", "🛡 Админы и Сессии"],
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col md:flex-row">
      {/* Мобильная шапка */}
      <div className="md:hidden flex items-center justify-between bg-slate-900 p-4 border-b border-slate-800">
        <span className="font-bold text-cyan-400">TIKTOK CONTROL v3.0</span>
        <button onClick={() => setMobileMenu(!mobileMenu)} className="px-3 py-1 bg-slate-800 rounded">☰ Меню</button>
      </div>

      {/* Боковое меню */}
      <aside className={`${mobileMenu ? "block" : "hidden"} md:flex w-full md:w-64 bg-slate-900 border-r border-cyan-500/20 p-4 flex-col gap-1`}>
        <div className="hidden md:block mb-4">
          <h1 className="text-lg font-bold text-cyan-400">TIKTOK CONTROL v3.0</h1>
          <div className="text-xs text-slate-400 mt-1">
            👤 {user.username} ({user.role}) | {wsConnected ? "🟢 WS" : "🟡 Реконнект"}
          </div>
        </div>
        {navItems.map(([k, label]) => (
          <button
            key={k}
            onClick={() => { setTab(k); setMobileMenu(false); }}
            className={`text-left px-3 py-2.5 rounded-lg text-sm transition ${
              tab === k ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40" : "text-slate-400 hover:bg-slate-800"
            }`}
          >
            {label}
          </button>
        ))}
        <button
          onClick={async () => {
            await api("/api/auth/logout", { method: "POST" });
            setUser(null);
          }}
          className="mt-auto text-left px-3 py-2 text-rose-400 hover:bg-rose-500/10 rounded text-sm"
        >
          🚪 Выйти (отозвать сессию)
        </button>
      </aside>

      {/* Основной контент */}
      <main className="flex-1 p-4 md:p-8 overflow-y-auto space-y-6">
        {toast && (
          <div className={`p-3 rounded-lg border text-sm ${toast.type === "err" ? "bg-rose-500/20 border-rose-500/40 text-rose-200" : "bg-emerald-500/20 border-emerald-500/40 text-emerald-200"}`}>
            {toast.msg}
          </div>
        )}

        {confirmDialog && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
            <div className={`${card} max-w-md w-full space-y-4`}>
              <h3 className="text-lg font-bold text-rose-400">Подтверждение действия</h3>
              <p className="text-sm text-slate-300">{confirmDialog.title}</p>
              <div className="flex justify-end gap-3">
                <button onClick={() => setConfirmDialog(null)} className="px-4 py-2 bg-slate-800 rounded-lg text-sm">Отмена</button>
                <button onClick={() => { confirmDialog.onConfirm(); setConfirmDialog(null); }} className="px-4 py-2 bg-rose-500 text-white font-bold rounded-lg text-sm">Подтвердить</button>
              </div>
            </div>
          </div>
        )}

        {/* 1. ДАШБОРД */}
        {tab === "dashboard" && (
          <div className="space-y-6">
            <div className="flex flex-wrap justify-between items-center gap-3">
              <div>
                <h2 className="text-2xl font-bold">Панель мониторинга v3.0</h2>
                <p className="text-xs text-slate-400">Автообновление через защищённый WebSocket {loading && "• Обновление..."}</p>
              </div>
              <div className="flex gap-2">
                <button onClick={loadData} className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm">🔄 Обновить</button>
                <button
                  onClick={async () => {
                    const r = await api("/api/queue/toggle-pause", { method: "POST" });
                    if (r.ok) { notify("Состояние очереди изменено"); loadData(); }
                  }}
                  className={btn}
                >
                  {stats.queue_paused ? "▶️ Возобновить очередь" : "⏸ Пауза очереди"}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                ["Воркер", stats.metrics?.worker_online || stats.worker_online ? "🟢 ONLINE" : "🔴 OFFLINE"],
                ["Telegram", stats.metrics?.telegram_online || stats.telegram_online ? "🟢 ONLINE" : "🔴 OFFLINE"],
                ["Очередь", stats.queue_paused ? "⏸ ПАУЗА" : "⚡ АКТИВНА"],
                ["CPU / RAM", `${stats.metrics?.cpu_percent ?? 0}% / ${stats.metrics?.ram_percent ?? 0}%`],
                ["Аккаунтов", stats.accounts_count || 0],
                ["Видео в каталоге", stats.videos_count || 0],
                ["На проверке (Verification)", stats.verification_tasks || 0],
                ["Выполнено / DLQ", `${stats.completed_tasks || 0} / ${stats.failed_tasks || 0}`],
              ].map(([k, v]) => (
                <div key={String(k)} className={card}>
                  <div className="text-slate-400 text-xs">{k}</div>
                  <div className="text-xl font-bold text-cyan-400 mt-1">{v}</div>
                </div>
              ))}
            </div>

            {/* График нагрузки и задержек */}
            <div className={card}>
              <h3 className="font-bold text-sm text-slate-300 mb-3">Динамика ресурсов и задержки БД (последние снимки)</h3>
              <div className="h-32 flex items-end gap-2 pt-4">
                {(stats.health_series || []).map((pt: any, idx: number) => (
                  <div key={idx} className="flex-1 flex flex-col items-center gap-1">
                    <div className="w-full bg-cyan-500/40 rounded-t" style={{ height: `${Math.max(8, Math.min(100, pt.cpu || 10))}%` }} title={`CPU: ${pt.cpu}% | RAM: ${pt.ram}%`} />
                    <span className="text-[10px] text-slate-500">{idx + 1}</span>
                  </div>
                ))}
                {(!stats.health_series || stats.health_series.length === 0) && (
                  <div className="text-xs text-slate-500">Снимки метрик появятся после опроса раздела Диагностика</div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* 2. АККАУНТЫ */}
        {tab === "accounts" && (
          <div className="space-y-6">
            <div className="flex flex-wrap justify-between items-center gap-2">
              <h2 className="text-2xl font-bold">Аккаунты TikTok</h2>
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    notify("Готовлю QR. Мёртвые прокси пропускаются, это может занять минуту.");
                    try {
                      const r = await api("/api/accounts/qr-signup", { method: "POST" });
                      const d = await r.json();
                      if (!r.ok || d.status !== "qr_ready" || !d.image_base64) {
                        setLoginUi({ mode: "qr-error", message: d.detail || "QR не открылся" });
                        return notify(d.detail || "QR не открылся", "err");
                      }
                      setLoginUi({ mode: "qr-new", token: d.token, message: d.detail, image: d.image_base64 });
                      notify("QR на экране. Сканируй его в TikTok на телефоне.");
                    } catch {
                      setLoginUi({ mode: "qr-error", message: "Сервер не успел ответить. Нажми ещё раз и подожди минуту." });
                      notify("Сервер не успел ответить", "err");
                    }
                  }}
                  className={btn}
                >
                  Добавить сканером
                </button>
                <input placeholder="Поиск по @username..." value={accSearch} onChange={e => setAccSearch(e.target.value)} className={`${inp} w-56`} />
                <button onClick={() => exportJson("tiktok_accounts.json", accounts)} className="px-3 py-2 bg-slate-800 rounded-lg text-xs">📥 Экспорт JSON</button>
              </div>
            </div>

            {loginUi && (
              <div className={`${card} border-cyan-500/40 space-y-3`}>
                <div className="flex justify-between">
                  <h3 className="font-bold text-cyan-400">
                    {loginUi.mode === "qr-new" ? "Сканер: добавление аккаунта" : loginUi.mode === "qr-error" ? "Сканер: QR не открылся" : `Вход в аккаунт #${loginUi.id}`}
                  </h3>
                  <button onClick={() => setLoginUi(null)} className="text-xs text-slate-400">✕ Закрыть</button>
                </div>
                <p className="text-sm text-slate-300">{loginUi.message}</p>
                {(loginUi.mode === "qr" || loginUi.mode === "qr-new") && loginUi.image && (
                  <img alt="QR TikTok" className="bg-white rounded-lg p-3 w-72 h-72 object-contain" src={`data:image/png;base64,${loginUi.image}`} />
                )}
                {(loginUi.mode === "qr" || loginUi.mode === "qr-new") && !loginUi.image && (
                  <p className="text-sm text-rose-300">Картинка QR не пришла. Нажми «Добавить сканером» ещё раз.</p>
                )}
                {loginUi.mode === "code" && (
                  <form
                    className="flex gap-2"
                    onSubmit={async (e) => {
                      e.preventDefault();
                      const r = await api(`/api/accounts/${loginUi.id}/login/code`, { method: "POST", body: JSON.stringify({ code: loginCode }) });
                      const d = await r.json();
                      if (!r.ok) return notify(d.detail || "Код не отправлен", "err");
                      if (d.status === "authenticated") {
                        notify("Код принят, аккаунт вошёл");
                        setLoginUi(null);
                        setLoginCode("");
                        loadData();
                        return;
                      }
                      notify(d.detail || "Код не подошёл", "err");
                    }}
                  >
                    <input placeholder="Код из SMS, почты или приложения" value={loginCode} onChange={e => setLoginCode(e.target.value)} className={inp} required />
                    <button className={btn}>Подтвердить код</button>
                  </form>
                )}
              </div>
            )}

            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const res = await api("/api/accounts", { method: "POST", body: JSON.stringify(accForm) });
                const data = await res.json();
                if (!res.ok) return notify(data.detail || "Ошибка сохранения", "err");
                notify("Аккаунт сохранён. Дальше нажми «Войти» или «QR с телефона».");
                setAccForm({ ...accForm, username: "@", login_name: "", password: "", proxy_url: "", version: undefined });
                loadData();
              }}
              className={`${card} grid grid-cols-1 md:grid-cols-3 gap-3`}
            >
              <input placeholder="@username профиля" value={accForm.username} onChange={e => setAccForm({ ...accForm, username: e.target.value })} className={inp} required />
              <input placeholder="Логин: почта, телефон или username" value={accForm.login_name} onChange={e => setAccForm({ ...accForm, login_name: e.target.value })} className={inp} />
              <input type="password" autoComplete="new-password" placeholder="Пароль TikTok" value={accForm.password} onChange={e => setAccForm({ ...accForm, password: e.target.value })} className={inp} />
              <input placeholder="Прокси (http://user:pass@ip:port)" value={accForm.proxy_url} onChange={e => setAccForm({ ...accForm, proxy_url: e.target.value })} className={inp} />
              <input type="number" placeholder="Лимит комм/день" value={accForm.daily_comment_limit} onChange={e => setAccForm({ ...accForm, daily_comment_limit: +e.target.value })} className={inp} />
              <button className={btn}>{accForm.version ? "Обновить аккаунт" : "Сохранить аккаунт"}</button>
            </form>

            {selectedAccount && (
              <div className={`${card} border-cyan-500/40 space-y-2`}>
                <div className="flex justify-between">
                  <h3 className="font-bold text-cyan-400">Состояние аккаунта {selectedAccount.username}</h3>
                  <button onClick={() => setSelectedAccount(null)} className="text-xs text-slate-400">✕ Закрыть</button>
                </div>
                <div className="text-xs text-slate-300 space-y-1">
                  <div>Статус авторизации: <b>{selectedAccount.auth_state}</b> ({selectedAccount.auth_diagnostics?.reason})</div>
                  <div>Прокси (маскированный): <b>{selectedAccount.proxy_masked}</b> ({selectedAccount.proxy_status})</div>
                  <div>Часы активности (UTC): {selectedAccount.work_hour_start}:00 – {selectedAccount.work_hour_end}:00 | Версия записи: v{selectedAccount.version}</div>
                </div>
              </div>
            )}

            <div className={`${card} overflow-x-auto`}>
              <table className="w-full text-left text-sm">
                <thead className="text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="py-2">Аккаунт</th>
                    <th className="py-2">Прокси</th>
                    <th className="py-2">Авторизация</th>
                    <th className="py-2">Лимит сегодня</th>
                    <th className="py-2">Действия</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {accounts.map(a => (
                    <tr key={a.id}>
                      <td className="py-2.5">
                        <div className="font-bold text-cyan-300">{a.username}</div>
                        <div className="text-xs text-slate-500">{a.login_name || "логин не задан"}{a.has_password ? " · пароль сохранён" : ""}</div>
                      </td>
                      <td className="py-2.5 text-xs">{a.proxy_masked} <span className="text-slate-400">({a.proxy_status})</span></td>
                      <td className="py-2.5">
                        <span className={`px-2 py-0.5 rounded text-xs ${a.auth_state === "authenticated" ? "bg-emerald-500/20 text-emerald-300" : "bg-amber-500/20 text-amber-300"}`}>
                          {a.auth_state === "authenticated" ? "Вошёл" : a.auth_state === "needs_2fa" ? "Ждёт код" : a.auth_state === "qr_pending" ? "Ждёт скан" : a.auth_state === "reauth_required" ? "Нужен вход" : a.auth_state}
                        </span>
                      </td>
                      <td className="py-2.5">{a.comments_today}/{a.daily_comment_limit} (ошибок: {a.total_errors})</td>
                      <td className="py-2.5 flex flex-wrap gap-2">
                        <button
                          onClick={async () => {
                            const r = await api(`/api/accounts/${a.id}`);
                            if (r.ok) setSelectedAccount(await r.json());
                          }}
                          className="text-xs px-2 py-1 bg-slate-800 rounded"
                        >
                          Состояние
                        </button>
                        <button
                          onClick={async () => {
                            notify("Открываю вход TikTok...");
                            const r = await api(`/api/accounts/${a.id}/login`, { method: "POST" });
                            const d = await r.json();
                            if (!r.ok) return notify(d.detail || "Ошибка входа", "err");
                            if (d.status === "authenticated") {
                              notify("Аккаунт вошёл");
                              loadData();
                              return;
                            }
                            if (d.status === "needs_2fa") {
                              setLoginCode("");
                              setLoginUi({ id: a.id, mode: "code", message: d.detail });
                              return;
                            }
                            notify(d.detail || "Вход не выполнен", "err");
                          }}
                          className="text-xs px-2 py-1 bg-cyan-500/20 text-cyan-300 rounded"
                        >
                          Войти
                        </button>
                        <button
                          onClick={async () => {
                            notify("Готовлю QR...");
                            const r = await api(`/api/accounts/${a.id}/login/qr`, { method: "POST" });
                            const d = await r.json();
                            if (!r.ok || d.status !== "qr_ready") return notify(d.detail || "QR не открылся", "err");
                            setLoginUi({ id: a.id, mode: "qr", message: d.detail, image: d.image_base64 });
                          }}
                          className="text-xs px-2 py-1 bg-slate-800 rounded"
                        >
                          QR с телефона
                        </button>
                        <button
                          onClick={() => setAccForm({ ...accForm, username: a.username, login_name: a.login_name || "", password: "", proxy_url: "", daily_comment_limit: a.daily_comment_limit, version: a.version })}
                          className="text-xs px-2 py-1 bg-slate-800 rounded text-cyan-300"
                        >
                          Изменить
                        </button>
                        <button
                          onClick={async () => {
                            await api(`/api/accounts/${a.id}/check-proxy`, { method: "POST" });
                            notify("Прокси проверен");
                            loadData();
                          }}
                          className="text-xs px-2 py-1 bg-slate-800 rounded"
                        >
                          Тест прокси
                        </button>
                        <button
                          onClick={() => setConfirmDialog({
                            title: `Удалить аккаунт ${a.username}?`,
                            onConfirm: async () => {
                              await api(`/api/accounts/${a.id}`, { method: "DELETE" });
                              notify("Аккаунт удалён");
                              loadData();
                            }
                          })}
                          className="text-xs px-2 py-1 bg-rose-500/20 text-rose-300 rounded"
                        >
                          Удалить
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* 3. ИСТОЧНИКИ */}
        {tab === "sources" && (
          <div className="space-y-4">
            <h2 className="text-2xl font-bold">Управление источниками</h2>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const r = await api("/api/sources", { method: "POST", body: JSON.stringify(srcForm) });
                if (!r.ok) return notify("Ошибка сохранения источника", "err");
                setSrcForm({ ...srcForm, value: "" });
                notify("Источник сохранён");
                loadData();
              }}
              className={`${card} grid grid-cols-1 md:grid-cols-4 gap-3`}
            >
              <select value={srcForm.source_type} onChange={e => setSrcForm({ ...srcForm, source_type: e.target.value })} className={inp}>
                <option value="main_likes">Лайки донора (@user)</option>
                <option value="search_keyword">Поисковый запрос</option>
                <option value="hashtag">Хэштег (#tag)</option>
              </select>
              <input placeholder="@username / запрос / #tag" value={srcForm.value} onChange={e => setSrcForm({ ...srcForm, value: e.target.value })} className={inp} required />
              <input type="number" min={5} max={1440} value={srcForm.interval_minutes} onChange={e => setSrcForm({ ...srcForm, interval_minutes: +e.target.value })} className={inp} />
              <button className={btn}>Сохранить источник</button>
            </form>
            <div className={card}>
              {sources.map(s => (
                <div key={s.id} className="flex justify-between items-center py-2 border-b border-slate-800 text-sm">
                  <span><b>[{s.source_type}]</b> {s.value} (интервал: {s.interval_minutes} мин.) {s.last_error && <span className="text-rose-400">Ошибка: {s.last_error}</span>}</span>
                  <button
                    onClick={() => setConfirmDialog({
                      title: `Удалить источник ${s.value}?`,
                      onConfirm: async () => { await api(`/api/sources/${s.id}`, { method: "DELETE" }); loadData(); }
                    })}
                    className="text-rose-400 text-xs"
                  >
                    Удалить
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 4. КАТАЛОГ ВИДЕО */}
        {tab === "videos" && (
          <div className="space-y-4">
            <div className="flex flex-wrap justify-between items-center gap-2">
              <h2 className="text-2xl font-bold">Каталог целевых видео</h2>
              <div className="flex gap-2">
                <input placeholder="Поиск по URL..." value={vidFilter.search} onChange={e => setVidFilter({ ...vidFilter, search: e.target.value })} className={`${inp} w-48`} />
                <select value={vidFilter.status} onChange={e => setVidFilter({ ...vidFilter, status: e.target.value })} className={`${inp} w-40`}>
                  <option value="">Все статусы</option>
                  <option value="available">Доступные</option>
                  <option value="private">Приватные</option>
                  <option value="unavailable">Недоступные</option>
                </select>
                <button onClick={() => exportJson("target_videos.json", videos)} className="px-3 py-2 bg-slate-800 rounded-lg text-xs">📥 Экспорт</button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  const r = await api("/api/videos", { method: "POST", body: JSON.stringify(vidForm) });
                  const d = await r.json();
                  if (!r.ok) return notify(d.detail || "Ошибка валидации URL", "err");
                  setVidForm({ video_url: "", search_keyword: "" });
                  notify("Видео проверено и добавлено");
                  loadData();
                }}
                className={`${card} space-y-2`}
              >
                <h3 className="font-bold text-sm text-cyan-400">Добавить одно видео (с проверкой oEmbed)</h3>
                <input placeholder="https://www.tiktok.com/@user/video/123" value={vidForm.video_url} onChange={e => setVidForm({ ...vidForm, video_url: e.target.value })} className={inp} required />
                <input placeholder="Ключевое слово (опц.)" value={vidForm.search_keyword} onChange={e => setVidForm({ ...vidForm, search_keyword: e.target.value })} className={inp} />
                <button className={btn}>Проверить и добавить</button>
              </form>

              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  const urls = bulkVids.split("\n").map(u => u.trim()).filter(Boolean);
                  if (!urls.length) return;
                  const r = await api("/api/videos/bulk", { method: "POST", body: JSON.stringify({ urls }) });
                  const d = await r.json();
                  if (!r.ok) return notify(d.detail || "Ошибка импорта", "err");
                  setBulkVids("");
                  notify(`Импортировано: ${d.added}, пропущено дубликатов: ${d.skipped}`);
                  loadData();
                }}
                className={`${card} space-y-2`}
              >
                <h3 className="font-bold text-sm text-cyan-400">Массовый импорт URL (защита от дубликатов)</h3>
                <textarea rows={3} placeholder="По одной ссылке TikTok на строку..." value={bulkVids} onChange={e => setBulkVids(e.target.value)} className={inp} required />
                <button className={btn}>Импортировать список</button>
              </form>
            </div>

            <div className={card}>
              {videos.map(v => (
                <div key={v.id} className="flex justify-between items-center py-2 border-b border-slate-800 text-sm">
                  <div className="truncate max-w-2xl">
                    <span className={`px-2 py-0.5 rounded text-xs mr-2 ${v.video_status === "available" ? "bg-emerald-500/20 text-emerald-300" : "bg-rose-500/20 text-rose-300"}`}>
                      {v.video_status}
                    </span>
                    <a href={v.video_url} target="_blank" rel="noreferrer" className="text-cyan-400 hover:underline">{v.video_url}</a>
                    <span className="text-xs text-slate-500 ml-2">({v.is_processed ? "Обработано" : "Ожидает"})</span>
                  </div>
                  <button
                    onClick={() => setConfirmDialog({
                      title: `Удалить видео #${v.id}?`,
                      onConfirm: async () => { await api(`/api/videos/${v.id}`, { method: "DELETE" }); loadData(); }
                    })}
                    className="text-rose-400 text-xs"
                  >
                    Удалить
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 5. ШАБЛОНЫ КОММЕНТАРИЕВ */}
        {tab === "comments" && (
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <h2 className="text-2xl font-bold">Редактор шаблонов комментариев</h2>
              <button onClick={() => exportJson("comment_templates.json", comments)} className="px-3 py-2 bg-slate-800 rounded-lg text-xs">📥 Экспорт JSON</button>
            </div>

            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const texts = bulkTxt.split("\n").map(t => t.trim()).filter(Boolean);
                if (!texts.length) return;
                const r = await api("/api/comments/bulk", { method: "POST", body: JSON.stringify({ category: bulkCat, texts }) });
                const d = await r.json();
                setBulkTxt("");
                notify(`Добавлено: ${d.added}, дубликатов пропущено: ${d.skipped}`);
                loadData();
              }}
              className={`${card} space-y-3`}
            >
              <input placeholder="Категория (general)" value={bulkCat} onChange={e => setBulkCat(e.target.value)} className={inp} />
              <textarea rows={4} placeholder="Шаблоны (каждый с новой строки)..." value={bulkTxt} onChange={e => setBulkTxt(e.target.value)} className={inp} required />
              <button className={btn}>Импортировать шаблоны</button>
            </form>

            {editTpl && (
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  await api(`/api/comments/${editTpl.id}`, { method: "PUT", body: JSON.stringify(editTpl) });
                  setEditTpl(null);
                  notify("Шаблон обновлён");
                  loadData();
                }}
                className={`${card} border-cyan-500/40 flex gap-2`}
              >
                <input value={editTpl.category} onChange={e => setEditTpl({ ...editTpl, category: e.target.value })} className={`${inp} w-40`} />
                <input value={editTpl.text} onChange={e => setEditTpl({ ...editTpl, text: e.target.value })} className={inp} />
                <button className={btn}>Сохранить</button>
              </form>
            )}

            <div className={card}>
              {comments.map(c => (
                <div key={c.id} className="flex justify-between items-center py-2 border-b border-slate-800 text-sm">
                  <span><b className="text-cyan-400">[{c.category}]</b> {c.text} <span className="text-xs text-slate-500">(исп: {c.used_count})</span></span>
                  <div className="flex gap-2">
                    <button onClick={() => setEditTpl(c)} className="text-cyan-300 text-xs">Редактировать</button>
                    <button onClick={() => setConfirmDialog({ title: "Удалить шаблон?", onConfirm: async () => { await api(`/api/comments/${c.id}`, { method: "DELETE" }); loadData(); } })} className="text-rose-400 text-xs">Удалить</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 6. ОЧЕРЕДЬ И DLQ */}
        {tab === "queue" && (
          <div className="space-y-4">
            <div className="flex flex-wrap justify-between items-center gap-2">
              <h2 className="text-2xl font-bold">Очередь заданий, Verification и DLQ</h2>
              <div className="flex gap-2">
                <select value={taskFilter.queue_name} onChange={e => setTaskFilter({ ...taskFilter, queue_name: e.target.value })} className={`${inp} w-44`}>
                  <option value="">Все очереди</option>
                  <option value="execution">execution</option>
                  <option value="verification">verification (проверка)</option>
                  <option value="dlq">dlq (Dead Letter Queue)</option>
                </select>
                <select value={taskFilter.status} onChange={e => setTaskFilter({ ...taskFilter, status: e.target.value })} className={`${inp} w-44`}>
                  <option value="">Все статусы</option>
                  <option value="pending">pending</option>
                  <option value="running">running</option>
                  <option value="verification_required">verification_required</option>
                  <option value="completed">completed</option>
                  <option value="dlq">dlq</option>
                </select>
              </div>
            </div>

            {selectedTask && (
              <div className={`${card} border-cyan-500/40 space-y-3`}>
                <div className="flex justify-between">
                  <h3 className="font-bold text-cyan-400">История переходов задания #{selectedTask.task.id} (UID: {selectedTask.task.operation_uid})</h3>
                  <button onClick={() => setSelectedTask(null)} className="text-xs text-slate-400">✕ Закрыть</button>
                </div>
                <div className="space-y-1 text-xs font-mono">
                  {(selectedTask.history || []).map((h: any) => (
                    <div key={h.id} className="text-slate-300">
                      [{h.created_at}] {h.from_status || "init"} → <b className="text-cyan-300">{h.to_status}</b> ({h.worker_id}): {h.reason}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className={`${card} overflow-x-auto`}>
              <table className="w-full text-left text-sm">
                <thead className="text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="py-2">ID / Очередь</th>
                    <th className="py-2">Акк / Видео</th>
                    <th className="py-2">Статус / Внеш. рез.</th>
                    <th className="py-2">Попытки / Время</th>
                    <th className="py-2">Действия</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {tasks.map(t => (
                    <tr key={t.id}>
                      <td className="py-2">#{t.id} <span className="text-xs text-cyan-400">[{t.queue_name}]</span> (P{t.priority})</td>
                      <td className="py-2">Акк #{t.account_id} / Вид #{t.video_id}<div className="text-xs text-slate-400 truncate max-w-xs">{t.comment_text}</div></td>
                      <td className="py-2">
                        <b>{t.status}</b> / <span className="text-xs text-amber-300">{t.external_result}</span>
                        {t.error_message && <div className="text-xs text-rose-400">{t.error_code}: {t.error_message}</div>}
                      </td>
                      <td className="py-2 text-xs">{t.attempts}/{t.max_attempts} ({t.duration_ms ? `${t.duration_ms} мс` : "—"})</td>
                      <td className="py-2 flex flex-wrap gap-1.5">
                        <button
                          onClick={async () => {
                            const r = await api(`/api/tasks/${t.id}`);
                            if (r.ok) setSelectedTask(await r.json());
                          }}
                          className="px-2 py-1 bg-slate-800 rounded text-xs"
                        >
                          История
                        </button>
                        {t.status === "verification_required" && (
                          <>
                            <button
                              onClick={async () => {
                                await api(`/api/tasks/${t.id}/manual-confirm`, { method: "POST", body: JSON.stringify({ confirmed_live: true, reason: "Подтверждено вручную в админке" }) });
                                notify("Задание подтверждено");
                                loadData();
                              }}
                              className="px-2 py-1 bg-emerald-500/20 text-emerald-300 rounded text-xs"
                            >
                              ✓ Подтвердить
                            </button>
                            <button
                              onClick={async () => {
                                await api(`/api/tasks/${t.id}/manual-confirm`, { method: "POST", body: JSON.stringify({ confirmed_live: false, reason: "Отклонено вручную в DLQ" }) });
                                notify("Перемещено в DLQ");
                                loadData();
                              }}
                              className="px-2 py-1 bg-rose-500/20 text-rose-300 rounded text-xs"
                            >
                              ✕ В DLQ
                            </button>
                          </>
                        )}
                        {t.status === "pending" && (
                          <button
                            onClick={async () => {
                              await api(`/api/tasks/${t.id}/cancel`, { method: "POST" });
                              notify("Задание отменено");
                              loadData();
                            }}
                            className="px-2 py-1 bg-rose-500/20 text-rose-300 rounded text-xs"
                          >
                            Отменить
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* 7. ЛОГИ И АУДИТ */}
        {tab === "logs" && (
          <div className="space-y-4">
            <div className="flex flex-wrap justify-between items-center gap-2">
              <h2 className="text-2xl font-bold">Системные логи и Журнал действий администраторов</h2>
              <div className="flex gap-2">
                <input placeholder="Поиск в логах..." value={logFilter.search} onChange={e => setLogFilter({ ...logFilter, search: e.target.value })} className={`${inp} w-48`} />
                <select value={logFilter.level} onChange={e => setLogFilter({ ...logFilter, level: e.target.value })} className={`${inp} w-36`}>
                  <option value="">Все уровни</option>
                  <option value="INFO">INFO</option>
                  <option value="WARNING">WARNING</option>
                  <option value="ERROR">ERROR</option>
                </select>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className={`${card} font-mono text-xs space-y-1.5 max-h-96 overflow-y-auto`}>
                <h3 className="font-bold text-cyan-400 mb-2">Системные логи (с маскированием секретов)</h3>
                {logs.map(L => (
                  <div key={L.id} className="border-b border-slate-800 pb-1">
                    <span className="text-slate-500">{L.created_at}</span> [{L.service}] <b className={L.level === "ERROR" ? "text-rose-400" : "text-emerald-400"}>{L.level}</b> {L.event}: {L.message}
                  </div>
                ))}
              </div>
              <div className={`${card} font-mono text-xs space-y-1.5 max-h-96 overflow-y-auto`}>
                <h3 className="font-bold text-amber-400 mb-2">Журнал аудита администраторов (AuditLog)</h3>
                {auditLogs.map(A => (
                  <div key={A.id} className="border-b border-slate-800 pb-1">
                    <span className="text-slate-500">{A.created_at}</span> <b>{A.username}</b> ({A.ip_address}) → <span className="text-cyan-300">{A.action}</span> [{A.target_type} #{A.target_id || "—"}] {A.details}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* 8. ДИАГНОСТИКА */}
        {tab === "diagnostics" && (
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <h2 className="text-2xl font-bold">Диагностика сервера и интеграций</h2>
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    const r = await api("/api/diagnostics");
                    if (r.ok) { setDiagnostics(await r.json()); notify("Диагностический отчёт сформирован"); }
                  }}
                  className={btn}
                >
                  🩺 Запустить полную проверку
                </button>
                <button
                  onClick={async () => {
                    const r = await api("/api/diagnostics/cleanup", { method: "POST" });
                    if (r.ok) notify("Устаревшие логи и сессии очищены");
                  }}
                  className="px-4 py-2 bg-slate-800 rounded-lg text-sm"
                >
                  🧹 Очистить старые данные
                </button>
              </div>
            </div>
            {diagnostics && (
              <div className={`${card} space-y-2 text-sm`}>
                <div>Время отчёта: <b>{diagnostics.generated_at}</b></div>
                <div>CPU: <b>{diagnostics.metrics.cpu_percent}%</b> | Свободно RAM: <b>{diagnostics.metrics.free_ram_mb} МБ</b> | Свободно на диске: <b>{diagnostics.metrics.free_disk_mb} МБ</b></div>
                <div>Задержка PostgreSQL: <b>{diagnostics.metrics.db_latency_ms} мс</b> | Задержка Redis: <b>{diagnostics.metrics.redis_latency_ms} мс</b></div>
                <div>Ожидающих отправки уведомлений Telegram: <b>{diagnostics.pending_notifications}</b></div>
              </div>
            )}
          </div>
        )}

        {/* 9. РЕЗЕРВНЫЕ КОПИИ */}
        {tab === "backups" && (
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <h2 className="text-2xl font-bold">Резервные копии PostgreSQL</h2>
              <button
                onClick={async () => {
                  const r = await api("/api/backups", { method: "POST" });
                  if (r.ok) { notify("Резервная копия создана и проверена"); loadData(); }
                }}
                className={btn}
              >
                ➕ Создать и проверить бэкап сейчас
              </button>
            </div>
            <div className={card}>
              {backups.map(b => (
                <div key={b.id} className="flex justify-between items-center py-2 border-b border-slate-800 text-sm">
                  <span><b>{b.filename}</b> ({b.size_bytes} байт) — {b.notes}</span>
                  <span className={b.is_verified ? "text-emerald-400 text-xs" : "text-rose-400 text-xs"}>
                    {b.is_verified ? "✓ Проверен (pg_restore OK)" : "⚠ Не проверен"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 10. НАСТРОЙКИ */}
        {tab === "settings" && (
          <div className="space-y-4">
            <h2 className="text-2xl font-bold">Системные пороги и настройки уведомлений</h2>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                await api("/api/settings", { method: "POST", body: JSON.stringify(settingForm) });
                notify("Настройка сохранена");
                loadData();
              }}
              className={`${card} grid grid-cols-1 md:grid-cols-4 gap-3`}
            >
              <input placeholder="Ключ (MIN_FREE_DISK_MB)" value={settingForm.key} onChange={e => setSettingForm({ ...settingForm, key: e.target.value })} className={inp} required />
              <input placeholder="Значение" value={settingForm.value} onChange={e => setSettingForm({ ...settingForm, value: e.target.value })} className={inp} required />
              <input placeholder="Описание" value={settingForm.description} onChange={e => setSettingForm({ ...settingForm, description: e.target.value })} className={inp} />
              <button className={btn}>Сохранить настройку</button>
            </form>
            <div className={card}>
              {settingsList.map(s => (
                <div key={s.key} className="flex justify-between py-2 border-b border-slate-800 text-sm">
                  <span><b>{s.key}</b> = <span className="text-cyan-300">{s.value}</span></span>
                  <span className="text-xs text-slate-400">{s.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 11. АДМИНИСТРАТОРЫ И СЕССИИ */}
        {tab === "security" && (
          <div className="space-y-6">
            <h2 className="text-2xl font-bold">Управление администраторами, паролем и сессиями</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  const r = await api("/api/auth/change-password", { method: "POST", body: JSON.stringify({ ...pwForm, revoke_other_sessions: true }) });
                  const d = await r.json();
                  if (!r.ok) return notify(d.detail || "Ошибка смены пароля", "err");
                  setPwForm({ current_password: "", new_password: "" });
                  notify("Пароль изменён, остальные сессии отозваны");
                }}
                className={`${card} space-y-3`}
              >
                <h3 className="font-bold text-cyan-400 text-sm">Безопасная смена пароля</h3>
                <input type="password" placeholder="Текущий пароль" value={pwForm.current_password} onChange={e => setPwForm({ ...pwForm, current_password: e.target.value })} className={inp} required />
                <input type="password" placeholder="Новый пароль (мин. 8 симв.)" value={pwForm.new_password} onChange={e => setPwForm({ ...pwForm, new_password: e.target.value })} className={inp} required />
                <button className={btn}>Обновить пароль</button>
              </form>

              {user.role === "owner" && (
                <form
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const r = await api("/api/auth/admins", { method: "POST", body: JSON.stringify(adminForm) });
                    const d = await r.json();
                    if (!r.ok) return notify(d.detail || "Ошибка создания пользователя", "err");
                    setAdminForm({ username: "", password: "", role: "admin" });
                    notify("Пользователь добавлен");
                    loadData();
                  }}
                  className={`${card} space-y-3`}
                >
                  <h3 className="font-bold text-cyan-400 text-sm">Добавить администратора или наблюдателя (RBAC)</h3>
                  <input placeholder="Логин" value={adminForm.username} onChange={e => setAdminForm({ ...adminForm, username: e.target.value })} className={inp} required />
                  <input type="password" placeholder="Пароль" value={adminForm.password} onChange={e => setAdminForm({ ...adminForm, password: e.target.value })} className={inp} required />
                  <select value={adminForm.role} onChange={e => setAdminForm({ ...adminForm, role: e.target.value })} className={inp}>
                    <option value="admin">Администратор (admin)</option>
                    <option value="viewer">Наблюдатель (viewer — только чтение)</option>
                  </select>
                  <button className={btn}>Создать пользователя</button>
                </form>
              )}
            </div>

            <div className={card}>
              <div className="flex justify-between items-center mb-3">
                <h3 className="font-bold text-sm">Активные сессии текущего аккаунта ({sessions.length})</h3>
                <button
                  onClick={async () => {
                    await api("/api/auth/revoke-all-sessions", { method: "POST" });
                    setUser(null);
                  }}
                  className="px-3 py-1 bg-rose-500/20 text-rose-300 rounded text-xs"
                >
                  Отозвать все сессии
                </button>
              </div>
              {sessions.map(s => (
                <div key={s.id} className="text-xs py-1.5 border-b border-slate-800 flex justify-between">
                  <span>JTI: {s.jti} | IP: {s.ip_address} | {s.user_agent}</span>
                  <span className="text-slate-400">Истекает: {s.expires_at}</span>
                </div>
              ))}
            </div>

            {admins.length > 0 && (
              <div className={card}>
                <h3 className="font-bold text-sm mb-2">Список пользователей системы</h3>
                {admins.map(u => (
                  <div key={u.id} className="flex justify-between py-1.5 border-b border-slate-800 text-sm">
                    <span><b>{u.username}</b> — роль: <span className="text-cyan-300">{u.role}</span> {u.is_owner && "👑"}</span>
                    {!u.is_owner && user.role === "owner" && (
                      <button onClick={() => setConfirmDialog({ title: `Удалить пользователя ${u.username}?`, onConfirm: async () => { await api(`/api/auth/admins/${u.id}`, { method: "DELETE" }); loadData(); } })} className="text-rose-400 text-xs">Удалить</button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
