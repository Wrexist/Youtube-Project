# Only used by `docker compose --profile full up`. For development, run
# `npm run dev` on the host — hot reload is worth more than parity here.
FROM node:22-alpine AS deps
WORKDIR /repo
# The lockfile covers the whole workspace, so the manifests all have to be
# present before install or npm resolves a different tree than CI did.
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
COPY packages/contracts/package.json packages/contracts/
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY . .
# Baked in at build time: Next inlines NEXT_PUBLIC_* into the client bundle, so
# changing it later needs a rebuild, not a restart.
ARG NEXT_PUBLIC_ENGINE_URL=http://localhost:8080
ENV NEXT_PUBLIC_ENGINE_URL=$NEXT_PUBLIC_ENGINE_URL
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /repo
ENV NODE_ENV=production
COPY --from=build /repo/node_modules ./node_modules
COPY --from=build /repo/package.json ./package.json
COPY --from=build /repo/apps/web ./apps/web
COPY --from=build /repo/packages ./packages
EXPOSE 3000
CMD ["npm", "run", "start", "--workspace=@studio/web"]
