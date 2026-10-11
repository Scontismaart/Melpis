import globals from "globals";

export default [{
  files: ["web/**/*.js"],
  ignores: ["web/vendor/**"],
  languageOptions: {
    ecmaVersion: "latest",
    sourceType: "script",
    globals: {
      ...globals.browser,
      Chart: "readonly",
      DOMPurify: "readonly",
      FullCalendar: "readonly",
      AUTH_PARAMS: "readonly",
      AUTH_API_BASE: "readonly",
      mostraErrorePagina: "readonly",
      urlConNext: "readonly",
      collegaGoogle: "readonly",
      vaiADestinazione: "readonly",
      MelpisI18n: "readonly",
      t: "readonly",
      i18next: "readonly",
      getAuthMessage: "readonly",
      showAuthFieldError: "readonly",
      clearAuthFieldError: "readonly",
    },
  },
  rules: { "no-undef": "error", "no-dupe-args": "error", "no-unreachable": "error" },
}, {
  files: ["web/scripts/**/*.mjs"],
  languageOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    globals: globals.node,
  },
  rules: { "no-undef": "error", "no-dupe-args": "error", "no-unreachable": "error" },
}];
