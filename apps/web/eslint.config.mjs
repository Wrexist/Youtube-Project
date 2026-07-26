import next from "eslint-config-next";

/**
 * Next 16 removed `next lint`, which is why `npm run lint` had been failing with
 * "Invalid project directory: .../apps/web/lint" — it was parsing `lint` as a path.
 * CI never ran the script, so the web app went unlinted. This restores it.
 */
const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "tsconfig.tsbuildinfo"],
  },
  // eslint-config-next 16 default-exports a flat-config *array*, not a factory.
  ...next,
];

export default config;
