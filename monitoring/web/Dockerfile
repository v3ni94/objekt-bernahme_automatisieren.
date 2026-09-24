FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html app.js style.css /usr/share/nginx/html/
COPY vendor /usr/share/nginx/html/vendor
COPY assets /usr/share/nginx/html/assets
