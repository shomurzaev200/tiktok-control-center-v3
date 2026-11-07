FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

RUN apk add --no-cache openssl \
    && mkdir -p /etc/nginx/certs \
    && openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
       -keyout /etc/nginx/certs/privkey.pem \
       -out /etc/nginx/certs/fullchain.pem \
       -subj "/CN=localhost"

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80 443
CMD ["nginx", "-g", "daemon off;"]
