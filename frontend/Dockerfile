FROM node:20 AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginx:stable-alpine
COPY --from=builder /app/dist /opt/frontend-dist
COPY nginx-default.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/10-sync-frontend-assets.sh /docker-entrypoint.d/10-sync-frontend-assets.sh
RUN chmod +x /docker-entrypoint.d/10-sync-frontend-assets.sh
EXPOSE 80
