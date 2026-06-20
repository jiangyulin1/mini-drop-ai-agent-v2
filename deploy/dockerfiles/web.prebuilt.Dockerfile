# Mini-Drop Web runtime image for offline or mirror-limited environments.
# Build web/dist locally first with: npm --prefix web run build
FROM nginx:1.27-alpine

COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY web/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
