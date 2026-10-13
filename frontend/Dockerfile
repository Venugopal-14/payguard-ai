FROM node:20-slim AS base
WORKDIR /app

# Install dependencies
FROM base AS deps
COPY package.json package-lock.json* ./
RUN npm install

# Build the app
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# NEXT_PUBLIC_* values are inlined at build time, so the backend URL must be
# available during `next build` (not just at runtime). Render automatically
# translates the NEXT_PUBLIC_API_URL env var into a Docker build ARG; this
# declaration brings it into scope for the build stage.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN npm run build

# Run the standalone server
FROM base AS runner
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# Next.js standalone server reads HOSTNAME and PORT. Default HOSTNAME is
# localhost, which rejects external traffic on Render and causes a 502. Bind to
# 0.0.0.0 and default the port to 3000 (Render overrides PORT at runtime, e.g.
# 10000; the server honours it automatically).
ENV HOSTNAME="0.0.0.0"
ENV PORT=3000
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
