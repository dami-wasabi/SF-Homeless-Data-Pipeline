module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } },
  settings: { react: { version: "detect" } },
  plugins: ["react"],
  extends: ["eslint:recommended", "plugin:react/recommended", "plugin:react/jsx-runtime"],
  rules: {
    "react/prop-types": "off",
  },
};
