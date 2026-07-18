# Stage 1: Build React frontend
FROM node:18-alpine AS builder
WORKDIR /app

# Install dependencies using legacy-peer-deps to avoid React 19 vs react-scripts conflicts
COPY package*.json ./
RUN npm ci --legacy-peer-deps

# Copy sources and build the production bundle
COPY . .
ARG REACT_APP_API_BASE_URL
ENV REACT_APP_API_BASE_URL=${REACT_APP_API_BASE_URL}
RUN npm run build

# Stage 2: Serve using Nginx
FROM nginx:stable-alpine
COPY --from=builder /app/build /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
