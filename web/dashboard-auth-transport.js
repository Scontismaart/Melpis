(function attachDashboardAuthTransport(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardAuthTransport = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardAuthTransport() {
  "use strict";

  return Object.freeze({
    create(deps) {
      const {
        API_BASE, fetch: fetchRequest, document, storage,
        onNetworkError, onNetworkOk, invalidateSession, redirectToLogin,
        showMfaRequired, openSecurity,
      } = deps;

      function leggiCookie(nome) {
        return document.cookie
          .split(";")
          .map((part) => part.trim())
          .find((part) => part.startsWith(`${nome}=`))
          ?.slice(nome.length + 1) || "";
      }

      function csrfToken() {
        return decodeURIComponent(leggiCookie("__Host-wa_csrf") || leggiCookie("wa_csrf"));
      }

      async function tentaRefresh() {
        try {
          return await fetchRequest(`${API_BASE}/api/auth/refresh`, {
            method: "POST",
            headers: csrfToken() ? { "X-CSRF-Token": csrfToken() } : {},
            credentials: "include",
          });
        } catch (err) {
          onNetworkError();
          throw err;
        }
      }

      async function apiFetch(url, options = {}) {
        const method = (options.method || "GET").toUpperCase();
        const safeMethod = ["GET", "HEAD", "OPTIONS"].includes(method);
        const headers = { ...(options.headers || {}) };
        const selectedOrg = storage.getItem("melpis_selected_organization");
        if (selectedOrg) headers["X-Organization-Id"] = selectedOrg;
        async function tenta() {
          if (!safeMethod) {
            const token = csrfToken();
            if (token) headers["X-CSRF-Token"] = token;
            else delete headers["X-CSRF-Token"];
          }
          try {
            return await fetchRequest(url, { ...options, headers, credentials: "include" });
          } catch (err) {
            onNetworkError();
            throw err;
          }
        }
        let res = await tenta();
        if (res.status === 401 && !url.includes("/api/auth/")) {
          try {
            const refreshRes = await tentaRefresh();
            if (refreshRes.ok) res = await tenta();
          } catch (err) {
            invalidateSession();
            redirectToLogin();
            throw err;
          }
          if (res.status === 401) {
            invalidateSession();
            redirectToLogin();
          }
        }
        if (res.status === 403) {
          const mfaHeader = res.headers.get("X-MFA-Required");
          let isMfa = mfaHeader === "true";
          if (!isMfa) {
            try {
              const cloned = res.clone();
              const data = await cloned.json();
              if (
                data &&
                typeof data.detail === "string" &&
                (data.detail.includes("MFA") || data.detail.includes("due fattori"))
              ) {
                isMfa = true;
              }
            } catch (_) {}
          }
          if (isMfa) {
            res.mfaRequired = true;
            showMfaRequired(() => openSecurity());
          }
        }
        onNetworkOk();
        return res;
      }

      return Object.freeze({ apiFetch, tentaRefresh });
    },
  });
});
