FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginx:stable-alpine
COPY --from=build /app/dist /opt/frontend-dist
COPY nginx-default.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/10-sync-frontend-assets.sh /docker-entrypoint.d/10-sync-frontend-assets.sh
RUN chmod +x /docker-entrypoint.d/10-sync-frontend-assets.sh
EXPOSE 3000
