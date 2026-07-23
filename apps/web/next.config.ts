import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  env: {
    ENGINE_URL: process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8080",
  },
};

export default config;
